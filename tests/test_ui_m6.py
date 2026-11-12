"""M6 UI: Game Profiles page (select, edit glossary, save, copy, import / export, reset)."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from subwizard import profiles  # noqa: E402
from tests.test_ui_m1 import _page, window  # noqa: E402, F401


def _select(page, name):
    page.picker.setCurrentIndex(page._names.index(name))


def test_using_a_profile_applies_its_region_and_saves_the_choice(window):
    c = window.controller
    page = _page(window, "profiles")
    _select(page, "gta6")
    assert page.title_edit.text() == "GTA 6"
    assert page.glossary.rowCount() >= 4
    page.use_selected()
    assert c.config.profile == "gta6"
    assert c.config.roi.template == "bottom_center"
    assert not page.use_button.isEnabled()
    assert "GTA 6" in page._active.text()


def test_editing_the_glossary_saves_a_user_copy_and_reset_restores_it(window):
    page = _page(window, "profiles")
    _select(page, "gta6")
    page.add_term("Leonida Keys", "keep")
    page.save()
    assert profiles.is_user("gta6")
    assert profiles.load_profile("gta6").glossary["Leonida Keys"] == "keep"
    assert page.delete_button.isEnabled()
    page.delete_selected()  # built-in: "reset to default"
    assert not profiles.is_user("gta6")
    assert "Leonida Keys" not in profiles.load_profile("gta6").glossary


def test_copy_creates_a_new_profile_that_can_be_deleted(window):
    page = _page(window, "profiles")
    _select(page, "gta6")
    page.new_profile(copy=True)
    name = page.selected
    assert name.startswith("gta6-copy") and profiles.is_user(name)
    assert profiles.load_profile(name).keep_terms == profiles.load_profile("gta6").keep_terms
    page.delete_selected()
    assert name not in profiles.list_profiles()


def test_export_then_import_round_trips(window, tmp_path):
    page = _page(window, "profiles")
    _select(page, "gta6")
    out = tmp_path / "Shared Game.yaml"
    page.export_file(out)
    page.import_file(out)
    assert page.selected == "shared-game"
    assert profiles.load_profile("shared-game").glossary == profiles.load_profile("gta6").glossary


def test_importing_garbage_shows_a_notice(window, tmp_path):
    page = _page(window, "profiles")
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    page.import_file(bad)
    assert page._notice.isVisibleTo(page)


def test_region_page_draws_extra_and_excluded_areas(window):
    from subwizard.ocr.roi import Rect

    c = window.controller
    page = _page(window, "roi")
    page._draw_mode.setCurrentIndex(1)
    page._on_rect(Rect(0.1, 0.0, 0.4, 0.1))
    page._draw_mode.setCurrentIndex(2)
    page._on_rect(Rect(0.8, 0.8, 0.2, 0.2))
    assert c.config.roi.extra_rects == [[0.1, 0.0, 0.4, 0.1]]
    assert c.config.roi.exclude_rects == [[0.8, 0.8, 0.2, 0.2]]
    assert "1" in page._regions_info.text() and page._clear_regions.isEnabled()
    page.editor.grab()  # paints the blue / red areas without errors
    page._on_clear_regions()
    assert c.config.roi.extra_rects == [] and c.config.roi.exclude_rects == []
    page._draw_mode.setCurrentIndex(0)
    page._on_rect(Rect(0.2, 0.7, 0.6, 0.2))
    assert c.config.roi.rect == [0.2, 0.7, 0.6, 0.2]


def test_keys_are_added_listed_masked_and_removed_one_by_one(window, isolated_user_state):
    c = window.controller
    page = _page(window, "translate")
    page._keys.setText("AIzaKEY-one-1234567")
    page._save.click()
    page._keys.setText("AIzaKEY-two-7654321, AIzaKEY-one-1234567")  # one new, one repeat
    page._save.click()
    assert c.api_key_status() == (2, "keyring")
    shown = [label.text() for label in page.key_list.findChildren(type(page._test_meta))]
    assert len(shown) == 2 and "AIza…4567" in shown[0] and "AIza…4321" in shown[1]
    assert not any("KEY-one" in text for text in shown)  # never the whole key
    c.remove_api_key(0)
    assert list(isolated_user_state.store.values()) == ["AIzaKEY-two-7654321"]
    page.refresh_key_list()
    assert page.key_list.isVisibleTo(page)


def test_resting_key_state_comes_from_the_translator():
    from subwizard.translator.gemini import GeminiTranslator

    now = [100.0]
    g = GeminiTranslator(["k1-aaaaaaaaaaaa", "k2-bbbbbbbbbbbb"], model="m", timeout_s=1,
                         retries=0, thinking_level="minimal", quota_cooldown_s=60,
                         client_factory=lambda *a: None, clock=lambda: now[0])  # fmt: skip
    assert g.key_states() == [("active", 0.0), ("ready", 0.0)]
    g._resting_until[0] = 130.0
    g._next_key()
    assert g.key_states() == [("resting", 30.0), ("active", 0.0)]


def test_diagnostics_graphs_fill_while_the_clip_plays(window):
    from tests.test_ui_m1 import wait_until

    c = window.controller
    page = _page(window, "diagnostics")
    c.start()
    assert wait_until(lambda: len(page.recorder) >= 3, timeout=10)
    page.graphs.refresh()  # the page refreshes itself only while visible (tests never show)
    fps = page.graphs.charts["fps"].series[0]
    assert len(fps) >= 3 and fps[-1] and fps[-1] > 1
    assert page.graphs.charts["fps"].value_range()[1] >= 60
    page.graphs.grab()  # paints without errors
    c.stop()


def test_wizard_lists_user_profiles_too(window):
    from subwizard.profiles import GameProfile
    from subwizard.ui.wizard import SetupWizard

    profiles.save_profile(GameProfile(name="rdr2", title="Red Dead", game_info="Batı."))
    wizard = SetupWizard(window.controller)
    try:
        assert list(wizard.profile_cards)[:2] == ["gta6", "default"]
        card = wizard.profile_cards["rdr2"]
        assert card.title.text() == "Red Dead" and card.text.text() == "Batı."
        wizard._choose_profile("rdr2")
        assert window.controller.config.profile == "rdr2"
    finally:
        wizard.close()


def test_reading_method_switch_rebuilds_ocr(window):
    c = window.controller
    page = _page(window, "translate")
    c.start()
    page._mode.setCurrentIndex(1)
    assert c.config.translator.mode == "vision"
    assert c._session._ocr is not None  # restarted (local OCR: no key in tests)
    page._mode.setCurrentIndex(0)
    assert c.config.translator.mode == "ocr"
    c.stop()


def test_av_measure_needs_audio_then_applies_the_estimate(window):
    import numpy as np

    c = window.controller
    page = _page(window, "device")
    page._on_av_measure()  # file source, no audio: cannot start
    assert "capture card" in page._av_result
    rec = c._session.av_recorder
    rec.start()  # as if listening: picture 90 ms after the sound, four times
    dark, bright = np.zeros((8, 8, 3), np.uint8), np.full((8, 8, 3), 200, np.uint8)
    for event in (1.0, 2.0, 3.0, 4.0):
        rec._video += [(event + 0.08, 0.0), (event + 0.09, float(bright.mean()))]
        rec._audio += [(event - 0.01, 0.0), (event, 0.9)]
    rec._video.append((0.0, float(dark.mean())))
    c._finish_av_sync()
    assert c.config.audio.delay_ms == 90
    assert page._delay.slider.value() == 90 and "90" in page._av_result
    rec.start()
    c._finish_av_sync()  # nothing recorded: failure text, delay unchanged
    assert c.config.audio.delay_ms == 90 and page._av_measure.isEnabled()


def test_read_aloud_setting_is_saved_and_starts_the_worker(window, monkeypatch):
    from tests.test_tts import FakeTTS

    c = window.controller
    engine = FakeTTS()
    monkeypatch.setattr(c._session, "_tts_factory", lambda name, voice, output: engine)
    page = _page(window, "translate")
    c.start()
    assert not page._tts_engine.isEnabled()
    page._tts.setCurrentIndex(1)
    assert c.config.tts.mode == "translation" and c._session._tts is not None
    assert page._tts_engine.isEnabled()
    page._tts_engine.setCurrentIndex(1)
    assert c.config.tts.engine == "edge" and c._session._tts is not None
    page._tts.setCurrentIndex(0)
    assert c.config.tts.mode == "off" and c._session._tts is None
    c.stop()


def test_ocr_engine_choice_is_saved_and_rebuilds_ocr(window):
    c = window.controller
    page = _page(window, "roi")
    c.start()
    page._engine.setCurrentIndex(1)
    assert c.config.ocr.engine == "windows"
    assert c._session._ocr is not None  # off Windows: rebuilt with RapidOCR
    page._engine.setCurrentIndex(0)
    assert c.config.ocr.engine == "rapid"
    c.stop()
