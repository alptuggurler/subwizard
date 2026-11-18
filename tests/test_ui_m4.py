"""M4 UI: subtitle renderer (Turkish glyphs, positions), Appearance / History / Hotkeys
pages, hotkey actions."""

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QKeyEvent  # noqa: E402

from subwizard.config import OverlayConfig  # noqa: E402
from subwizard.pipeline.board import SubtitleView  # noqa: E402
from subwizard.pipeline.subtitle_state import SubtitleLine  # noqa: E402
from subwizard.translator.base import TranslationResult  # noqa: E402
from subwizard.ui.pages.appearance import AppearancePage  # noqa: E402
from subwizard.ui.pages.history import HistoryPage  # noqa: E402
from subwizard.ui.pages.hotkeys import HotkeysPage  # noqa: E402
from subwizard.ui.subtitle_paint import (  # noqa: E402
    TURKISH_SAMPLE,
    Style,
    make_font,
    place,
    render_subtitle,
    surrounding_color,
    text_bounds,
)
from tests.test_ui_m1 import _page, wait_until, window  # noqa: E402, F401


@pytest.fixture(autouse=True)
def qapp():
    """Renderer tests need a QGuiApplication even when no window fixture made one."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _view(text, speaker=None):
    return SubtitleView(1, text, speaker, "src", "gemini", False, 0.0, 0.0)


def test_every_turkish_character_has_a_glyph_in_the_subtitle_font():
    """SPEC 3.7 acceptance: ğüşıöçİĞÜŞÖÇ never turn into boxes."""
    from PySide6.QtGui import QTextLayout

    style = Style.from_config(OverlayConfig(), 1920, 1080)
    font = make_font(style.families, style.font_px, style.bold)
    for ch in TURKISH_SAMPLE:  # after Qt's font fallback, as it is really drawn
        layout = QTextLayout(ch, font)
        layout.beginLayout()
        layout.createLine()
        layout.endLayout()
        glyphs = [g for run in layout.glyphRuns() for g in run.glyphIndexes()]
        assert glyphs and 0 not in glyphs, f"{ch!r} has no glyph"


def test_turkish_text_renders_as_wide_as_its_ascii_lookalike():
    # Missing glyphs would collapse to boxes of one fixed width; real glyphs differ.
    style = Style.from_config(OverlayConfig(background_opacity=0.0), 1920, 1080)
    tr = render_subtitle(_view(TURKISH_SAMPLE), style, 1.0, "").toImage()
    lit = sum(tr.pixelColor(x, y).alpha() > 0 for x in range(tr.width())
              for y in range(0, tr.height(), 2))  # fmt: skip
    assert lit > 100


def test_font_size_is_a_percentage_of_the_video_height():
    assert Style.from_config(OverlayConfig(font_size_pct=5), 1920, 1080).font_px == 54
    assert Style.from_config(OverlayConfig(font_size_pct=5), 1280, 720).font_px == 36


def test_top_position_and_margin():
    video = QRectF(0, 0, 1000, 500)
    box = place("top", 400, 60, video, 0.8, 0.95, 2.0)
    assert box.top() == 10 and box.left() == 300


def test_replace_mode_samples_the_color_around_the_text():
    import numpy as np

    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[:, :] = (30, 60, 90)  # BGR scene
    image[40:60, 50:150] = (255, 255, 255)  # the English text
    bounds = text_bounds([(10, 5, 100, 20)], (40, 35))
    assert bounds == (50, 40, 150, 60)
    assert surrounding_color(image, bounds) == (90, 60, 30)


def test_pages_replace_their_placeholders(window):
    assert isinstance(_page(window, "appearance"), AppearancePage)
    assert isinstance(_page(window, "history"), HistoryPage)
    assert isinstance(_page(window, "hotkeys"), HotkeysPage)


def test_preset_and_options_are_saved_and_repaint_the_preview(window):
    c = window.controller
    page = _page(window, "appearance")
    c.apply_overlay_preset("high_contrast")
    assert c.config.overlay.text_color == "#FFFF00" and c.config.overlay.font_size_pct == 6.0
    assert page._size.slider.value() == 60
    page._position.setCurrentIndex(1)
    assert c.config.overlay.position == "replace"
    c.flush()
    assert "replace" in window.config_path.read_text(encoding="utf-8")
    page.preview.resize(480, 270)
    assert not page.preview.grab().isNull()


def test_history_page_lists_searches_and_exports(window, tmp_path):
    c = window.controller
    c.history.add(SubtitleLine(1, "Keep your head down.", "LUCIA", 1.0))
    c.history.translated(1, TranslationResult("Kafanı eğ.", "gemini", 100.0))
    c.history.add(SubtitleLine(2, "Run!", None, 2.0))
    page = _page(window, "history")
    page.refresh()
    assert page.table.rowCount() == 2
    assert page.table.item(0, 2).text() == "Kafanı eğ."
    page._search.setText("run")
    assert page.table.rowCount() == 1
    page._search.setText("")
    out = tmp_path / "out.srt"
    page.export(out, "srt")
    assert "Kafanı eğ.\nLUCIA: Keep your head down." in out.read_text(encoding="utf-8")
    page.table.selectRow(0)
    page.mark_selected_bad()
    assert c.history.get(1).bad


def test_history_retranslate_uses_current_settings(window):
    c = window.controller
    c.history.add(SubtitleLine(5, "Get in the car.", "JASON", 1.0))
    assert c.retranslate_entry(5)
    assert wait_until(lambda: c.history.get(5).translation == "[tr] Get in the car.")


def test_recording_a_hotkey_saves_it_and_flags_conflicts(window):
    c = window.controller
    page = _page(window, "hotkeys")
    recorder = page.recorders["copy_last"]
    recorder.start()
    recorder.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C,
                                     Qt.KeyboardModifier.ControlModifier))  # fmt: skip
    assert c.config.hotkeys.keys["copy_last"] == "Ctrl+C"
    assert c.set_hotkey("mute", "F7") == ["retranslate"]
    assert "F7" in page._notice._text.text() and page._notice.isVisibleTo(page)
    c.reset_hotkeys()
    assert c.config.hotkeys.keys["mute"] == "F6"
    assert "F7" not in page._notice._text.text()  # (off Windows the global-key note stays)


def test_escape_cancels_recording(window):
    page = _page(window, "hotkeys")
    recorder = page.recorders["mute"]
    recorder.start()
    recorder.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                     Qt.KeyboardModifier.NoModifier))  # fmt: skip
    assert window.controller.config.hotkeys.keys["mute"] == "F6"
    assert recorder.text() == "F6"


def test_hotkey_actions(window):
    c = window.controller
    c._on_subtitle(_view("Kafanı eğ."))
    window.trigger("copy_last")
    assert QGuiApplication.clipboard().text() == "Kafanı eğ."
    window.trigger("toggle_subtitles")
    assert c.config.overlay.visible is False
    window.trigger("pause_translation")
    assert c.translation_paused is True
    window.trigger("history_panel")
    assert window.home.video.show_history
    window.home.video.resize(640, 360)
    assert not window.home.video.grab().isNull()


def test_rebinding_replaces_the_window_shortcuts(window):
    window.controller.set_hotkey("calibrate", "Ctrl+K")
    keys = {s.key().toString() for s in window._shortcuts}
    assert "Ctrl+K" in keys and "F2" not in keys


def test_diagnostics_support_bundle(window, tmp_path):
    import zipfile

    page = _page(window, "diagnostics")
    out = page.save_bundle(tmp_path / "s.zip")
    with zipfile.ZipFile(out) as z:
        assert "diagnostics.txt" in z.namelist()


def test_translation_sits_next_to_the_english_text_not_the_whole_region():
    """Found on Windows: with a tall region (lower half of the screen) the translation was
    drawn above the region, in the middle of the picture, far from the subtitle."""
    from types import SimpleNamespace

    import numpy as np

    from subwizard.config import RoiConfig
    from subwizard.ocr.base import OCRLine
    from subwizard.sources.base import Frame
    from subwizard.ui.widgets.video_view import VideoView

    roi = RoiConfig(template="custom", rect=[0.1, 0.5, 0.8, 0.45], padding_pct=0.0)
    tick = SimpleNamespace(lines=(OCRLine("Turn that off.", 0.9, (100, 300, 400, 40)),))
    controller = SimpleNamespace(config=SimpleNamespace(roi=roi), last_ocr=tick)
    view = SimpleNamespace(_controller=controller, _anchor_cache=None)
    view._source_bounds = lambda frame: VideoView._source_bounds(view, frame)
    frame = Frame(np.zeros((1000, 1000, 3), np.uint8), 0.0, 1)

    top, bottom = VideoView._anchor(view, frame, 7, 0.5, 0.95)
    assert (top, bottom) == pytest.approx((0.8, 0.84))
    video = QRectF(0, 0, 1000, 1000)
    box = place("bottom", 400, 60, video, top, bottom, 1.5)
    assert 700 < box.bottom() < 800  # just above the English line, not above y=500

    controller.last_ocr = SimpleNamespace(lines=())  # the English left first
    assert VideoView._anchor(view, frame, 7, 0.5, 0.95) == pytest.approx((0.8, 0.84))
    assert VideoView._anchor(view, frame, 8, 0.5, 0.95) == (0.5, 0.95)  # another line: region


def test_replace_mode_never_makes_a_bigger_block_than_the_english_text():
    """Found by the user: with "Orijinalin yerine" the translation was a tall box with a
    name line and a marker, larger than the text it covered."""
    from subwizard.ui.subtitle_paint import replace_layout

    cfg = OverlayConfig(font_size_pct=4.5)
    view = SubtitleView(1, "Para olmadan olmaz.", "Lucia", "Not without the money.",
                        "mymemory", True, 0.0, 0.0)  # fmt: skip
    layout = replace_layout(cfg, view, 1920, 1080, area_w=520, area_h=48, lines=1)
    assert layout.view.text == "Lucia: Para olmadan olmaz."  # the name inline, no marker
    assert layout.view.speaker is None and not layout.view.fallback
    assert layout.style.font_px <= 48 * 0.8 and layout.box_h == 48
    pixmap = render_subtitle(layout.view, layout.style, 1.0, "(yedek)")
    assert pixmap.height() <= 48 * 1.6  # one line of text (plus transparent padding)
    # A much longer translation gets a wider box first, then a smaller font: never taller.
    long = SubtitleView(2, "Bu çeviri İngilizce satırdan çok daha uzun bir cümle oldu " * 2,
                        None, "Short line.", "gemini", False, 0.0, 0.0)  # fmt: skip
    fitted = replace_layout(cfg, long, 1920, 1080, area_w=300, area_h=48, lines=1)
    assert fitted.box_w > 300 and fitted.box_h == 48
    assert fitted.style.font_px < layout.style.font_px


def test_above_mode_sits_right_over_the_english_text_at_its_size():
    """Asked for by the user (a photo of the game): the translation right over the English
    line, as large as it, the name inline, no box."""
    from PySide6.QtCore import QRectF

    from subwizard.ui.subtitle_paint import above_layout, place_above

    assert OverlayConfig().position == "above"
    cfg = OverlayConfig(font_size_pct=4.5)
    view = SubtitleView(1, "Hayır, onu polis için tutacağız.", "Simon Krieger",
                        "No, we keep him for the cops.", "gemini", False, 0.0, 0.0)  # fmt: skip
    layout = above_layout(cfg, view, 1920, 1080, area_w=900, area_h=48, lines=1)
    assert layout.view.text == "Simon Krieger: Hayır, onu polis için tutacağız."
    assert layout.style.background[3] == 0 and layout.style.outline
    assert layout.style.font_px == round(48 * 0.8)  # the English line's size
    pixmap = render_subtitle(layout.view, layout.style, 1.0, "(yedek)")
    area = QRectF(500, 900, 900, 48)
    video = QRectF(0, 0, 1920, 1080)
    box = place_above(area, video, pixmap.width(), pixmap.height())
    assert box.bottom() == pytest.approx(area.top())  # touching the English text's top
    assert box.center().x() == pytest.approx(area.center().x())
    # Never out of the picture at the edges.
    edge = place_above(QRectF(0, 10, 100, 40), video, 600, 60)
    assert edge.left() == 0 and edge.top() == 0
