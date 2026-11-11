"""M3: subtitle state machine, translation service, cache, keys, worker. Qt-free."""

import threading
import time

import pytest

from subwizard.config import AppConfig, SubtitleConfig, load_config
from subwizard.core import secrets
from subwizard.core.metrics import M, MetricsHub
from subwizard.pipeline.board import SubtitleBoard
from subwizard.pipeline.subtitle_state import (
    LineCleared,
    LineStable,
    State,
    SubtitleLine,
    SubtitleStateMachine,
    extends,
)
from subwizard.pipeline.translate_worker import TranslateWorker
from subwizard.translator.base import (
    BaseTranslator,
    TranslationError,
    TranslationRequest,
    TranslationResult,
)
from subwizard.translator.cache import CachedTranslation, TranslationCache, cache_key
from subwizard.translator.deep import DeepTranslatorFallback
from subwizard.translator.gemini import GeminiTranslator, is_timeout
from subwizard.translator.prompt import DEFAULT_SYSTEM_PROMPT, clean_reply, user_prompt
from subwizard.translator.service import TranslationService, build_engines
from subwizard.translator.test_run import run_test


# --- state machine ----------------------------------------------------------------
def machine(**overrides) -> SubtitleStateMachine:
    return SubtitleStateMachine(SubtitleConfig(**overrides))


def feed_all(sm, texts, start=0.0, step=0.25):
    events = []
    for i, text in enumerate(texts):
        events += sm.feed(text, start + i * step)
    return events


def stable_texts(events):
    return [e.line.full_text for e in events if isinstance(e, LineStable)]


def test_line_becomes_stable_after_being_seen_twice():
    sm = machine()
    assert sm.feed("Where did you park the car?", 1.0) == []
    assert sm.state == State.CANDIDATE
    (event,) = sm.feed("Where did you park the car?", 1.25)
    assert isinstance(event, LineStable)
    assert event.line.text == "Where did you park the car?"
    assert event.line.seen_at == 1.0  # latency is measured from the first sighting


def test_same_line_is_reported_once_even_with_ocr_noise():
    sm = machine()
    texts = ["We need to get out of here."] * 2 + [
        "We need to get out of here,", "We need t0 get out of here.", "We need to get out of here."
    ] * 5
    assert stable_texts(feed_all(sm, texts)) == ["We need to get out of here."]


def test_a_different_line_replaces_the_stable_one():
    sm = machine()
    events = feed_all(sm, ["First line here."] * 3 + ["Second line, totally new."] * 2)
    assert stable_texts(events) == ["First line here.", "Second line, totally new."]
    assert [e.line.replaces for e in events if isinstance(e, LineStable)] == [None, None]


def test_growing_text_replaces_the_shorter_line():
    sm = machine()
    events = feed_all(sm, ["Where the", "Where the", "Where the hell did",
                           "Where the hell did", "Where the hell did you park?",
                           "Where the hell did you park?"])  # fmt: skip
    stable = [e.line for e in events if isinstance(e, LineStable)]
    assert [line.text for line in stable] == [
        "Where the", "Where the hell did", "Where the hell did you park?"]
    assert stable[1].replaces == stable[0].id
    assert stable[2].replaces == stable[1].id
    assert extends("Where the...", "Where the hell")


def test_typewriter_that_never_pauses_is_translated_once_finished():
    sm = machine()
    texts = ["Get", "Get in", "Get in the", "Get in the car", "Get in the car!", "Get in the car!"]
    assert stable_texts(feed_all(sm, texts)) == ["Get in the car!"]


def test_line_clears_after_k_empty_ticks():
    sm = machine(empty_ticks=2)
    events = feed_all(sm, ["Hold on tight."] * 2)
    line_id = events[0].line.id
    assert sm.feed("", 2.0) == []  # one empty read may be a missed frame
    assert sm.feed("", 2.25) == [LineCleared(line_id)]
    assert sm.state == State.EMPTY
    assert stable_texts(feed_all(sm, ["Hold on tight."] * 2)) == ["Hold on tight."]


