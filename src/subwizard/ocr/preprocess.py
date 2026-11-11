"""Per-engine image preparation before OCR (SPEC 3.3). Qt-free.

RapidOCR's neural models want a natural image: light handling only, no thresholding.
Classic engines (Tesseract) want binarised, upscaled text. Both are expressed as a
PreprocessConfig so each engine gets its own defaults.
"""

from __future__ import annotations

import cv2
import numpy as np

from subwizard.config import PreprocessConfig

UPSCALE_FACTOR = 2


def white_text_mask(image: np.ndarray, min_value: int, max_saturation: int) -> np.ndarray:
    """Keep bright, unsaturated pixels (white subtitle text), black out the rest.
    Returns a single-channel image with dark text on white, the form OCR reads best."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, min_value), (180, max_saturation, 255))
    return cv2.bitwise_not(mask)


def preprocess(image: np.ndarray, cfg: PreprocessConfig) -> np.ndarray:
    if image.size == 0:
        return image
    if cfg.white_text_mask:
        out = white_text_mask(image, cfg.mask_min_value, cfg.mask_max_saturation)
    elif cfg.grayscale:
        out = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        out = image
    if cfg.contrast_stretch and out.ndim == 2:
        out = cv2.normalize(out, None, 0, 255, cv2.NORM_MINMAX)
    if 0 < out.shape[0] < cfg.upscale_below_px:
        out = cv2.resize(out, None, fx=UPSCALE_FACTOR, fy=UPSCALE_FACTOR,
                         interpolation=cv2.INTER_CUBIC)  # fmt: skip
    if cfg.threshold != "none" and out.ndim == 2 and not cfg.white_text_mask:
        if cfg.threshold == "otsu":
            _, out = cv2.threshold(out, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            out = cv2.adaptiveThreshold(out, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY, 31, 10)  # fmt: skip
    return np.ascontiguousarray(out)
