"""M7 Windows OCR: the platform-free parts (image conversion, boxes) and the fallback.
The WinRT calls themselves only run on Windows."""

import logging

import numpy as np

from subwizard.config import AppConfig
from subwizard.platform.windows.ocr_winrt import fit, line_box, to_bgra


def test_images_become_contiguous_bgra():
    gray = np.full((10, 20), 7, np.uint8)
    bgr = np.zeros((10, 20, 3), np.uint8)[:, ::-1]  # non-contiguous view
    for image in (gray, bgr):
        out = to_bgra(image)
        assert out.shape == (10, 20, 4) and out.flags["C_CONTIGUOUS"] and out.dtype == np.uint8
    assert to_bgra(gray)[0, 0].tolist() == [7, 7, 7, 255]


def test_large_images_are_shrunk_to_the_engine_limit():
    image = np.zeros((100, 4000, 4), np.uint8)
    small, f = fit(image, 2000)
    assert small.shape[:2] == (50, 2000) and f == 0.5
    same, f = fit(image[:, :500], 2000)
    assert f == 1.0 and same.shape[1] == 500


def test_line_box_unions_words_and_undoes_scaling():
    words = [(10, 20, 30, 10), (50, 18, 20, 14)]
    assert line_box(words, 1.0) == (10, 18, 70, 32)
    assert line_box(words, 2.0) == (5, 9, 35, 16)  # preprocessing enlarged the crop 2x
    assert line_box([], 1.0) == (0, 0, 0, 0)


def test_windows_engine_falls_back_to_rapidocr_off_windows(caplog):
    from subwizard.ocr.worker import build_ocr

    config = AppConfig()
    config.ocr.engine = "windows"
    with caplog.at_level(logging.WARNING):
        engine = build_ocr(config)
    assert engine.name == "rapid" and "Windows OCR" in caplog.text


def test_preprocess_for_an_engine_missing_from_a_saved_config_is_added():
    config = AppConfig()
    config.ocr.preprocess = {"rapid": config.ocr.preprocess["rapid"]}  # older config file
    config.ocr.engine = "windows"
    assert config.ocr.prep().grayscale and "windows" in config.ocr.preprocess
