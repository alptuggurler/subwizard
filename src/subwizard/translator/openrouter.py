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
