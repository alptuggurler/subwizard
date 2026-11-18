"""VideoView: draws the latest frame, plus the F9 performance overlay (SPEC 2, 3.7).

Repaints are driven by the source thread's "new frame" signal, never by a timer,
and the paint path does no OCR, I/O or locking beyond reading the latest-only slot.
"""

from __future__ import annotations

import math
from dataclasses import replace

from PySide6.QtCore import QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from subwizard.controller import AppController
from subwizard.core.metrics import M, Snapshot
from subwizard.i18n import t
from subwizard.ocr.roi import Rect, config_layout, to_frame
from subwizard.pipeline.subtitle_stack import SubtitleStack
from subwizard.sources.base import Frame
from subwizard.ui.metrics_view import DASH, GRADE_COLORS, metric_rows
from subwizard.ui.subtitle_paint import (
    Style,
    above_layout,
    place,
    place_above,
    paint_replace,
    render_subtitle,
    replace_layout,
    surrounding_color,
    text_bounds,
)
from subwizard.utils import timing

OVERLAY_MARGIN = 12
OVERLAY_PADDING = 10
OVERLAY_LINE_SPACING = 4
OVERLAY_FONT_PX = 13
OVERLAY_BACKGROUND = QColor(0, 0, 0, 170)
OVERLAY_TEXT = QColor(235, 235, 235)
OVERLAY_RADIUS = 8
PLACEHOLDER_COLOR = QColor(150, 150, 150)
REGION_COLOR = QColor(0, 200, 255)  # same colors as the region editor
PADDING_COLOR = QColor(0, 200, 255, 160)
REPLACE_PAD_PX = 4  # cover a little more than the OCR box (frame pixels)
HISTORY_FONT_PX = 14
HISTORY_WIDTH_RATIO = 0.45
HISTORY_MAX_WIDTH = 720
HISTORY_SOURCE = QColor(170, 170, 170)
ANIMATION_TICK_MS = 16  # repaint period while a subtitle fades or glides
GLIDE_TAU_S = 0.06  # the subtitle box eases to a new height instead of jumping
GLIDE_DONE_PX = 0.5
STACK_GAP_EM = 0.12  # gap between the current line and the older one above it
TOAST_MS = 1800  # "Çeviri açık / kapalı" after the pause key
TOAST_FONT_PX = 22
TOAST_PADDING = 14
TOAST_TOP_RATIO = 0.08  # from the top: away from subtitles at the bottom
TOAST_ON = QColor(110, 220, 130)
TOAST_OFF = QColor(255, 170, 90)


