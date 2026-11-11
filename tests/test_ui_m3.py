"""M3 UI: Translation page, API keys, test button, subtitle on the video, Home card."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from PySide6.QtCore import QRectF  # noqa: E402

from subwizard.pipeline.board import SubtitleView  # noqa: E402
from subwizard.ui.pages.home import translator_state  # noqa: E402
from subwizard.ui.pages.translate import TranslatePage  # noqa: E402
from subwizard.ui.subtitle_paint import place, render_subtitle  # noqa: E402
from subwizard.ui.metrics_view import metric_rows  # noqa: E402
from tests.test_ui_m1 import _page, wait_until, window  # noqa: E402, F401


def _view(text="Kafanı eğ, polisler her yerde. ğüşıöçİ", speaker="LUCIA", fallback=True):
    return SubtitleView(1, text, speaker, "Keep your head down.", "deep", fallback, 0.0, 420.0)


def test_translate_page_replaces_the_placeholder(window):
    assert isinstance(_page(window, "translate"), TranslatePage)


def test_saving_keys_goes_to_the_credential_store_not_the_config(window, isolated_user_state):
    page = _page(window, "translate")
    page._keys.setText("AIzaKEY-one-1234567, AIzaKEY-two-1234567")
    page._save.click()
    assert window.controller.api_key_status() == (2, "keyring")
    assert page._keys.text() == ""  # the field never keeps a key on screen
    assert list(isolated_user_state.store.values()) == ["AIzaKEY-one-1234567,AIzaKEY-two-1234567"]
    window.controller.flush()
    assert "AIzaKEY" not in window.config_path.read_text(encoding="utf-8")
    assert translator_state(window.controller)[1] == "ok"


def test_home_card_warns_without_a_key(window):
    text, grade = translator_state(window.controller)
    assert grade == "warn" and "Google" in text


def test_engine_combo_and_model_list(window):
    page = _page(window, "translate")
    page._engine.setCurrentIndex(1)
    assert window.controller.config.translator.engine == "deep"
    assert not page._key_row.isEnabled()
    assert "gemini-3.5-flash-lite" in [page._model.itemText(i) for i in range(page._model.count())]


def test_test_button_shows_result_and_latency(window):
    page = _page(window, "translate")
    page._test_input.setText("Run!")
    page._test_button.click()
    assert wait_until(lambda: page._test_button.isEnabled())
    assert page._test_result.text() == "[tr] Run!"
    assert "ms" in page._test_meta.text()


def test_subtitle_pixmap_renders_turkish_text():
    pixmap = render_subtitle(_view(), 1280, 720, 1.0, "(yedek)")
    assert not pixmap.isNull()
    assert pixmap.width() < 1280 * 0.8 + 1  # wraps at 80% of the video width
    image = pixmap.toImage()
    assert image.pixelColor(pixmap.width() // 2, pixmap.height() // 2).alpha() > 0


def test_long_subtitle_wraps_onto_more_lines():
    short = render_subtitle(_view("Kısa.", speaker=None), 1280, 720, 1.0, "")
    long = render_subtitle(_view("Bu çok uzun bir altyazı satırı " * 6, speaker=None),
                           1280, 720, 1.0, "")  # fmt: skip
    line_h = 720 * 0.045
    assert long.height() >= short.height() + 2 * line_h  # at least three lines


def test_subtitle_goes_above_the_region_or_below_when_no_room():
    video = QRectF(0, 0, 1000, 500)
    above = place(400, 60, video, roi_top=0.78, roi_bottom=0.96)
    assert above.bottom() <= 0.78 * 500
    below = place(400, 60, video, roi_top=0.02, roi_bottom=0.18)
    assert below.top() >= 0.18 * 500


def test_subtitle_signal_reaches_the_view(window):
    c = window.controller
    c._on_subtitle(_view())
    assert c.subtitle is not None
    window.home.video.resize(640, 360)
    image = window.home.video.grab()  # paints without a frame: must not crash
    assert not image.isNull()


def test_translation_rows_exist_in_metrics(window):
    labels = {r.label for r in metric_rows(window.controller.snapshot(), window.controller.config)}
    assert {"diag.sub_latency", "diag.tr_counts", "diag.tr_cost"} <= labels
