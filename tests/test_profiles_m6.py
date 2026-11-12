"""M6: game profiles, glossary (keep terms), profile ignore list (SPEC 3.6, M6 acceptance)."""

import logging
import re

import pytest

from subwizard import profiles
from subwizard.cli import build_parser, overrides_from_args
from subwizard.config import AppConfig, SubtitleConfig
from subwizard.core.metrics import M, MetricsHub
from subwizard.pipeline.subtitle_state import SubtitleStateMachine
from subwizard.profiles import GameProfile, glossary_violations, load_profile
from subwizard.translator.base import BaseTranslator, TranslationRequest, TranslationResult
from subwizard.translator.cache import TranslationCache
from subwizard.translator.deep import DeepTranslatorFallback
from subwizard.translator.prompt import protect, restore, system_prompt
from subwizard.translator.service import TranslationService, build_engines

GLOSSARY = {"Jason": "keep", "Vice City": "keep", "homie": "kanka"}

# A "machine translation" that translates every word it knows, names included: what
# Google Translate does to "Vice City" -> "Yardımcı Şehir" without protection.
WORDS = {"meet": "buluş", "me": "benimle", "in": "içinde", "vice": "Yardımcı",
         "city": "Şehir", "jason": "Ceyson", "homie": "dostum", "tonight": "bu gece"}  # fmt: skip


def word_by_word(text: str) -> str:
    return re.sub(r"[A-Za-z]+", lambda m: WORDS.get(m.group(0).lower(), m.group(0)), text)


def test_builtin_profiles_are_listed_default_first():
    found = profiles.list_profiles()
    assert list(found)[0] == "default"
    assert "gta6" in found
    gta = found["gta6"]
    assert gta.glossary["Jason"] == "keep" and "Jason" in gta.keep_terms
    assert gta.roi_template == "bottom_center"


def test_unknown_profile_falls_back_to_default():
    assert load_profile("no-such-game").name == "default"


def test_user_profile_overrides_builtin_and_delete_restores_it():
    edited = load_profile("gta6").model_copy(update={"prompt_extra": "Kısa tut."})
    profiles.save_profile(edited)
    assert load_profile("gta6").prompt_extra == "Kısa tut."
    assert profiles.is_user("gta6") and profiles.is_builtin("gta6")
    assert profiles.delete_profile("gta6")
    assert load_profile("gta6").prompt_extra != "Kısa tut."


def test_import_names_the_profile_after_the_file(tmp_path):
    src = tmp_path / "My Game!.yaml"
    src.write_text("title: Benim Oyunum\nglossary:\n  Arthur: keep\n", encoding="utf-8")
    imported = profiles.import_profile(src)
    assert imported.name == "my-game"
    assert load_profile("my-game").keep_terms == ["Arthur"]


def test_broken_yaml_is_skipped_not_fatal(tmp_path):
    profiles.user_dir().mkdir(parents=True, exist_ok=True)
    (profiles.user_dir() / "broken.yaml").write_text("glossary: [unclosed", encoding="utf-8")
    assert "broken" not in profiles.list_profiles()


def test_prompt_contains_game_info_glossary_and_extra():
    profile = GameProfile(name="x", game_info="Vice City'de geçer.", prompt_extra="Argoyu koru.",
                          glossary=GLOSSARY)  # fmt: skip
    prompt = system_prompt("", profile)
    assert "Vice City'de geçer." in prompt
    assert "- Jason → Jason (çevirme" in prompt
    assert "- homie → kanka" in prompt
    assert prompt.rstrip().endswith("Argoyu koru.")
    assert system_prompt("", None) == system_prompt("", GameProfile())


