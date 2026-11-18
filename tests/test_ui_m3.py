"""M3 UI: Translation page, API keys, test button, subtitle on the video, Home card."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from PySide6.QtCore import QRectF  # noqa: E402

from subwizard.pipeline.board import SubtitleView  # noqa: E402
from subwizard.ui.pages.home import translator_state  # noqa: E402
from subwizard.ui.pages.translate import TranslatePage  # noqa: E402
from subwizard.config import OverlayConfig  # noqa: E402
from subwizard.ui.subtitle_paint import Style, place, render_subtitle  # noqa: E402
from subwizard.ui.metrics_view import metric_rows  # noqa: E402
from tests.test_ui_m1 import _page, wait_until, window  # noqa: E402, F401


STYLE = Style.from_config(OverlayConfig(), 1280, 720)


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
    models = lambda: [page._model.itemText(i) for i in range(page._model.count())]  # noqa: E731
    assert "gemini-3.5-flash-lite" in models() and "claude-haiku-5-5" not in models()
    page._engine.setCurrentIndex(1)  # OpenRouter: its own models, keys editable
    assert window.controller.config.translator.engine == "openrouter"
    assert page._key_row.isEnabled() and "google/gemini-2.5-flash-lite" in models()
    page._engine.setCurrentIndex(2)  # Claude: its own model list
    assert window.controller.config.translator.engine == "anthropic"
    assert models() == ["claude-haiku-5-5"] and page._key_row.isEnabled()
    assert not page.account_card.isVisibleTo(page)  # that card is OpenRouter's
    page._engine.setCurrentIndex(3)
    assert window.controller.config.translator.engine == "deep"
    assert not page._key_row.isEnabled()


def test_test_button_shows_result_and_latency(window):
    page = _page(window, "translate")
    page._test_input.setText("Run!")
    page._test_button.click()
    assert wait_until(lambda: page._test_button.isEnabled())
    assert page._test_result.text() == "[tr] Run!"
    assert "ms" in page._test_meta.text()


def test_subtitle_pixmap_renders_turkish_text():
    pixmap = render_subtitle(_view(), STYLE, 1.0, "(yedek)")
    assert not pixmap.isNull()
    assert pixmap.width() < 1280 * 0.8 + 1  # wraps at 80% of the video width
    image = pixmap.toImage()
    assert image.pixelColor(pixmap.width() // 2, pixmap.height() // 2).alpha() > 0


def test_long_subtitle_wraps_onto_more_lines():
    short = render_subtitle(_view("Kısa.", speaker=None), STYLE, 1.0, "")
    long = render_subtitle(_view("Bu çok uzun bir altyazı satırı " * 9, speaker=None),
                           STYLE, 1.0, "")  # fmt: skip
    line_h = STYLE.font_px  # a line is at least as tall as the font
    assert long.height() >= short.height() + 2 * line_h  # at least three lines


def test_subtitle_goes_above_the_region_or_below_when_no_room():
    video = QRectF(0, 0, 1000, 500)
    above = place("bottom", 400, 60, video, 0.78, 0.96, 1.5)
    assert above.bottom() <= 0.78 * 500
    below = place("bottom", 400, 60, video, 0.02, 0.18, 1.5)
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


def test_openrouter_account_card(window, isolated_user_state, monkeypatch):
    from subwizard.translator import openrouter

    page = _page(window, "translate")
    assert not page.account_card.isVisibleTo(page)
    page._engine.setCurrentIndex(1)  # OpenRouter, no key yet
    assert page.account_card.isVisibleTo(page)
    assert "OpenRouter" in page._account_status.text()  # "add your key" hint
    assert "$5 → ~231" in page._account_plan.text()

    info = openrouter.KeyInfo(5.0, 4.9, None, 0.1, 0.02, 3, 50)
    monkeypatch.setattr(openrouter, "fetch_key_info", lambda key: info)
    page._keys.setText("sk-or-v1-" + "b" * 40)
    page._save.click()  # its own credential store entry, then the card refreshes
    assert [user for _service, user in isolated_user_state.store] == ["openrouter_api_keys"]
    assert wait_until(lambda: "$4.90" in page._account_status.text())
    text = page._account_status.text()
    assert "~227" in text and "3 / 50" in text
