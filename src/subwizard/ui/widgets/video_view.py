"""VideoView: draws the latest frame, plus the F9 performance overlay (SPEC 2, 3.7).

Repaints are driven by the source thread's "new frame" signal, never by a timer,
and the paint path does no OCR, I/O or locking beyond reading the latest-only slot.
"""

from __future__ import annotations

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
from subwizard.ocr.roi import Rect
from subwizard.sources.base import Frame
from subwizard.ui.metrics_view import DASH, GRADE_COLORS, metric_rows
from subwizard.ui.subtitle_paint import (
    REPLACE_MIN_FONT_PX,
    Style,
    place,
    render_subtitle,
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
REPLACE_FILL = 0.8  # replace mode: font height as a share of one English line's height
REPLACE_PAD_PX = 4  # cover a little more than the OCR box (frame pixels)
HISTORY_FONT_PX = 14
HISTORY_WIDTH_RATIO = 0.45
HISTORY_MAX_WIDTH = 720
HISTORY_SOURCE = QColor(170, 170, 170)


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
        self._subtitle_key: tuple | None = None
        self._subtitle_pixmap: QPixmap | None = None
        self._replace_cache: tuple | None = None
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
        controller.subtitleChanged.connect(self.update)
        controller.overlayChanged.connect(self.update)

    def set_placeholder(self, text: str) -> None:
        """Shown centered while there is no frame (stopped, connecting, error)."""
        self._placeholder = text
        self.update()

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
        cfg, view = c.config.overlay, c.subtitle
        if not cfg.visible or view is None:
            return
        dpr = self.devicePixelRatioF()
        roi = c.config.roi
        cover = self._replace_area(frame, target) if cfg.position == "replace" else None
        if cover is not None:
            area, color, lines = cover
            font_px = max(REPLACE_MIN_FONT_PX,
                          min(round(target.height() * cfg.font_size_pct / 100),
                              round(area.height() / max(1, lines) * REPLACE_FILL)))  # fmt: skip
            bg = (*color, 255) if cfg.auto_bg else None
            style = Style.from_config(cfg, round(target.width()), round(target.height()), bg,
                                      font_px, max(1, round(area.width())))  # fmt: skip
        else:
            style = Style.from_config(cfg, round(target.width()), round(target.height()))
        key = (view.line_id, view.text, style, dpr)
        if key != self._subtitle_key:  # re-rendered only when text, style or size change
            self._subtitle_key = key
            self._subtitle_pixmap = render_subtitle(view, style, dpr, t("subtitle.fallback"))
        pixmap = self._subtitle_pixmap
        w, h = pixmap.width() / dpr, pixmap.height() / dpr
        if cover is not None:
            area, color, _ = cover
            if cfg.auto_bg:
                painter.fillRect(area, QColor(*color))  # hide the English text completely
            box = QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)
        else:
            position = "bottom" if cfg.position == "replace" else cfg.position
            box = place(position, w, h, target, roi.rect[1], roi.rect[1] + roi.rect[3],
                        cfg.margin_pct)  # fmt: skip
        painter.drawPixmap(box.topLeft(), pixmap)

    def _replace_area(self, frame: Frame, target: QRectF):  # noqa: ANN202
        """(widget rect over the English text, its surrounding color, line count) from the
        last OCR result; None when there are no boxes (falls back to `bottom`)."""
        c = self._controller
        tick = c.last_ocr
        if tick is None or not tick.lines:
            return None
        roi = c.config.roi
        height, width = frame.image.shape[:2]
        x0, y0, _, _ = Rect(*roi.rect).padded(roi.padding_pct).to_pixels(width, height)
        bounds = text_bounds([line.box for line in tick.lines], (x0, y0))
        if bounds is None:
            return None
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
        return area, color, len(tick.lines)

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
