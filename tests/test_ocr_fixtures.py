"""OCR accuracy on the fixture frames (M2 acceptance): measured, not eyeballed."""

import difflib
import json
from pathlib import Path

import cv2
import pytest

from subwizard.config import AppConfig
from subwizard.ocr.preprocess import preprocess
from subwizard.ocr.roi import TEMPLATES, crop
from subwizard.ocr.worker import filter_lines
from subwizard.pipeline.text_utils import join_lines

FIXTURES = Path(__file__).parent / "fixtures" / "ocr"
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
MIN_CASE_ACCURACY = 0.90
MIN_MEAN_ACCURACY = 0.97


@pytest.fixture(scope="module")
def engine():
    from subwizard.ocr.rapid import RapidOCREngine

    return RapidOCREngine(AppConfig().ocr.det_max_side)


def read_case(engine, case) -> str:
    cfg = AppConfig()
    image = cv2.imread(str(FIXTURES / case["file"]))
    assert image is not None, case["file"]
    region = crop(image, TEMPLATES[case["template"]], cfg.roi.padding_pct)
    processed = preprocess(region, cfg.ocr.preprocess["rapid"])
    result = engine.recognize(processed)
    return join_lines([line.text for line in filter_lines(result, cfg.ocr.min_confidence)])


def accuracy(got: str, expected: str) -> float:
    if not got and not expected:
        return 1.0
    return difflib.SequenceMatcher(None, got, expected).ratio()


def test_accuracy_on_every_fixture(engine):
    scores = {}
    for case in CASES:
        got = read_case(engine, case)
        scores[case["file"]] = (accuracy(got, case["text"]), got, case["text"])
    report = "\n".join(f"{f}: {s:.3f} got={g!r} want={w!r}" for f, (s, g, w) in scores.items())
    mean = sum(s for s, _, _ in scores.values()) / len(scores)
    assert all(s >= MIN_CASE_ACCURACY for s, _, _ in scores.values()), report
    assert mean >= MIN_MEAN_ACCURACY, report


def test_empty_frame_reads_nothing(engine):
    case = next(c for c in CASES if c["file"] == "empty.jpg")
    assert read_case(engine, case) == ""


def test_two_line_subtitle_comes_back_in_reading_order(engine):
    case = next(c for c in CASES if c["lines"] == 2)
    assert read_case(engine, case) == case["text"]