def test_profile_reaches_the_prompt_in_the_debug_log(caplog, monkeypatch):
    """M6 acceptance: with `--profile gta6` the profile is visible in the debug log."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    overrides = overrides_from_args(build_parser().parse_args(["--profile", "gta6"]))
    config = AppConfig(**overrides)
    with caplog.at_level(logging.DEBUG, logger="subwizard.translator.service"):
        primary, _fallback, _ = build_engines(config)
    assert primary is not None and primary.name == "gemini"
    text = caplog.text
    assert "profil gta6" in text
    assert "Leonida" in text and "- Lucia → Lucia (çevirme" in text
    assert "test-key-not-real" not in text


def test_keep_terms_survive_machine_translation():
    """M6 acceptance: glossary `keep` terms are kept in the translation (mock translator)."""
    profile = GameProfile(name="g", glossary=GLOSSARY)
    deep = DeepTranslatorFallback("en", "tr", 1.0, backend=word_by_word, profile=profile)
    request = TranslationRequest("Meet me in Vice City tonight, Jason")
    result = deep.translate(request)
    assert "Vice City" in result.text and "Jason" in result.text
    assert "Yardımcı" not in result.text and "Ceyson" not in result.text
    assert glossary_violations(request.text, result.text, GameProfile(name="g", glossary={
        "Jason": "keep", "Vice City": "keep"})) == []  # fmt: skip
    # Without the profile the same engine mangles the names (the mock is honest).
    plain = DeepTranslatorFallback("en", "tr", 1.0, backend=word_by_word).translate(request)
    assert "Ceyson" in plain.text


def test_protect_and_restore_keep_original_spelling():
    profile = GameProfile(name="g", glossary={"Vice City": "keep", "Vice": "keep"})
    text, originals = protect("VICE CITY and vice", profile)
    assert "VICE" not in text and originals == ["VICE CITY", "vice"]
    assert restore(text.lower(), originals) == "VICE CITY and vice"


class LiteralEngine(BaseTranslator):
    name = "gemini"

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def translate(self, request):
        return TranslationResult(self.reply, self.name, 10.0)


def test_glossary_violation_is_logged_and_counted(caplog):
    metrics = MetricsHub()
    profile = GameProfile(name="g", glossary=GLOSSARY)
    svc = TranslationService(AppConfig(), metrics, LiteralEngine("Dostum, Ceyson geldi"), None,
                             TranslationCache(10), profile=profile)  # fmt: skip
    with caplog.at_level(logging.WARNING):
        svc.translate(TranslationRequest("Homie, Jason is here"))
    assert metrics.snapshot().counter(M.TR_GLOSSARY_MISSES) == 1
    assert "Jason → Jason" in caplog.text and "homie → kanka" in caplog.text


def test_turkish_suffix_after_a_kept_name_is_not_a_violation():
    profile = GameProfile(name="g", glossary=GLOSSARY)
    assert glossary_violations("Tell Jason, homie", "Jason'a söyle, kankam", profile) == []


def test_profiles_have_separate_cache_entries():
    cache = TranslationCache(10)
    a = TranslationService(AppConfig(), MetricsHub(), LiteralEngine("A"), None, cache)
    b = TranslationService(AppConfig(), MetricsHub(), LiteralEngine("B"), None, cache,
                           profile=GameProfile(name="gta6"))  # fmt: skip
    assert a.translate(TranslationRequest("Hello there")).text == "A"
    assert b.translate(TranslationRequest("Hello there")).text == "B"
    assert a.translate(TranslationRequest("Hello there")).cached


@pytest.mark.parametrize("text", ["Press ⓧ to skip", "HOLD [X] TO SKIP"])
def test_profile_ignore_list_removes_button_prompts(text):
    sm = SubtitleStateMachine(SubtitleConfig(), tuple(load_profile("gta6").ignore_phrases))
    assert sm.clean(text) == ""
    assert sm.clean(f"{text} We need to go") == "We need to go"


# --- several regions, excluded areas --------------------------------------------------
def _frame():
    import numpy as np

    return np.full((100, 200, 3), 255, dtype=np.uint8)  # all white: every pixel "text"


def test_single_region_is_the_plain_crop_view():
    from subwizard.ocr.roi import Rect, crop, crop_regions

    image = _frame()
    main = Rect(0.1, 0.5, 0.5, 0.3)
    region = crop_regions(image, main, [], [], 0.0)
    assert region.base is image  # no copy on the common path
    assert region.shape == crop(image, main, 0.0).shape


def test_excluded_area_is_blacked_out_and_the_frame_is_untouched():
    from subwizard.ocr.roi import Rect, crop_regions

    image = _frame()
    region = crop_regions(image, Rect(0.0, 0.5, 1.0, 0.5), [], [Rect(0.0, 0.5, 0.25, 0.5)], 0.0)
    assert region.shape[:2] == (50, 200)
    assert (region[:, :50] == 0).all() and (region[:, 50:] == 255).all()
    assert (image == 255).all()


def test_extra_regions_are_stacked_not_boxed_and_map_back_to_the_frame():
    from subwizard.ocr.roi import STRIP_GAP_PX, Rect, crop_regions, layout, to_frame

    image = _frame()
    image[80:, :, :] = 200  # tell the regions apart
    main, top = Rect(0.0, 0.8, 1.0, 0.2), Rect(0.0, 0.0, 0.5, 0.1)
    region = crop_regions(image, main, [top], [], 0.0)
    assert region.shape[:2] == (20 + STRIP_GAP_PX + 10, 200)  # not the 100-row bounding box
    assert (region[:20] == 200).all()  # main first
    assert (region[20:20 + STRIP_GAP_PX] == 0).all()  # black gap
    assert (region[20 + STRIP_GAP_PX:, :100] == 255).all()  # the extra region
    assert (region[20 + STRIP_GAP_PX:, 100:] == 0).all()  # narrower: padded black
    strips = layout(main, [top], 0.0, 200, 100)
    assert to_frame((5, 3, 10, 4), strips) == (5, 83, 10, 4)
    assert to_frame((5, 20 + STRIP_GAP_PX + 2, 10, 4), strips) == (5, 2, 10, 4)


def test_excluded_area_inside_an_extra_region_is_blacked_out():
    from subwizard.ocr.roi import STRIP_GAP_PX, Rect, crop_regions

    image = _frame()
    region = crop_regions(image, Rect(0.0, 0.8, 1.0, 0.2), [Rect(0.0, 0.0, 0.5, 0.1)],
                          [Rect(0.0, 0.0, 0.1, 0.1)], 0.0)  # fmt: skip
    extra = region[20 + STRIP_GAP_PX:]
    assert (extra[:, :20] == 0).all() and (extra[:, 20:100] == 255).all()


def test_extra_and_excluded_regions_round_trip_through_the_config(tmp_path):
    from subwizard.config import load_config, save_config

    path = tmp_path / "c.yaml"
    config = load_config(path, {})
    config.roi.extra_rects = [[0.1, 0.0, 0.3, 0.1]]
    config.roi.exclude_rects = [[0.8, 0.8, 0.2, 0.2]]
    save_config(config, path)
    again = load_config(path, {})
    assert again.roi.extra_rects == [[0.1, 0.0, 0.3, 0.1]]
    assert again.roi.exclude_rects == [[0.8, 0.8, 0.2, 0.2]]


# --- M7: vision mode (experimental) ----------------------------------------------------
def test_vision_sends_the_image_and_parses_both_lines():
    pytest.importorskip("google.genai")
    from tests.test_translation_core import FakeResponse, gemini_with

    engine, clients = gemini_with([FakeResponse("EN: Keep driving.\nTR: \"Sürmeye devam et.\"",
                                                prompt_tokens=300)],
                                  media_resolution="low")  # fmt: skip
    result = engine.translate_image(b"\xff\xd8jpeg", "image/jpeg", (("Go.", "Git."),))
    assert result.source == "Keep driving." and result.translation == "Sürmeye devam et."
    assert result.result.input_tokens == 300
    _model, contents, config = next(iter(clients.values())).calls[0]
    image, text = contents
    assert image.inline_data.mime_type == "image/jpeg" and image.inline_data.data == b"\xff\xd8jpeg"
    assert "EN: Go.\nTR: Git." in text
    assert "MEDIA_RESOLUTION_LOW" in str(config.media_resolution)
    assert "EN: <okuduğun" in config.system_instruction
    # the text config is not changed by the vision one
    assert engine._generate_config().media_resolution is None


@pytest.mark.parametrize(("reply", "expected"), [
    ("YOK", ("", "")),
    ("EN: Run!\nTR: Koş!", ("Run!", "Koş!")),
    ("Koş!", ("", "Koş!")),  # no labels: the whole reply is the translation
])  # fmt: skip
def test_parse_vision_reply(reply, expected):
    from subwizard.translator.prompt import parse_vision_reply

    assert parse_vision_reply(reply) == expected


def test_benchmark_report_runs_offline(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "scripts" / "benchmark.py"
    spec = importlib.util.spec_from_file_location("benchmark", path)
    bench = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(__import__("sys").modules, "benchmark", bench)  # for its dataclasses
    spec.loader.exec_module(bench)
    out = tmp_path / "b.md"
    assert bench.main(["--translator", "none", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| ocr+text | 1.000 |" in text or "| ocr+text | 0.9" in text
    assert "no Gemini API key" in text  # conftest: no keys, no network


def test_vision_engine_reads_and_hands_the_translation_to_the_service():
    import numpy as np

    from subwizard.ocr.vision import VisionHandoff, VisionOCR
    from subwizard.translator.base import VisionResult

    class FakeGemini:
        calls = 0

        def translate_image(self, image, mime_type="image/png", context=()):
            FakeGemini.calls += 1
            assert mime_type == "image/jpeg" and image[:2] == b"\xff\xd8"
            return VisionResult("We need to go.", "Gitmemiz lazım.",
                                TranslationResult("EN/TR", "gemini", 900.0,
                                                  input_tokens=300, output_tokens=20))  # fmt: skip

    handoff = VisionHandoff()
    engine = VisionOCR(FakeGemini(), handoff)
    result = engine.recognize(np.zeros((60, 400), dtype=np.uint8))
    assert result.text == "We need to go." and result.confidence == 1.0
    assert result.lines[0].box == (0, 0, 400, 60)
    assert handoff.take("we  need to go.").text == "Gitmemiz lazım."  # normalised key
    assert handoff.take("We need to go.") is None  # taken once


def test_service_uses_the_handed_over_vision_translation(monkeypatch):
    from subwizard.ocr import vision

    handoff = vision.VisionHandoff()
    monkeypatch.setattr("subwizard.translator.service.HANDOFF", handoff)
    handoff.put("Run!", TranslationResult("Koş!", "gemini", 800.0, input_tokens=280,
                                          output_tokens=5))  # fmt: skip
    metrics = MetricsHub()
    engine = LiteralEngine("SHOULD NOT BE CALLED")
    svc = TranslationService(AppConfig(), metrics, engine, None, TranslationCache(10))
    result = svc.translate(TranslationRequest("Run!"))
    assert result.text == "Koş!" and result.cost_usd > 0
    assert metrics.snapshot().counter(M.TR_TOKENS_IN) == 280
    assert svc.translate(TranslationRequest("Run!")).cached  # cached for next time


def test_vision_mode_without_a_key_falls_back_to_local_ocr():
    from subwizard.ocr.worker import _build_vision

    config = AppConfig()
    config.translator.mode = "vision"
    assert _build_vision(config) is None  # conftest: no keys
