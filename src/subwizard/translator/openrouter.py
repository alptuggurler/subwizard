"""OpenRouter translator (https://openrouter.ai). Qt-free.

One prepaid balance in USD (a few dollars, by card) gives access to many models, Gemini
among them, without Google's billing setup. Same prompts as the Gemini engine (system
prompt with the game profile, context lines, speaker), so quality and the glossary carry
over; only the transport differs.

Checked against openrouter.ai/docs on 2026-11-18:
- POST /api/v1/chat/completions, `Authorization: Bearer <key>`.
- `usage.cost` (USD credits) is in every reply; no flag needed. It is what was charged,
  so it is used as the cost instead of a local price table.
- `reasoning: {"enabled": false}` turns thinking off (faster, cheaper for one line).
- Errors: {"error": {"code", "message", "metadata"}}, sometimes inside an HTTP 200.
  402 = out of credits, 401 = bad key, 403 = moderation or the key's own credit limit,
  429 = rate limited, 408 / 502 / 503 = model or provider trouble.

Only the standard library is used (urllib), like the Ollama engine.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from subwizard import APP_NAME
from subwizard.profiles import GameProfile
from subwizard.translator.base import (
    BaseTranslator,
    TranslationError,
    TranslationRequest,
    TranslationResult,
    brief,
)
from subwizard.translator.prompt import clean_reply, system_prompt, user_prompt
from subwizard.utils import timing

log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
APP_URL = "https://github.com/alptuggurler/subwizard"  # attribution headers (optional)
MAX_OUTPUT_TOKENS = 512  # one subtitle line
RETRYABLE_CODES = (408, 500, 502, 503, 504)
REASONS = {401: "bad_key", 402: "no_credit"}


class OpenRouterTranslator(BaseTranslator):
    name = "openrouter"

    def __init__(self, api_keys: list[str], *, model: str, timeout_s: float, retries: int,
                 prompt: str = "", profile: GameProfile | None = None,
                 url: str = API_URL) -> None:  # fmt: skip
        if not api_keys:
            raise TranslationError("no_api_key")
        self._key = api_keys[0]  # one balance per account: a second key adds nothing
        self.model = model
        self._timeout_s = timeout_s
        self._retries = retries
        self._system = system_prompt(prompt, profile)
        self._url = url

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
                "HTTP-Referer": APP_URL,
                "X-Title": APP_NAME,
            },
        )  # fmt: skip
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310 - fixed https URL
            return json.loads(response.read().decode("utf-8"))

    def translate(self, request: TranslationRequest) -> TranslationResult:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system},
                {"role": "user", "content": user_prompt(request)},
            ],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "reasoning": {"enabled": False},
        }
        started = timing.now()
        attempts_left = self._retries + 1
        while True:
            attempts_left -= 1
            try:
                reply = self._post(body)
                error = reply.get("error")
                if error:  # an error can come back with HTTP 200
                    code = int(error.get("code") or 0)
                    raise _api_error(code, error) if code else TranslationError(
                        f"api_error ({str(error.get('message', ''))[:200]})")
                return self._result(reply, timing.ms_since(started))
            except urllib.error.HTTPError as exc:
                code = exc.code
                error = _error_body(exc)
                if code in RETRYABLE_CODES and attempts_left > 0:
                    log.warning("OpenRouter hatası (%s), tekrar deneniyor.", code)
                    continue
                raise _api_error(code, error) from exc
            except TimeoutError as exc:
                raise TranslationError("timeout") from exc  # SPEC: no retry on timeout
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, TimeoutError):
                    raise TranslationError("timeout") from exc
                if attempts_left > 0:
                    continue
                raise TranslationError(f"network ({brief(exc)})") from exc
            except (OSError, ValueError) as exc:  # connection reset, bad JSON
                if attempts_left > 0:
                    continue
                raise TranslationError(f"api_error ({brief(exc)})") from exc

    def _result(self, reply: dict[str, Any], latency_ms: float) -> TranslationResult:
        choices = reply.get("choices") or [{}]
        text = clean_reply(str((choices[0].get("message") or {}).get("content") or ""))
        if not text:
            log.warning("OpenRouter boş yanıt döndü (finish=%s)", choices[0].get("finish_reason"))
            raise TranslationError("empty")
        usage = reply.get("usage") or {}
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        return TranslationResult(
            text, self.name, latency_ms,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=cached,
            cost_usd=float(usage.get("cost") or 0.0),
        )  # fmt: skip


# --- account: what the key may still spend, and what a top-up buys --------------------
# GET /api/v1/key works with a normal key (the account balance, /credits, needs a
# management key). Free-model limits from openrouter.ai/docs/api-reference/limits.
KEY_URL = "https://openrouter.ai/api/v1/key"
FREE_THRESHOLD_USD = 10.0  # all-time purchases that raise the free-model daily cap
FREE_DAILY_LOW, FREE_DAILY_HIGH = 50, 1000
# Rough planning numbers: a busy dialogue scene sends this many new lines an hour (cache
# hits are free), each with the system prompt and context lines.
LINES_PER_HOUR = 300
TYPICAL_LINE_TOKENS = (600, 30)  # input, output
# USD per 1M tokens (input, output) from openrouter.ai/models, 2026-11-18; only for
# the estimate (the real charge comes with each reply).
PRICES = {
    "google/gemini-2.5-flash-lite": (0.10, 0.40),
    "google/gemini-2.5-flash": (0.30, 2.50),
}
TOP_UPS_USD = (5, 10, 20)


@dataclass(frozen=True)
class KeyInfo:
    limit: float | None  # the key's own spending limit (USD), None = no limit
    remaining: float | None
    reset: str | None  # "daily" / "weekly" / "monthly" / None
    usage: float  # spent with this key, all time
    usage_daily: float  # spent today (UTC)
    free_used: int  # ":free" model requests today
    free_limit: int


def fetch_key_info(key: str, timeout_s: float = 5.0, url: str = KEY_URL) -> KeyInfo:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed https URL
            data = json.loads(response.read().decode("utf-8")).get("data") or {}
    except urllib.error.HTTPError as exc:
        raise _api_error(exc.code, _error_body(exc)) from exc
    except (OSError, ValueError) as exc:  # offline, timeout, bad JSON
        raise TranslationError(f"network ({brief(exc)})") from exc
    free = data.get("free_model_daily_requests") or {}

    def number(name: str) -> float | None:
        value = data.get(name)
        return None if value is None else float(value)

    return KeyInfo(
        limit=number("limit"), remaining=number("limit_remaining"),
        reset=data.get("limit_reset"), usage=number("usage") or 0.0,
        usage_daily=number("usage_daily") or 0.0,
        free_used=int(free.get("used") or 0), free_limit=int(free.get("limit") or 0),
    )  # fmt: skip


def cost_per_hour(model: str, line_tokens: tuple[float, float] = TYPICAL_LINE_TOKENS,
                  lines_per_hour: int = LINES_PER_HOUR) -> float | None:  # fmt: skip
    """Estimated USD an hour of busy dialogue costs with `model`; None if its price is
    unknown. `line_tokens` can be the average measured so far (the spend ledger)."""
    price = PRICES.get(model)
    if price is None:
        return None
    per_line = (line_tokens[0] * price[0] + line_tokens[1] * price[1]) / 1_000_000
    return per_line * lines_per_hour


def hours_for(usd: float, per_hour: float | None) -> float | None:
    return None if not per_hour else usd / per_hour


def _error_body(exc: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        data = json.loads(exc.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    error = data.get("error") if isinstance(data, dict) else None
    return error if isinstance(error, dict) else {}


def _api_error(code: int, error: dict[str, Any]) -> TranslationError:
    """A reason the service and the Test button understand. 429 and "out of credits"
    start with "quota" so the Home card shows the quota notice and the fallback takes over."""
    message = str(error.get("message") or "")[:200]
    metadata = error.get("metadata") or {}
    if code == 429:
        return TranslationError(f"quota (rate_limited: {message})")
    if code == 402:
        return TranslationError(f"quota (no_credit: {message})")
    if code == 403 and isinstance(metadata, dict) and metadata.get("limit_source"):
        return TranslationError(f"quota (key_limit: {message})")
    reason = REASONS.get(code, f"api_error:{code}")
    return TranslationError(f"{reason} ({message})" if message else reason)