def test_one_empty_read_between_two_reads_of_the_same_line_changes_nothing():
    sm = machine(empty_ticks=2)
    events = feed_all(sm, ["Hold on tight.", "Hold on tight.", "", "Hold on tight."])
    assert stable_texts(events) == ["Hold on tight."]
    assert not any(isinstance(e, LineCleared) for e in events)


def test_flicker_back_to_the_shown_line_is_not_new():
    sm = machine()
    events = feed_all(sm, ["Keep driving.", "Keep driving.", "HUD 120 m", "Keep driving."])
    assert stable_texts(events) == ["Keep driving."]


def test_noise_is_dropped():
    sm = machine()
    for noise in ["", "ok", "||| --- ///", "12:45 99%", "  ..  "]:
        assert sm.clean(noise) == "", noise
    assert sm.clean("Go!") == "Go!"


def test_ignore_phrases_are_cut_out_before_matching():
    sm = machine(ignore_phrases=[r"press \S+ to skip", "[invalid("])
    assert sm.clean("Press X to skip") == ""
    assert sm.clean("Stay close. Press ⓧ to skip") == "Stay close."
    assert sm.clean("a [invalid( b c d") == "a b c d"  # broken regex matched literally


def test_speaker_name_is_split_off():
    sm = machine()
    (event,) = feed_all(sm, ["LUCIA: Not without the money."] * 2)
    assert event.line.speaker == "LUCIA"
    assert event.line.text == "Not without the money."
    assert sm.split_speaker("Old Man Jenkins: Get off my lawn") == ("Old Man Jenkins",
                                                                    "Get off my lawn")
    assert sm.split_speaker("WARNING:") == (None, "WARNING:")


def test_stable_repeats_zero_means_first_sight():
    sm = machine(stable_repeats=0)
    assert stable_texts(sm.feed("Instant line.", 0.0)) == ["Instant line."]


# --- board --------------------------------------------------------------------------
def _line(i, text="Hi there", seen=0.0, replaces=None):
    return SubtitleLine(i, text, None, seen, replaces)


def _result(text="Merhaba", engine="gemini", fallback=False):
    return TranslationResult(text, engine, 100.0, fallback=fallback)


def test_board_shows_only_the_current_lines_translation():
    board = SubtitleBoard(min_display_ms=1500)
    board.line_stable(_line(1))
    board.line_stable(_line(2, "Second"))
    assert not board.translated(_line(1), _result("eski"), now=1.0)  # late: line 1 is gone
    assert board.translated(_line(2, "Second", seen=0.5), _result("yeni"), now=1.0)
    assert board.shown.text == "yeni"
    assert board.shown.latency_ms == pytest.approx(500)


def test_board_keeps_a_translation_for_min_display_time():
    board = SubtitleBoard(min_display_ms=1500)
    board.line_stable(_line(1))
    board.translated(_line(1), _result(), now=10.0)
    assert not board.cleared(1, now=10.5)  # left the screen early: stays until 11.5
    assert not board.tick(11.0)
    assert board.tick(11.6)
    assert board.shown is None


def test_board_clears_at_once_when_shown_long_enough():
    board = SubtitleBoard(min_display_ms=1500)
    board.line_stable(_line(1))
    board.translated(_line(1), _result(), now=10.0)
    assert board.cleared(1, now=13.0)
    assert board.shown is None


# --- translation service ----------------------------------------------------------
class FakeEngine(BaseTranslator):
    def __init__(self, name="gemini", fail: Exception | None = None, tokens=(40, 12)):
        self.name = name
        self.calls: list[TranslationRequest] = []
        self.fail = fail
        self.tokens = tokens

    def translate(self, request):
        self.calls.append(request)
        if self.fail is not None:
            raise self.fail
        return TranslationResult(f"TR<{request.text}>", self.name, 50.0,
                                 input_tokens=self.tokens[0], output_tokens=self.tokens[1])  # fmt: skip


def service(primary=None, fallback=None, cache=None, config=None, clock=None):
    return TranslationService(config or AppConfig(), MetricsHub(), primary, fallback,
                              cache or TranslationCache(100), **({"clock": clock} if clock else {}))  # fmt: skip


