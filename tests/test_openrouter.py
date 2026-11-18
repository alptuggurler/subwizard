"""OpenRouter engine: request shape, the charged cost, error mapping, its own keys, and
its place in the service (daily limit, fallback). No network: the HTTP call is replaced."""

import io
import json
import urllib.error

import pytest

from subwizard.config import AppConfig
from subwizard.core import secrets
from subwizard.core.metrics import MetricsHub
from subwizard.profiles import GameProfile
from subwizard.translator import openrouter
from subwizard.translator.base import TranslationError, TranslationRequest, TranslationResult
from subwizard.translator.cache import TranslationCache
from subwizard.translator.openrouter import OpenRouterTranslator
from subwizard.translator.service import TranslationService, build_engines
from subwizard.translator.spend import SpendLedger

KEY = "sk-or-v1-" + "a" * 64


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(*replies, sent=None):
    """Each call takes the next reply: a dict (JSON body) or an exception to raise."""
    queue = list(replies)

    def urlopen(request, timeout):
        if sent is not None:
            sent.append((request, json.loads(request.data), timeout))
        reply = queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return FakeResponse(json.dumps(reply).encode())

    return urlopen


def http_error(code, message="", metadata=None):
    body = {"error": {"code": code, "message": message, "metadata": metadata or {}}}
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps(body).encode()))


OK = {"choices": [{"message": {"content": "  \"Hadi ama dostum.\"  "}, "finish_reason": "stop"}],
      "usage": {"prompt_tokens": 300, "completion_tokens": 9, "cost": 0.0000336,
                "prompt_tokens_details": {"cached_tokens": 200}}}  # fmt: skip


def engine(**kw):
    return OpenRouterTranslator([KEY], model="google/gemini-2.5-flash-lite", timeout_s=2.5,
                                retries=1, **kw)  # fmt: skip


def test_request_carries_gemini_prompts_and_reply_has_the_charged_cost(monkeypatch):
    sent = []
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", fake_urlopen(OK, sent=sent))
    profile = GameProfile(glossary={"Vice City": "keep"})
    result = engine(profile=profile).translate(
        TranslationRequest("Come on, man.", context=(("Hi.", "Selam."),), speaker="Jason"))
    assert result.text == "Hadi ama dostum." and result.engine == "openrouter"
    assert (result.input_tokens, result.output_tokens, result.cached_tokens) == (300, 9, 200)
    assert result.cost_usd == pytest.approx(0.0000336)
    request, body, timeout = sent[0]
    assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.get_header("Authorization") == f"Bearer {KEY}" and timeout == 2.5
    assert body["model"] == "google/gemini-2.5-flash-lite"
    assert body["reasoning"] == {"enabled": False}
    system, user = body["messages"]
    assert system["role"] == "system" and "Vice City" in system["content"]
    assert "Konuşan: Jason" in user["content"] and "EN: Hi.\nTR: Selam." in user["content"]


@pytest.mark.parametrize(("error", "reason"), [
    (http_error(402, "Insufficient credits"), "quota (no_credit"),
    (http_error(429, "slow down"), "quota (rate_limited"),
    (http_error(403, "Key limit exceeded", {"limit_source": "openrouter_key_limit"}),
     "quota (key_limit"),
    (http_error(401, "Invalid API key"), "bad_key"),
    (http_error(403, "flagged", {"reasons": ["x"]}), "api_error:403"),
    (urllib.error.URLError(TimeoutError()), "timeout"),
    (TimeoutError(), "timeout"),
])  # fmt: skip
def test_errors_become_reasons(monkeypatch, error, reason):
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", fake_urlopen(error))
    with pytest.raises(TranslationError) as raised:
        engine().translate(TranslationRequest("Hi"))
    assert str(raised.value).startswith(reason)


def test_server_error_is_retried_once_and_error_inside_200_is_caught(monkeypatch):
    monkeypatch.setattr(openrouter.urllib.request, "urlopen",
                        fake_urlopen(http_error(502, "down"), OK))  # fmt: skip
    assert engine().translate(TranslationRequest("Hi")).text == "Hadi ama dostum."
    monkeypatch.setattr(openrouter.urllib.request, "urlopen",
                        fake_urlopen({"error": {"code": 402, "message": "no money"}}))  # fmt: skip
    with pytest.raises(TranslationError, match="no_credit"):
        engine().translate(TranslationRequest("Hi"))
    empty = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    monkeypatch.setattr(openrouter.urllib.request, "urlopen", fake_urlopen(empty))
    with pytest.raises(TranslationError, match="empty"):
        engine().translate(TranslationRequest("Hi"))


def test_keys_are_stored_apart_from_gemini(monkeypatch):
    store = {}
    import keyring

    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__(u, p))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get(u))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.setattr(secrets, "env_file_paths", lambda: [])
    secrets.save_api_keys([KEY], secrets.OPENROUTER)
    assert secrets.load_api_keys(secrets.OPENROUTER) == ([KEY], "keyring")
    assert "gemini_api_keys" not in store
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-env")
    assert secrets.load_api_keys(secrets.OPENROUTER) == (["sk-or-v1-env"], "env")
    assert secrets.provider_for("openrouter") == "openrouter"
    assert secrets.provider_for("deep") == "gemini"


def test_build_engines_uses_openrouter_keys(monkeypatch):
    asked = []

    def keys(provider="gemini"):
        asked.append(provider)
        return ([KEY], "test") if provider == "openrouter" else ([], "")

    monkeypatch.setattr("subwizard.translator.service.load_api_keys", keys)
    config = AppConfig()
    config.translator.engine = "openrouter"
    primary, fallback, note = build_engines(config)
    assert primary.name == "openrouter" and primary.model == "google/gemini-2.5-flash-lite"
    assert fallback.name == "deep" and note == "" and asked == ["openrouter"]

    monkeypatch.setattr("subwizard.translator.service.load_api_keys", lambda p="gemini": ([], ""))
    primary, fallback, note = build_engines(config)
    assert primary is None and note == "no_api_key"


class Fake:
    def __init__(self, name, cost=0.0):
        self.name, self.cost, self.calls = name, cost, 0

    def translate(self, request):
        self.calls += 1
        return TranslationResult(f"{self.name}<{request.text}>", self.name, 10.0,
                                 input_tokens=500, output_tokens=50, cost_usd=self.cost)  # fmt: skip

    def close(self):
        pass


def test_charged_cost_is_counted_and_the_daily_limit_applies(tmp_path):
    config = AppConfig()
    config.translator.engine = "openrouter"
    config.translator.daily_limit_usd = 0.05
    ledger = SpendLedger(tmp_path / "usage.sqlite")
    paid, deep = Fake("openrouter", cost=0.03), Fake("deep")
    svc = TranslationService(config, MetricsHub(), paid, deep, TranslationCache(100),
                             ledger=ledger)  # fmt: skip
    first = svc.translate(TranslationRequest("One."))
    assert first.cost_usd == pytest.approx(0.03) and not first.fallback
    svc.translate(TranslationRequest("Two."))
    assert ledger.today().cost_usd == pytest.approx(0.06)
    third = svc.translate(TranslationRequest("Three."))  # over $0.05: the fallback today
    assert third.engine == "deep" and third.fallback and paid.calls == 2
    svc.close()
