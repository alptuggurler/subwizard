"""Claude API engine: request shape (Gemini's prompts, thinking off, cached system
prompt), token and cost accounting, error mapping, own keys. No network: the SDK client
is replaced."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from subwizard.config import AppConfig
from subwizard.core import secrets
from subwizard.core.metrics import MetricsHub
from subwizard.profiles import GameProfile
from subwizard.translator.anthropic_api import AnthropicTranslator, status_error
from subwizard.translator.base import TranslationError, TranslationRequest
from subwizard.translator.cache import TranslationCache
from subwizard.translator.service import TranslationService, build_engines
from subwizard.translator.spend import SpendLedger

KEY = "sk-ant-api03-" + "a" * 40


def reply(text="  \"Hadi ama dostum.\" ", cached=0, created=0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=100, output_tokens=12,
                              cache_read_input_tokens=cached,
                              cache_creation_input_tokens=created),
    )  # fmt: skip


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes, self.calls, self.closed = list(outcomes), [], False
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


def engine(client, **kw):
    return AnthropicTranslator([KEY], model="claude-haiku-5-5", timeout_s=2.5, retries=1,
                               client_factory=lambda key, timeout: client, **kw)  # fmt: skip


def status(cls, code, message):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "x", "message": message}}
    return cls(message, response=httpx2.Response(code, request=request), body=body)


def test_request_uses_gemini_prompts_without_thinking_and_counts_cached_tokens():
    client = FakeClient(reply(cached=600, created=0))
    profile = GameProfile(glossary={"Vice City": "keep"})
    result = engine(client, profile=profile).translate(
        TranslationRequest("Come on, man.", context=(("Hi.", "Selam."),), speaker="Jason"))
    assert result.text == "Hadi ama dostum." and result.engine == "anthropic"
    assert (result.input_tokens, result.output_tokens, result.cached_tokens) == (700, 12, 600)
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["thinking"] == {"type": "disabled"}
    system = call["system"][0]
    assert "Vice City" in system["text"] and system["cache_control"] == {"type": "ephemeral"}
    user = call["messages"][0]["content"]
    assert "Konuşan: Jason" in user and "EN: Hi.\nTR: Selam." in user


def test_errors_become_reasons_and_overload_is_retried_once():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    client = FakeClient(status(anthropic.InternalServerError, 529, "Overloaded"), reply())
    assert engine(client).translate(TranslationRequest("Hi")).text == "Hadi ama dostum."
    for error, reason in [
        (status(anthropic.RateLimitError, 429, "slow down"), "quota (rate_limited"),
        (status(anthropic.BadRequestError, 400, "Your credit balance is too low"),
         "quota (no_credit"),
        (status(anthropic.AuthenticationError, 401, "invalid x-api-key"), "bad_key"),
        (anthropic.APITimeoutError(request=request), "timeout"),
    ]:  # fmt: skip
        with pytest.raises(TranslationError) as raised:
            engine(FakeClient(error)).translate(TranslationRequest("Hi"))
        assert str(raised.value).startswith(reason)
    assert str(status_error(402, "pay")).startswith("quota (no_credit")
    with pytest.raises(TranslationError, match="empty"):
        engine(FakeClient(reply(text=""))).translate(TranslationRequest("Hi"))


def test_build_engines_uses_claude_keys(monkeypatch):
    monkeypatch.setattr("subwizard.translator.service.load_api_keys",
                        lambda p="gemini": ([KEY], "test") if p == "anthropic" else ([], ""))  # fmt: skip
    warmed = []
    monkeypatch.setattr(AnthropicTranslator, "warm", lambda self: warmed.append(self))
    config = AppConfig()
    config.translator.engine = "anthropic"
    primary, fallback, note = build_engines(config)
    assert primary.name == "anthropic" and primary.model == "claude-haiku-5-5"
    assert fallback.name == "deep" and note == ""
    assert warmed == [primary]  # the connection is opened at start
    primary.close()
    assert secrets.provider_for("anthropic") == "anthropic"


def test_warm_opens_connection_without_tokens():
    client = FakeClient()
    looked_up = []
    client.models = SimpleNamespace(retrieve=looked_up.append)
    engine(client).warm().join(2)
    assert looked_up == ["claude-haiku-5-5"] and client.calls == []  # no message sent


def test_warm_failure_is_only_logged():
    client = FakeClient()

    def fail(_model):
        raise RuntimeError("offline")

    client.models = SimpleNamespace(retrieve=fail)
    engine(client).warm().join(2)  # no exception


def test_cost_from_the_price_table_and_the_daily_limit(tmp_path):
    config = AppConfig()
    config.translator.engine = "anthropic"
    config.translator.daily_limit_usd = 0.00004  # two lines' worth
    ledger = SpendLedger(tmp_path / "usage.sqlite")
    client = FakeClient(reply(cached=600), reply(cached=600))
    svc = TranslationService(config, MetricsHub(), engine(client), None, TranslationCache(100),
                             ledger=ledger)  # fmt: skip
    first = svc.translate(TranslationRequest("One."))
    # 100 new input × $0.10 + 600 cached × $0.01 + 12 output × $0.50, per 1M tokens
    assert first.cost_usd == pytest.approx((100 * 0.10 + 600 * 0.01 + 12 * 0.50) / 1e6)
    assert not svc.at_limit()
    svc.translate(TranslationRequest("Two."))
    assert svc.at_limit() and ledger.today().cost_usd == pytest.approx(2 * first.cost_usd)
    svc.close()