def test_same_line_never_goes_to_the_api_twice():
    """M3 acceptance: the state machine reports a line once, and the cache catches the
    same line coming back later (it reappears, or OCR reads it with other spacing)."""
    gemini = FakeEngine()
    svc = service(primary=gemini)
    sm = machine()
    texts = (["Where did you park?"] * 6 + [""] * 3 + ["Where did you park?"] * 4
             + ["WHERE  did you park?"] * 2  # same line still on screen: not reported
             + [""] * 3 + ["WHERE  did you park?"] * 3 + [""] * 3)  # fmt: skip
    for event in feed_all(sm, texts):
        if isinstance(event, LineStable):
            svc.translate(TranslationRequest(event.line.text))
    assert len(gemini.calls) == 1
    assert svc.usage.requests == 1
    assert svc.usage.cache_hits == 2


def test_cost_is_computed_from_tokens_and_the_models_price():
    gemini = FakeEngine(tokens=(1_000_000, 100_000))
    svc = service(primary=gemini)
    result = svc.translate(TranslationRequest("x y z"))
    price = AppConfig().translator.prices["gemini-3.5-flash-lite"]
    assert result.cost_usd == pytest.approx(price.input + price.output / 10)
    assert svc.usage.cost_usd == pytest.approx(result.cost_usd)
    assert "~$" in svc.summary()


def test_primary_failure_falls_back_and_is_marked():
    gemini = FakeEngine(fail=TranslationError("timeout"))
    deep = FakeEngine("deep")
    svc = service(primary=gemini, fallback=deep)
    result = svc.translate(TranslationRequest("Run!"))
    assert result.engine == "deep" and result.fallback
    assert svc.usage.fallbacks == 1


def test_a_fallback_translation_in_the_cache_gets_retried_with_gemini():
    cache = TranslationCache(100)
    cache.put(cache_key("Run!", "tr"), CachedTranslation("Koş! (yedek)", "deep"))
    gemini = FakeEngine()
    result = service(primary=gemini, cache=cache).translate(TranslationRequest("Run!"))
    assert result.engine == "gemini" and len(gemini.calls) == 1


def test_without_any_engine_working_an_error_is_raised():
    svc = service(primary=FakeEngine(fail=TranslationError("x")),
                  fallback=FakeEngine("deep", fail=TranslationError("y")))  # fmt: skip
    with pytest.raises(TranslationError):
        svc.translate(TranslationRequest("Run!"))
    assert svc.usage.errors == 1


def test_disk_cache_survives_a_restart(tmp_path):
    path = tmp_path / "cache" / "translations.sqlite"
    first = TranslationCache(10, path)
    first.put(cache_key("Hello there", "tr"), CachedTranslation("Merhaba", "gemini"))
    first.close()
    second = TranslationCache(10, path)
    assert second.get(cache_key("  hello   THERE ", "tr")) == CachedTranslation("Merhaba", "gemini")
    assert len(second) == 1
    second.clear()
    assert len(second) == 0


def test_memory_cache_is_lru_bounded():
    cache = TranslationCache(2)
    for i in range(3):
        cache.put(f"k{i}", CachedTranslation(str(i), "gemini"))
    assert cache.get("k0") is None and cache.get("k2") is not None


def test_broken_cache_file_falls_back_to_memory(tmp_path):
    bad = tmp_path / "cache.sqlite"
    bad.write_text("not a database")
    cache = TranslationCache(10, bad)
    cache.put("k", CachedTranslation("v", "gemini"))
    assert cache.get("k").text == "v"


# --- Gemini translator (fake client) ----------------------------------------------
class FakeAPIError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class ReadTimeout(Exception):  # name like httpx2.ReadTimeout
    pass


class FakeResponse:
    def __init__(self, text, prompt_tokens=30, out=8, thoughts=0):
        self.text = text
        self.usage_metadata = type("U", (), {"prompt_token_count": prompt_tokens,
                                              "candidates_token_count": out,
                                              "thoughts_token_count": thoughts})()  # fmt: skip
        self.prompt_feedback = None
        self.candidates = []


