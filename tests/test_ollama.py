"""Local model through Ollama: request shape, reply cleanup, error mapping, and its place
as Gemini's first fallback. No real Ollama: the HTTP call is replaced."""

import io
import json
import urllib.error

import pytest

from subwizard.config import AppConfig
from subwizard.core import secrets
from subwizard.core.metrics import MetricsHub
from subwizard.profiles import GameProfile
from subwizard.translator import ollama
from subwizard.translator.base import TranslationError, TranslationRequest, TranslationResult
from subwizard.translator.cache import CachedTranslation, TranslationCache, cache_key
from subwizard.translator.ollama import FallbackChain, OllamaTranslator, build_prompt, clean_reply
from subwizard.translator.service import TranslationService, build_engines


@pytest.fixture(autouse=True)
def no_warm(monkeypatch):
    """Never load a real model in tests."""
    monkeypatch.setattr(OllamaTranslator, "warm", lambda self: None)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(reply=None, error=None, sent=None):
    def urlopen(request, timeout):
        if sent is not None:
            sent.append((request.full_url, json.loads(request.data), timeout))
        if error is not None:
            raise error
        return FakeResponse(json.dumps(reply).encode())

    return urlopen


def test_request_and_reply(monkeypatch):
    sent = []
    reply = {"message": {"content": "  \"Hadi ama dostum.\"\n"}, "prompt_eval_count": 60,
             "eval_count": 9}  # fmt: skip
    monkeypatch.setattr(ollama.urllib.request, "urlopen", fake_urlopen(reply, sent=sent))
    local = OllamaTranslator("translategemma:4b", "http://127.0.0.1:11434/", timeout_s=3.0)
    result = local.translate(TranslationRequest("Come on, man.", speaker="Jason"))
    assert result.text == "Hadi ama dostum." and result.engine == "ollama"
    assert (result.input_tokens, result.output_tokens) == (60, 9)
    url, body, timeout = sent[0]
    assert url == "http://127.0.0.1:11434/api/chat" and timeout == 3.0
    assert body["model"] == "translategemma:4b" and body["stream"] is False
    prompt = body["messages"][0]["content"]
    assert prompt.endswith("into Turkish:\n\n\nCome on, man.")
    assert "English (en) to Turkish (tr)" in prompt and "Jason" not in prompt  # no name


def test_prompt_keeps_profile_terms_and_reply_drops_reasoning():
    profile = GameProfile(glossary={"Vice City": "keep"})
    assert "Keep these names exactly as written: Vice City." in build_prompt("x", "en", "tr", profile)
    assert clean_reply("<think>hmm</think>\nMerhaba.") == "Merhaba."


@pytest.mark.parametrize(("error", "reason"), [
    (urllib.error.URLError(ConnectionRefusedError(61, "refused")), "ollama_unreachable"),
    (urllib.error.URLError(TimeoutError()), "timeout"),
    (TimeoutError(), "timeout"),
    (urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b"model not found")),
     "model_not_found (translategemma:4b)"),
])  # fmt: skip
def test_errors_become_reasons(monkeypatch, error, reason):
    monkeypatch.setattr(ollama.urllib.request, "urlopen", fake_urlopen(error=error))
    with pytest.raises(TranslationError) as raised:
        OllamaTranslator("translategemma:4b").translate(TranslationRequest("Hi"))
    assert str(raised.value).startswith(reason)


class Fake:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.calls = name, fail, 0

    def translate(self, request):
        self.calls += 1
        if self.fail:
            raise TranslationError("nope")
        return TranslationResult(f"{self.name}<{request.text}>", self.name, 10.0,
                                 input_tokens=500, output_tokens=50)  # fmt: skip

    def close(self):
        pass


def test_chain_tries_ollama_before_google():
    local, deep = Fake("ollama", fail=True), Fake("deep")
    assert FallbackChain([local, deep]).translate(TranslationRequest("Hi")).engine == "deep"
    assert local.calls == 1
    with pytest.raises(TranslationError, match="ollama: nope; deep: nope"):
        FallbackChain([Fake("ollama", True), Fake("deep", True)]).translate(TranslationRequest("x"))


def test_build_engines_puts_the_local_model_first(monkeypatch):
    monkeypatch.setattr(secrets, "load_api_keys", lambda: (["AIza" + "x" * 35], "test"))
    monkeypatch.setattr("subwizard.translator.service.load_api_keys",
                        lambda: (["AIza" + "x" * 35], "test"))  # fmt: skip
    config = AppConfig()
    config.translator.local_fallback = True
    primary, fallback, _ = build_engines(config)
    assert primary.name == "gemini"
    assert [e.name for e in fallback.engines] == ["ollama", "deep"]

    config.translator.engine = "ollama"
    primary, fallback, _ = build_engines(config)
    assert primary.name == "ollama" and fallback.name == "deep"


def test_local_answers_are_free_marked_and_retried_when_gemini_is_back():
    config = AppConfig()
    svc = TranslationService(config, MetricsHub(), Fake("gemini", fail=True), Fake("ollama"),
                             TranslationCache(100))  # fmt: skip
    result = svc.translate(TranslationRequest("Run!"))
    assert result.engine == "ollama" and result.fallback and result.cost_usd == 0.0
    # Cached as a fallback answer: Gemini gets another chance next time.
    gemini = Fake("gemini")
    again = TranslationService(config, MetricsHub(), gemini, None, svc.cache)
    assert again.translate(TranslationRequest("Run!")).engine == "gemini" and gemini.calls == 1


def test_ollama_as_the_engine_costs_nothing_and_is_not_a_fallback():
    config = AppConfig()
    config.translator.engine = "ollama"
    cache = TranslationCache(100)
    svc = TranslationService(config, MetricsHub(), Fake("ollama"), None, cache)
    result = svc.translate(TranslationRequest("Run!"))
    assert not result.fallback and result.cost_usd == 0.0 and svc.usage.cost_usd == 0.0
    cache.put(cache_key("Go!", "tr"), CachedTranslation("Git!", "ollama"))
    hit = svc.translate(TranslationRequest("Go!"))
    assert hit.cached and not hit.fallback
