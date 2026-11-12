"""RoiEditor: the video with the subtitle region drawn on top; drag to pick a new one
(SPEC 3.3). Used by the Alan & OCR page (live or frozen) and by F2 calibration.

Drawn on top of the frame:
- the configured region (solid), the padded area OCR really reads (dashed),
- the boxes of the lines OCR found last time,
- the region being dragged (filled), in normalised frame coordinates.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from subwizard.controller import AppController
from subwizard.ocr.roi import Rect, config_layout, to_frame
from subwizard.sources.base import Frame
from subwizard.ui.widgets.video_view import PADDING_COLOR, REGION_COLOR

LINE_BOX_COLOR = QColor(80, 220, 120)
PENDING_COLOR = QColor(255, 196, 0)
EXTRA_COLOR = QColor(90, 170, 255)  # more regions read together with the main one
EXCLUDE_COLOR = QColor(235, 80, 80)  # blacked out before OCR
EXCLUDE_FILL = QColor(235, 80, 80, 70)
PENDING_FILL = QColor(255, 196, 0, 50)
SHADE = QColor(0, 0, 0, 90)  # dims everything outside the region
HINT_BACKGROUND = QColor(0, 0, 0, 170)
HINT_TEXT = QColor(240, 240, 240)
PLACEHOLDER_COLOR = QColor(150, 150, 150)
REGION_PEN_WIDTH = 2
HINT_MARGIN = 16
HINT_PADDING = 10


def frame_to_qimage(image) -> QImage:  # noqa: ANN001 - numpy array, BGR or grayscale
    """Wrap a numpy image (no copy: the caller keeps the array alive while painting)."""
    height, width = image.shape[:2]
    if image.ndim == 2:
        return QImage(image.data, width, height, image.strides[0], QImage.Format.Format_Grayscale8)
    return QImage(image.data, width, height, image.strides[0], QImage.Format.Format_BGR888)


class RoiEditor(QWidget):
    rectChosen = Signal(object)  # Rect, normalised

    def __init__(
        self, controller: AppController, *, commit_on_release: bool = True,
        parent: QWidget | None = None,
    ):  # fmt: skip
        super().__init__(parent)
        self._controller = controller
        self._commit_on_release = commit_on_release
        self._frozen: Frame | None = None
        self._target = QRectF()  # where the frame is drawn, in widget pixels
        self._drag_from: QPointF | None = None
        self._pending: Rect | None = None
        self._hint = ""
        self._placeholder = ""
        self.setMouseTracking(False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        controller.frameReady.connect(self._on_frame)
        controller.settingsChanged.connect(self.update)
        controller.ocrTick.connect(self._on_ocr)

    # --- state ------------------------------------------------------------------------
    def frame(self) -> Frame | None:
        return self._frozen if self._frozen is not None else self._controller.latest_frame()

    @property
    def frozen(self) -> bool:
        return self._frozen is not None

    def freeze(self, frame: Frame | None) -> None:
        """Keep showing `frame` (a private copy) instead of the live picture; None = live."""
        self._frozen = None if frame is None else Frame(frame.image.copy(), frame.timestamp, frame.index)
        self.update()

    @property
    def pending(self) -> Rect | None:
        return self._pending

    def set_pending(self, rect: Rect | None) -> None:
        self._pending = rect
        self.update()

    def set_hint(self, text: str) -> None:
        self._hint = text
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def _on_frame(self) -> None:
        if self._frozen is None and self.isVisible():
            self.update()

    def _on_ocr(self, _tick: object) -> None:
        if self.isVisible():
            self.update()

    # --- geometry -----------------------------------------------------------------------
    def _to_norm(self, pos: QPointF) -> tuple[float, float]:
        t = self._target
        if t.width() <= 0 or t.height() <= 0:
            return 0.0, 0.0
        return (pos.x() - t.left()) / t.width(), (pos.y() - t.top()) / t.height()

    def _to_widget(self, rect: Rect) -> QRectF:
        t = self._target
        return QRectF(t.left() + rect.x * t.width(), t.top() + rect.y * t.height(),
                      rect.w * t.width(), rect.h * t.height())  # fmt: skip

    # --- mouse --------------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.frame() is not None:
            self._drag_from = event.position()
            self._pending = None
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_from is not None:
            self._pending = Rect.from_points(*self._to_norm(self._drag_from),
                                             *self._to_norm(event.position()))  # fmt: skip
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_from is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_from = None
        rect = self._pending
        if rect is None or not rect.usable:
            self._pending = None  # a click, not a drag
        elif self._commit_on_release:
            self._pending = None
            self.rectChosen.emit(rect)
        self.update()

    # --- painting -----------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        frame = self.frame()
        if frame is None:
            self._target = QRectF()
            if self._placeholder:
                painter.setPen(PLACEHOLDER_COLOR)
                painter.drawText(self.rect().adjusted(24, 24, -24, -24),
                                 Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                 self._placeholder)  # fmt: skip
            painter.end()
            return

        height, width = frame.image.shape[:2]
        scale = min(self.width() / width, self.height() / height)
        self._target = QRectF((self.width() - width * scale) / 2,
                              (self.height() - height * scale) / 2,
                              width * scale, height * scale)  # fmt: skip
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(self._target, frame_to_qimage(frame.image))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        roi = self._controller.config.roi
        region = Rect(*roi.rect)
        padded = region.padded(roi.padding_pct)
        self._draw_shade(painter, self._to_widget(padded))
        for extra in roi.extra_rects:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(EXTRA_COLOR, REGION_PEN_WIDTH, Qt.PenStyle.DashLine))
            painter.drawRect(self._to_widget(Rect(*extra)))
        for excluded in roi.exclude_rects:
            painter.setPen(QPen(EXCLUDE_COLOR, REGION_PEN_WIDTH))
            painter.setBrush(EXCLUDE_FILL)
            painter.drawRect(self._to_widget(Rect(*excluded)))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(REGION_COLOR, REGION_PEN_WIDTH))
        painter.drawRect(self._to_widget(region))
        painter.setPen(QPen(PADDING_COLOR, 1, Qt.PenStyle.DashLine))
        painter.drawRect(self._to_widget(padded))
        self._draw_line_boxes(painter, width, height, scale)

        if self._pending is not None:
            painter.setPen(QPen(PENDING_COLOR, REGION_PEN_WIDTH))
            painter.setBrush(PENDING_FILL)
            painter.drawRect(self._to_widget(self._pending))
        if self._hint:
            self._draw_hint(painter)
        painter.end()

    def _draw_shade(self, painter: QPainter, keep: QRectF) -> None:
        t = self._target
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(SHADE)
        for part in (
            QRectF(t.left(), t.top(), t.width(), keep.top() - t.top()),
            QRectF(t.left(), keep.bottom(), t.width(), t.bottom() - keep.bottom()),
            QRectF(t.left(), keep.top(), keep.left() - t.left(), keep.height()),
            QRectF(keep.right(), keep.top(), t.right() - keep.right(), keep.height()),
        ):
            if part.width() > 0 and part.height() > 0:
                painter.drawRect(part)

    def _draw_line_boxes(self, painter: QPainter, width: int, height: int, scale: float) -> None:
        tick = self._controller.last_ocr
        if tick is None or self._drag_from is not None:
            return
        # Line boxes are in OCR-image pixels; `to_frame` undoes the region stacking.
        strips = config_layout(self._controller.config.roi, width, height)
        painter.setPen(QPen(LINE_BOX_COLOR, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for line in tick.lines:
            x, y, w, h = to_frame(line.box, strips)
            painter.drawRect(QRectF(self._target.left() + x * scale,
                                    self._target.top() + y * scale,
                                    w * scale, h * scale))  # fmt: skip

    def _draw_hint(self, painter: QPainter) -> None:
        fm = painter.fontMetrics()
        lines = self._hint.splitlines()
        text_w = max(fm.horizontalAdvance(line) for line in lines)
        box = QRectF(HINT_MARGIN, HINT_MARGIN, text_w + 2 * HINT_PADDING,
                     fm.height() * len(lines) + 2 * HINT_PADDING)  # fmt: skip
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(HINT_BACKGROUND)
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(HINT_TEXT)
        y = box.top() + HINT_PADDING + fm.ascent()
        for line in lines:
            painter.drawText(int(box.left() + HINT_PADDING), int(y), line)
            y += fm.height()