class FakeClient:
    def __init__(self, key, script):
        self.key = key
        self.script = script  # shared list of outcomes, consumed in order
        self.models = self
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def gemini_with(script, keys=("key-one-123456789", "key-two-123456789"), clock=None, **kw):
    clients = {}

    def factory(key, timeout_s):
        clients[key] = FakeClient(key, script)
        return clients[key]

    args = dict(model="gemini-3.5-flash-lite", timeout_s=2.5, retries=1, thinking_level="minimal",
                client_factory=factory, quota_cooldown_s=60)
    args.update(kw)
    if clock is not None:
        args["clock"] = clock
    return GeminiTranslator(list(keys), **args), clients


def test_gemini_request_shape():
    pytest.importorskip("google.genai")
    engine, clients = gemini_with([FakeResponse('"Kafanı eğ."', thoughts=4)])
    result = engine.translate(TranslationRequest("Keep your head down.",
                                                 (("Go.", "Git."),), "JASON"))  # fmt: skip
    assert result.text == "Kafanı eğ."  # quotes stripped
    assert (result.input_tokens, result.output_tokens) == (30, 12)  # thinking tokens count as output
    (model, contents, config), = next(iter(clients.values())).calls
    assert model == "gemini-3.5-flash-lite"
    assert "EN: Go.\nTR: Git." in contents and "Konuşan: JASON" in contents
    assert contents.rstrip().endswith("Keep your head down.")
    assert config.system_instruction == DEFAULT_SYSTEM_PROMPT
    assert str(config.thinking_config.thinking_level.value) == "MINIMAL"
    assert config.temperature is None and config.top_p is None and config.top_k is None
    assert {str(s.threshold.value) for s in config.safety_settings} == {"OFF"}


def test_quota_error_rotates_to_the_next_key_without_using_a_retry(clock):
    pytest.importorskip("google.genai")
    engine, clients = gemini_with([FakeAPIError(429), FakeResponse("Tamam")], clock=clock)
    assert engine.translate(TranslationRequest("OK")).text == "Tamam"
    assert [len(c.calls) for c in clients.values()] == [1, 1]
    # The first key rests; after the cooldown it is used again.
    clock.advance(61)
    engine._current = 0
    assert engine._next_key() == 0


def test_all_keys_on_quota_raises_for_fallback(clock):
    pytest.importorskip("google.genai")
    engine, _ = gemini_with([FakeAPIError(429), FakeAPIError(429)], clock=clock)
    with pytest.raises(TranslationError, match="quota"):
        engine.translate(TranslationRequest("OK"))


def test_timeout_goes_to_fallback_without_retry():
    pytest.importorskip("google.genai")
    script = [ReadTimeout("slow"), FakeResponse("never used")]
    engine, _ = gemini_with(script)
    with pytest.raises(TranslationError, match="timeout"):
        engine.translate(TranslationRequest("OK"))
    assert len(script) == 1
    assert is_timeout(ReadTimeout())


def test_server_error_is_retried_once():
    pytest.importorskip("google.genai")
    engine, _ = gemini_with([FakeAPIError(503), FakeResponse("Oldu")])
    assert engine.translate(TranslationRequest("OK")).text == "Oldu"
    engine, _ = gemini_with([FakeAPIError(503), FakeAPIError(503)])
    with pytest.raises(TranslationError):
        engine.translate(TranslationRequest("OK"))


def test_blocked_or_empty_reply_is_an_error():
    pytest.importorskip("google.genai")
    engine, _ = gemini_with([FakeResponse(None)])
    with pytest.raises(TranslationError, match="empty"):
        engine.translate(TranslationRequest("OK"))


def test_real_client_turns_off_sdk_retries_and_uses_ms_timeout():
    pytest.importorskip("google.genai")
    from subwizard.translator.gemini import _make_client

    client = _make_client("dummy-key-123456", 2.5)
    options = client._api_client._http_options
    assert options.timeout == 2500
    assert options.retry_options.attempts == 1