class VideoView(QWidget):
    # A plain raster widget, not QOpenGLWidget: measured at 60 FPS / ~4 ms frame age
    # for 1080p60, and it avoids forcing OpenGL composition onto the Mica window.
    escapePressed = Signal()
    doubleClicked = Signal()

    def __init__(
        self, controller: AppController, measure: bool = True, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self._controller = controller
        self._measure = measure  # only the main view reports render metrics, not previews
        self._last_painted = -1
        self._snapshot: Snapshot | None = None
        self._placeholder = ""
        self._pixmaps: dict[tuple, QPixmap] = {}  # rendered subtitle lines, by text and style
        overlay = controller.config.overlay
        self._stack = SubtitleStack(overlay.stack_lines, overlay.fade_ms)
        self._stack_view = None  # the controller's subtitle the stack was last told about
        self._glide: tuple[float, float] | None = None  # (bottom edge now, time)
        self._gliding = False
        self._fit_cache: tuple | None = None  # replace mode: (key, ReplaceLayout)
        self._last_cover: tuple | None = None  # replace mode: where the line fades out
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(ANIMATION_TICK_MS)
        self._anim_timer.timeout.connect(self._animate)
        self._replace_cache: tuple | None = None
        self._anchor_cache: tuple[int, tuple[float, float]] | None = None
        self.show_history = False  # F12
        self.fullscreen = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(controller.config.ui.cursor_hide_ms)
        self._cursor_timer.timeout.connect(self._hide_cursor)

        controller.frameReady.connect(self.update)
        controller.runningChanged.connect(self.update)
        controller.perfOverlayChanged.connect(self.update)
        controller.metricsUpdated.connect(self._on_metrics)
        controller.subtitleChanged.connect(self._on_subtitle)
        controller.overlayChanged.connect(self._on_overlay)

        self._toast: tuple[str, QColor] | None = None
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(TOAST_MS)
        self._toast_timer.timeout.connect(self._end_toast)
        controller.pauseChanged.connect(self._on_pause)

    # --- subtitle animation ------------------------------------------------------
    def _on_subtitle(self, _view: object = None) -> None:
        self._anim_timer.start()  # frames may not be arriving (game overlay, paused source)
        self.update()

    def _on_overlay(self) -> None:
        overlay = self._controller.config.overlay
        self._stack.configure(overlay.stack_lines, overlay.fade_ms)
        self.update()

    def _animate(self) -> None:
        self.update()
        if not self._stack.animating(timing.now()) and not self._gliding:
            self._anim_timer.stop()

    def _sync_stack(self, now: float) -> None:
        """Tell the stack what the board shows now (checked at paint time, so it holds
        even when the subtitle was set without the signal)."""
        view = self._controller.subtitle
        if view == self._stack_view:
            return
        self._stack_view = view
        if view is None:
            self._stack.clear(now)
        else:
            self._stack.show(view, now)

    def _line_pixmap(self, view, style: Style, dpr: float, used: dict) -> QPixmap:  # noqa: ANN001
        key = (view.line_id, view.text, view.speaker, view.fallback, view.source, style, dpr)
        pixmap = self._pixmaps.get(key)
        if pixmap is None:  # rendered only when text, style or size change
            pixmap = render_subtitle(view, style, dpr, t("subtitle.fallback"))
        used[key] = pixmap
        return pixmap

    def _eased_bottom(self, target: float, now: float, fresh: bool) -> float:
        """The box's bottom edge, moving toward `target` over a few frames."""
        if fresh or self._glide is None or self._stack.fade_s <= 0:
            self._glide, self._gliding = (target, now), False
            return target
        value, at = self._glide
        value += (target - value) * (1.0 - math.exp(-max(0.0, now - at) / GLIDE_TAU_S))
        self._gliding = abs(target - value) > GLIDE_DONE_PX
        if not self._gliding:
            value = target
        self._glide = (value, now)
        return value

    def set_placeholder(self, text: str) -> None:
        """Shown centered while there is no frame (stopped, connecting, error)."""
        self._placeholder = text
        self.update()

    def _on_pause(self, paused: bool) -> None:
        if paused:
            self.show_toast(t("toast.translation_off"), TOAST_OFF)
        else:
            self.show_toast(t("toast.translation_on"), TOAST_ON)

    def show_toast(self, text: str, color: QColor = OVERLAY_TEXT) -> None:
        """A short notice at the top center (pause key feedback); fades out on a timer."""
        self._toast = (text, color)
        self._toast_timer.start()
        self.update()

    def _end_toast(self) -> None:
        self._toast = None
        self.update()

    def _draw_toast(self, painter: QPainter) -> None:
        if self._toast is None:
            return
        text, color = self._toast
        font = QFont(painter.font())
        font.setPixelSize(TOAST_FONT_PX)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        width = fm.horizontalAdvance(text) + 2 * TOAST_PADDING
        height = fm.height() + TOAST_PADDING
        box = QRectF((self.width() - width) / 2, self.height() * TOAST_TOP_RATIO, width, height)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(OVERLAY_BACKGROUND)
        painter.drawRoundedRect(box, OVERLAY_RADIUS, OVERLAY_RADIUS)
        painter.setPen(color)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _on_metrics(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        if self._controller.config.ui.perf_overlay != "off" and self.isVisible():
            self.update()  # keeps the numbers fresh even when no frames arrive

    # --- painting ---------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        frame = self._controller.latest_frame()
        if frame is not None:
            self._draw_frame(painter, frame)
        elif self._placeholder:
            painter.setPen(PLACEHOLDER_COLOR)
            painter.drawText(
                self.rect().adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._placeholder,
            )
        level = self._controller.config.ui.perf_overlay
        if self._measure and level != "off" and self._snapshot is not None:
            self._draw_overlay(painter, level == "compact")
        if self.show_history:
            self._draw_history(painter)
        self._draw_toast(painter)
        painter.end()

    def _draw_frame(self, painter: QPainter, frame: Frame) -> None:
        image = frame.image
        height, width = image.shape[:2]
        # Wraps the numpy buffer without copying; `frame` keeps it alive for this paint.
        qimage = QImage(image.data, width, height, width * 3, QImage.Format.Format_BGR888)
        scale = min(self.width() / width, self.height() / height)
        target = QRectF(
            (self.width() - width * scale) / 2,
            (self.height() - height * scale) / 2,
            width * scale,
            height * scale,
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, qimage)
        if self._measure and self._controller.config.ui.perf_overlay == "detailed":
            self._draw_region(painter, target)
        self._draw_subtitle(painter, target, frame)

        if self._measure and frame.index != self._last_painted:
            # Count only frames that are new on screen: repainting the same frame
            # (resize, overlay refresh) is not a rendered frame.
            self._last_painted = frame.index
            metrics = self._controller.metrics
            metrics.tick(M.RENDER_FPS)
            metrics.observe(M.RENDER_FRAME_AGE_MS, timing.ms_since(frame.timestamp))

    def _draw_subtitle(self, painter: QPainter, target: QRectF, frame: Frame) -> None:
        c = self._controller
        cfg = c.config.overlay
        now = timing.now()
        was_empty = not self._pixmaps
        self._sync_stack(now)
        items = self._stack.items(now)
        if not cfg.visible or not items or c.config.display.mode == "web":
            self._pixmaps = {}
            return  # Mode B: the game is on the TV; the translation goes to the web page
        dpr = self.devicePixelRatioF()
        roi = c.config.roi
        used: dict[tuple, QPixmap] = {}
        main = items[-1] if items[-1].scale == 1.0 else None  # shown, or fading out
        older = items[:-1] if main is not None else items
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cover = None
        if main is not None and cfg.position in ("replace", "above"):
            line_id = main.view.line_id
            cover = self._replace_area(frame, target) if main.current else None
            if cover is not None:
                self._last_cover = (line_id, cover)
            elif self._last_cover is not None and (
                    not main.current or self._last_cover[0] == line_id):  # fmt: skip
                # The English left first, or the translation fades out: it stays where
                # it was instead of jumping to another place.
                cover = self._last_cover[1]
        if cover is not None and cfg.position == "replace":
            self._draw_replace(painter, target, cover, main, dpr, used)
        else:
            style = Style.from_config(cfg, round(target.width()), round(target.height()))
            position = "bottom" if cfg.position in ("replace", "above") else cfg.position
            top_edge: float | None = None
            center_x = target.center().x()
            if cover is not None:  # above: over the English text, at its size
                area, _color, lines = cover
                key = ("above", main.view, cfg.model_dump_json(), round(target.width()),
                       round(target.height()), round(area.width() / 4),
                       round(area.height() / 4), lines)  # fmt: skip
                if self._fit_cache is None or self._fit_cache[0] != key:
                    self._fit_cache = (key, above_layout(cfg, main.view, target.width(),
                                                         target.height(), area.width(),
                                                         area.height(), lines))  # fmt: skip
                layout = self._fit_cache[1]
                style = layout.style
                pixmap = self._line_pixmap(layout.view, style, dpr, used)
                w, h = pixmap.width() / dpr, pixmap.height() / dpr
                box = place_above(area, target, w, h)
                box.moveBottom(self._eased_bottom(box.bottom(), now, was_empty))
                painter.setOpacity(main.opacity)
                painter.drawPixmap(box.topLeft(), pixmap)
                top_edge, center_x = box.top(), box.center().x()
            elif main is not None:
                pixmap = self._line_pixmap(main.view, style, dpr, used)
                w, h = pixmap.width() / dpr, pixmap.height() / dpr
                top, bottom = self._anchor(frame, main.view.line_id, roi.rect[1],
                                           roi.rect[1] + roi.rect[3])  # fmt: skip
                box = place(position, w, h, target, top, bottom, cfg.margin_pct)
                if position != "top":  # the bottom edge glides; the box grows upward
                    box.moveBottom(self._eased_bottom(box.bottom(), now, was_empty))
                painter.setOpacity(main.opacity)
                painter.drawPixmap(box.topLeft(), pixmap)
                top_edge = box.top()
            elif self._glide is not None:
                top_edge = self._glide[0]  # only older lines are left: where the last one was
            if position != "top" and top_edge is not None:
                gap = style.font_px * STACK_GAP_EM
                for item in reversed(older):  # the newest older line is nearest
                    # Text only: an older line must not take the room of a full box.
                    compact = replace(item.view, speaker=None, fallback=False)
                    pixmap = self._line_pixmap(compact, replace(style, show_source=False),
                                               dpr, used)  # fmt: skip
                    w = pixmap.width() / dpr * item.scale
                    h = pixmap.height() / dpr * item.scale
                    top_edge -= gap + h
                    painter.setOpacity(item.opacity)
                    painter.drawPixmap(QRectF(center_x - w / 2, top_edge, w, h),
                                       pixmap, QRectF(pixmap.rect()))  # fmt: skip
        painter.restore()
        self._pixmaps = used
        if (self._stack.animating(now) or self._gliding) and not self._anim_timer.isActive():
            self._anim_timer.start()

    def _draw_replace(self, painter: QPainter, target: QRectF, cover: tuple, main,  # noqa: ANN001
                      dpr: float, used: dict) -> None:  # fmt: skip
        cfg = self._controller.config.overlay
        area, color, lines = cover
        view = main.view
        key = (view, cfg.model_dump_json(), round(target.width()), round(target.height()),
               round(area.width() / 4), round(area.height() / 4), lines)  # fmt: skip
        if self._fit_cache is None or self._fit_cache[0] != key:  # not measured every frame
            self._fit_cache = (key, replace_layout(cfg, view, target.width(), target.height(),
                                                   area.width(), area.height(), lines))  # fmt: skip
        layout = self._fit_cache[1]
        pixmap = self._line_pixmap(layout.view, layout.style, dpr, used)
        painter.setOpacity(main.opacity)
        paint_replace(painter, cfg, area, color, layout, pixmap, dpr)

    def _source_bounds(self, frame: Frame) -> tuple[int, int, int, int] | None:
        """Frame pixels (x0, y0, x1, y1) around the English text of the last OCR result,
        inside the subtitle region only (not an extra region, e.g. a name at the top)."""
        c = self._controller
        tick = c.last_ocr
        if tick is None or not tick.lines:
            return None
        height, width = frame.image.shape[:2]
        strips = config_layout(c.config.roi, width, height)
        main = strips[0]
        boxes = [b for b in (to_frame(line.box, strips) for line in tick.lines)
                 if main.y0 <= b[1] + b[3] / 2 < main.y1]  # fmt: skip
        return text_bounds(boxes, (0, 0))

    def _anchor(self, frame: Frame, line_id: int, roi_top: float, roi_bottom: float
                ) -> tuple[float, float]:  # fmt: skip
        """Top and bottom (share of the video height) of what the translation sits next to:
        the English text itself, so a tall region (lower half of the screen) does not push
        the translation to the middle of the picture. Kept per line: the translation does
        not jump when the English leaves first. The region when OCR gave no boxes."""
        bounds = self._source_bounds(frame)
        if bounds is not None:
            height = frame.image.shape[0]
            self._anchor_cache = (line_id, (bounds[1] / height, bounds[3] / height))
        elif self._anchor_cache is None or self._anchor_cache[0] != line_id:
            return roi_top, roi_bottom
        return self._anchor_cache[1]

    def _replace_area(self, frame: Frame, target: QRectF):  # noqa: ANN202
        """(widget rect over the English text, its surrounding color, line count) from the
        last OCR result; None when there are no boxes (falls back to `bottom`)."""
        c = self._controller
        bounds = self._source_bounds(frame)
        if bounds is None:
            return None
        width = frame.image.shape[1]
        line_id = c.subtitle.line_id if c.subtitle else -1
        if self._replace_cache is None or self._replace_cache[0] != (line_id, bounds):
            # Sampled once per line and position, not every frame.
            self._replace_cache = ((line_id, bounds), surrounding_color(frame.image, bounds))
        color = self._replace_cache[1]
        scale = target.width() / width
        pad = REPLACE_PAD_PX * scale
        area = QRectF(target.left() + bounds[0] * scale - pad, target.top() + bounds[1] * scale - pad,
                      (bounds[2] - bounds[0]) * scale + 2 * pad,
                      (bounds[3] - bounds[1]) * scale + 2 * pad)  # fmt: skip
        return area, color, len(c.last_ocr.lines)

    def _draw_history(self, painter: QPainter) -> None:
        """F12: the last few lines, for subtitles that went by too fast."""
        c = self._controller
        entries = c.history.last(c.config.overlay.history_lines)
        if not entries:
            return
        font = QFont(painter.font())
        font.setPixelSize(HISTORY_FONT_PX)
        painter.setFont(font)
        fm = painter.fontMetrics()
        width = min(self.width() * HISTORY_WIDTH_RATIO, HISTORY_MAX_WIDTH)
        lines: list[tuple[str, QColor]] = []
        for entry in entries:
            lines.append((f"{entry.clock}  {entry.source}", HISTORY_SOURCE))
            lines.append((f"        {entry.translation or DASH}", OVERLAY_TEXT))
        line_h = fm.height() + OVERLAY_LINE_SPACING
        box = QRectF(self.width() - width - OVERLAY_MARGIN, OVERLAY_MARGIN, width,
                     line_h * len(lines) + 2 * OVERLAY_PADDING)  # fmt: skip
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(OVERLAY_BACKGROUND)
        painter.drawRoundedRect(box, OVERLAY_RADIUS, OVERLAY_RADIUS)
        y = box.top() + OVERLAY_PADDING + fm.ascent()
        for text, color in lines:
            painter.setPen(color)
            painter.drawText(int(box.left() + OVERLAY_PADDING), int(y),
                             fm.elidedText(text, Qt.TextElideMode.ElideRight,
                                           int(width - 2 * OVERLAY_PADDING)))  # fmt: skip
            y += line_h

    def _draw_region(self, painter: QPainter, target: QRectF) -> None:
        """Debug view: where OCR is looking (solid) and what it really reads (dashed)."""
        roi = self._controller.config.roi
        region = Rect(*roi.rect)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for rect, pen in (
            (region, QPen(REGION_COLOR, 2)),
            (region.padded(roi.padding_pct), QPen(PADDING_COLOR, 1, Qt.PenStyle.DashLine)),
        ):
            painter.setPen(pen)
            painter.drawRect(QRectF(target.left() + rect.x * target.width(),
                                    target.top() + rect.y * target.height(),
                                    rect.w * target.width(), rect.h * target.height()))  # fmt: skip

    def _draw_overlay(self, painter: QPainter, compact: bool) -> None:
        rows = [
            row
            for row in metric_rows(self._snapshot, self._controller.config)
            if row.compact or not compact
        ]
        font = QFont(painter.font())
        font.setPixelSize(OVERLAY_FONT_PX)
        painter.setFont(font)
        fm = painter.fontMetrics()
        labels = [t(row.label) for row in rows]
        label_w = max(fm.horizontalAdvance(text) for text in labels) + 2 * OVERLAY_PADDING
        value_w = max(fm.horizontalAdvance(row.value) for row in rows)
        line_h = fm.height() + OVERLAY_LINE_SPACING
        box = QRectF(
            OVERLAY_MARGIN,
            OVERLAY_MARGIN,
            label_w + value_w + 2 * OVERLAY_PADDING,
            line_h * len(rows) + 2 * OVERLAY_PADDING - OVERLAY_LINE_SPACING,
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(OVERLAY_BACKGROUND)
        painter.drawRoundedRect(box, OVERLAY_RADIUS, OVERLAY_RADIUS)

        y = box.top() + OVERLAY_PADDING + fm.ascent()
        x = box.left() + OVERLAY_PADDING
        for row, label in zip(rows, labels, strict=True):
            painter.setPen(OVERLAY_TEXT)
            painter.drawText(int(x), int(y), label)
            color = GRADE_COLORS[row.grade]
            painter.setPen(QColor(color) if color else OVERLAY_TEXT)
            painter.drawText(int(x + label_w), int(y), row.value)
            y += line_h

    # --- fullscreen niceties -------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.escapePressed.emit()
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.doubleClicked.emit()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.unsetCursor()
        if self.fullscreen:
            self._cursor_timer.start()
        super().mouseMoveEvent(event)

    def enter_fullscreen(self, screen: QRect) -> None:
        """Detach into a borderless window covering `screen` (SPEC 3.7: frameless
        fullscreen). A plain screen-sized frameless window behaves the same on every
        platform, unlike native fullscreen modes."""
        self.fullscreen = True
        self.setParent(None)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setGeometry(screen)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._cursor_timer.start()

    def leave_fullscreen(self) -> None:
        """The caller re-inserts the widget into its layout afterwards."""
        self.fullscreen = False
        self._cursor_timer.stop()
        self.unsetCursor()
        self.setWindowFlags(Qt.WindowType.Widget)

    def _hide_cursor(self) -> None:
        if self.fullscreen:
            self.setCursor(Qt.CursorShape.BlankCursor)
