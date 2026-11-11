"""RapidOCR engine (PP-OCR models on onnxruntime, bundled with the package). Qt-free.

`rapidocr` 3.x is the maintained successor of the `rapidocr-onnxruntime` package
named in SPEC 1; the old one pins the non-headless OpenCV, which conflicts with ours.
"""

from __future__ import annotations

import logging

import numpy as np

from subwizard.ocr.base import BaseOCR, OCRLine, OCRResult

log = logging.getLogger(__name__)

SAME_ROW_OVERLAP = 0.5  # boxes overlapping vertically this much are one subtitle line


def _box_to_rect(points: np.ndarray, scale: float) -> tuple[int, int, int, int]:
    xs, ys = points[:, 0] / scale, points[:, 1] / scale
    x0, y0 = int(xs.min()), int(ys.min())
    return x0, y0, int(xs.max()) - x0, int(ys.max()) - y0


def group_rows(items: list[tuple[str, float, tuple[int, int, int, int]]]) -> list[OCRLine]:
    """Merge detected boxes into lines: boxes sharing a row are joined left to right,
    rows are returned top to bottom. Detection sometimes splits one line in two."""
    rows: list[list[tuple[str, float, tuple[int, int, int, int]]]] = []
    for item in sorted(items, key=lambda i: i[2][1]):
        _, _, (_, y, _, h) = item
        for row in rows:
            ry, rh = row[0][2][1], row[0][2][3]
            overlap = min(y + h, ry + rh) - max(y, ry)
            if overlap >= SAME_ROW_OVERLAP * min(h, rh):
                row.append(item)
                break
        else:
            rows.append([item])

    lines = []
    for row in rows:
        row.sort(key=lambda i: i[2][0])
        x0 = min(i[2][0] for i in row)
        y0 = min(i[2][1] for i in row)
        x1 = max(i[2][0] + i[2][2] for i in row)
        y1 = max(i[2][1] + i[2][3] for i in row)
        text = " ".join(i[0] for i in row)
        confidence = sum(i[1] for i in row) / len(row)
        lines.append(OCRLine(text, confidence, (x0, y0, x1 - x0, y1 - y0)))
    return lines


class RapidOCREngine(BaseOCR):
    name = "rapid"

    def __init__(self, det_max_side: int, threads: int = 0) -> None:
        from rapidocr import RapidOCR  # lazy: loads onnxruntime and the models

        params = {
            "Global.log_level": "error",
            # The default scales the *short* side up to 736 px, which turns a thin
            # subtitle strip into a ~6000 px wide image (~750 ms per call). Limiting
            # the long side instead gives ~120 ms with the same accuracy.
            "Det.limit_type": "max",
            "Det.limit_side_len": det_max_side,
            "Global.use_cls": False,  # subtitles are never upside down
        }
        if threads > 0:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = threads
        self._engine = RapidOCR(params=params)

    def recognize(self, image: np.ndarray, scale: float = 1.0) -> OCRResult:
        """`scale` is how much `image` was enlarged by preprocessing; boxes are
        returned in the coordinates of the original crop."""
        if image.size == 0:
            return OCRResult()
        out = self._engine(image, use_cls=False)
        if out.txts is None or out.boxes is None:
            return OCRResult()
        items = [
            (text, float(score), _box_to_rect(np.asarray(box), scale))
            for text, score, box in zip(out.txts, out.scores, out.boxes, strict=True)
            if text.strip()
        ]
        return OCRResult(tuple(group_rows(items)))