def test_prompt_helpers():
    assert clean_reply("TR: «Selam»") == "Selam"
    prompt = user_prompt(TranslationRequest("Hi"))
    assert "Önceki" not in prompt and prompt.endswith("Hi")


# --- fallback -------------------------------------------------------------------------
def test_deep_fallback_times_out_instead_of_hanging():
    gate = threading.Event()
    deep = DeepTranslatorFallback("en", "tr", timeout_s=0.1, backend=lambda t: gate.wait(5) or "x")
    started = time.perf_counter()
    with pytest.raises(TranslationError, match="timeout"):
        deep.translate(TranslationRequest("Hello"))
    assert time.perf_counter() - started < 1.0
    gate.set()
    deep.close()


def test_deep_fallback_does_not_send_the_speaker():
    sent = []
    deep = DeepTranslatorFallback("en", "tr", 1.0, backend=lambda t: sent.append(t) or "Selam")
    assert deep.translate(TranslationRequest("Hello", speaker="LUCIA")).text == "Selam"
    assert sent == ["Hello"]


# --- keys -----------------------------------------------------------------------------
def test_keys_come_from_env_then_dotenv_then_keyring(monkeypatch, tmp_path, isolated_user_state):
    assert secrets.load_api_keys() == ([], "")
    secrets.save_api_keys(["k-keyring-1"])
    assert secrets.load_api_keys() == (["k-keyring-1"], "keyring")
    (tmp_path / ".env").write_text('GEMINI_API_KEY="k-dotenv"\n')
    assert secrets.load_api_keys() == (["k-dotenv"], "env")
    monkeypatch.setenv("GEMINI_API_KEYS", "a1, b2,a1")
    assert secrets.load_api_keys() == (["a1", "b2"], "env")
    secrets.save_api_keys([])
    assert isolated_user_state.store == {}
    assert secrets.mask("AIzaSyD-1234567890abcd") == "AIza…abcd"


def test_no_key_means_deep_only(isolated_user_state):
    primary, fallback, note = build_engines(AppConfig())
    assert primary is None and fallback is not None and note == "no_api_key"
    primary, fallback, _ = build_engines(load_config(None, {"translator": {"fallback": False}}))
    assert primary is None and fallback is None


def test_key_selects_gemini(isolated_user_state):
    secrets.save_api_keys(["k-123456789012"])
    primary, fallback, note = build_engines(AppConfig())
    assert primary.name == "gemini" and fallback.name == "deep" and note == ""


def test_test_button_reports_engine_and_latency_without_key():
    outcome = run_test(AppConfig(), "Run!")
    assert outcome.text == "[tr] Run!"  # the offline stand-in for Google Translate
    assert outcome.engine == "deep" and outcome.note == "no_api_key"


# --- worker ---------------------------------------------------------------------------
def test_worker_translates_with_context_and_keeps_only_the_newest_pending():
    gate = threading.Event()
    done: list = []

    class SlowEngine(FakeEngine):
        def translate(self, request):
            gate.wait(2)
            return super().translate(request)

    engine = SlowEngine()
    worker = TranslateWorker(AppConfig(), MetricsHub(),
                             lambda cfg, m: service(primary=engine), done.append)  # fmt: skip
    worker.start()
    worker.submit(_line(1, "one"))
    time.sleep(0.2)  # "one" is now in flight
    worker.submit(_line(2, "two"))
    worker.submit(_line(3, "three"))  # "two" left the screen before its turn: skipped
    gate.set()
    deadline = time.time() + 3
    while len(done) < 2 and time.time() < deadline:
        time.sleep(0.01)
    worker.stop()
    worker.join(2)
    assert [d.line.text for d in done] == ["one", "three"]
    assert engine.calls[1].context == (("one", "TR<one>"),)


def test_metrics_record_requests_and_cache_hits():
    svc = service(primary=FakeEngine())
    for _ in range(3):
        svc.translate(TranslationRequest("same"))
    snap = svc._metrics.snapshot()
    assert snap.counter(M.TR_REQUESTS) == 1 and snap.counter(M.TR_CACHE_HITS) == 2
    assert snap.gauge(M.TR_COST) > 0
