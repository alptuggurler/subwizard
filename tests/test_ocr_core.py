import threading
import time

import numpy as np
import pytest

from subwizard.config import AppConfig, PreprocessConfig, load_config
from subwizard.core.metrics import M, MetricsHub
from subwizard.ocr.base import BaseOCR, OCRLine, OCRResult
from subwizard.ocr.change_detect import ChangeDetector
from subwizard.ocr.preprocess import preprocess, white_text_mask
from subwizard.ocr.rapid import group_rows
from subwizard.ocr.roi import TEMPLATES, Rect, crop
from subwizard.ocr.worker import OCRState, OCRWorker, filter_lines
from subwizard.pipeline.text_utils import join_lines, normalize
from subwizard.sources.base import Frame


# --- ROI -------------------------------------------------------------------------------
def test_rect_from_points_any_order_and_clamped():
    assert Rect.from_points(0.8, 0.9, 0.2, 0.7) == Rect.from_points(0.2, 0.7, 0.8, 0.9)
    r = Rect.from_points(-0.5, 0.5, 1.5, 0.75)
    assert (r.x, r.w, r.y, r.h) == (0.0, 1.0, 0.5, 0.25)


def test_padding_grows_by_the_regions_own_size_and_stays_in_frame():
    r = Rect(0.2, 0.8, 0.5, 0.1).padded(10)
    assert r.x == pytest.approx(0.15) and r.w == pytest.approx(0.6)
    assert r.y == pytest.approx(0.79) and r.h == pytest.approx(0.12)
    edge = Rect(0.0, 0.9, 1.0, 0.1).padded(20)
    assert edge.x == 0.0 and edge.x + edge.w == 1.0 and edge.y + edge.h == 1.0


def test_proportional_rect_survives_a_resolution_change():
    rect = TEMPLATES["bottom_center"]
    full = np.zeros((1080, 1920, 3), np.uint8)
    small = np.zeros((720, 1280, 3), np.uint8)
    h_full, w_full = crop(full, rect, 0).shape[:2]
    h_small, w_small = crop(small, rect, 0).shape[:2]
    assert (w_full, w_small) == (1344, 896)
    assert h_full / 1080 == pytest.approx(h_small / 720, abs=0.002)


def test_tiny_rect_is_not_usable_and_pixels_never_collapse():
    assert not Rect(0.5, 0.5, 0.001, 0.3).usable
    x0, y0, x1, y1 = Rect(0.999, 0.999, 0.0, 0.0).to_pixels(100, 100)
    assert x1 > x0 and y1 > y0 and x1 <= 100 and y1 <= 100


def test_every_template_fits_the_frame():
    for rect in TEMPLATES.values():
        assert rect.usable and rect.x + rect.w <= 1.0 and rect.y + rect.h <= 1.0


def test_rect_config_is_validated():
    with pytest.raises(Exception):
        load_config(None, {"roi": {"rect": [0.1, 0.2, 0.3]}})


# --- preprocessing ---------------------------------------------------------------------
def _strip(height=40, width=200):
    img = np.full((height, width, 3), (40, 90, 160), np.uint8)
    img[height // 3: 2 * height // 3, 20:180] = 250  # white "text"
    return img


def test_rapid_default_is_gray_without_thresholding():
    out = preprocess(_strip(80), PreprocessConfig(upscale_below_px=48))
    assert out.ndim == 2 and out.shape == (80, 200)
    assert set(np.unique(out)) == {105, 250}  # gray levels kept, not binarised to 0/255


def test_short_regions_are_upscaled():
    assert preprocess(_strip(40), PreprocessConfig(upscale_below_px=48)).shape == (80, 400)


def test_tesseract_profile_binarises():
    cfg = AppConfig().ocr.preprocess["tesseract"]
    out = preprocess(_strip(40), cfg)
    assert set(np.unique(out)) <= {0, 255}


def test_white_text_mask_keeps_only_bright_unsaturated_pixels():
    mask = white_text_mask(_strip(30), min_value=180, max_saturation=60)
    assert mask[15, 100] == 0  # text -> dark
    assert mask[2, 2] == 255  # blue background -> white


def test_empty_region_passes_through():
    assert preprocess(np.zeros((0, 0, 3), np.uint8), PreprocessConfig()).size == 0


# --- change detection ------------------------------------------------------------------
def test_change_detector_skips_static_and_fires_on_new_text():
    det = ChangeDetector(threshold=2.0, thumb_width=64)
    a = _strip(60)
    assert det.changed(a)  # first look is always a change
    noisy = np.clip(a.astype(int) + np.random.default_rng(0).integers(-2, 3, a.shape), 0, 255)
    assert not det.changed(noisy.astype(np.uint8))  # compression noise is not a change
    b = a.copy()
    b[10:50, 20:180] = 0
    assert det.changed(b)
    det.reset()
    assert det.changed(b)


def _scene(shift: int, text_cols: tuple[int, int] | None) -> np.ndarray:
    """A dark, scrolling game scene with an optional white "subtitle" bar."""
    xs = np.arange(320)[None, :, None]
    image = np.broadcast_to(((xs + shift) % 120 + 20).astype(np.uint8), (60, 320, 3)).copy()
    if text_cols is not None:
        image[25:35, text_cols[0]:text_cols[1]] = 245
    return image


def test_bright_mode_ignores_a_moving_scene_behind_the_subtitle():
    gray = ChangeDetector(2.0, 64)
    bright = ChangeDetector(2.0, 64, bright_min=180)
    for det in (gray, bright):
        det.changed(_scene(0, (40, 200)))
    # The scene scrolls, the subtitle stays: only "gray" mode re-runs OCR.
    assert gray.changed(_scene(30, (40, 200)))
    assert not bright.changed(_scene(30, (40, 200)))
    # A new subtitle is caught in both modes.
    assert bright.changed(_scene(60, (100, 300)))
    assert bright.changed(_scene(90, None))  # subtitle disappeared


def test_change_mode_reaches_the_worker():
    cfg = load_config(None, {"ocr": {"change_mode": "gray"}})
    worker = OCRWorker(cfg, MetricsHub(), lambda: None, engine_factory=lambda c: None,
                       on_tick=lambda t: None, on_state=lambda s, m: None)  # fmt: skip
    assert worker._detector._bright_min is None
    worker = OCRWorker(AppConfig(), MetricsHub(), lambda: None, engine_factory=lambda c: None,
                       on_tick=lambda t: None, on_state=lambda s, m: None)  # fmt: skip
    assert worker._detector._bright_min == AppConfig().ocr.change_bright_min


def test_change_detector_treats_a_new_shape_as_changed():
    det = ChangeDetector(2.0, 64)
    det.changed(_strip(60))
    assert det.changed(_strip(60, 400))


# --- text normalisation ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  We   need\nto  go ", "We need to go"),
        ("| don't know", "I don't know"),
        ("|'m done", "I'm done"),
        ("You and |.", "You and I."),
        ("he|lo there", "hello there"),
        ("Go now, B0SS", "Go now, BOSS"),
        ("MP40", "MP40"),  # a trailing digit may be real
        ("0kay, fine", "Okay, fine"),
        ("Room 2O5, 1O0 bucks", "Room 205, 100 bucks"),
        ("Wait , what ?", "Wait, what?"),
        ("Meet me at 7:30 on 42nd Street.", "Meet me at 7:30 on 42nd Street."),
        ("Agent 007", "Agent 007"),  # real digits stay digits
        ("A|B testing", "A|B testing"),  # a bar between capitals is left alone
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_join_lines():
    assert join_lines(["I told you,", "", "the money's here."]) == "I told you, the money's here."
    assert join_lines(["We need some-", "thing better"]) == "We need something better"
    assert join_lines(["Spider-", "Man"]) == "Spider- Man"  # only lowercase continuations
    assert join_lines([]) == ""


# --- line grouping / filtering ---------------------------------------------------------
def test_boxes_on_one_row_are_merged_left_to_right_and_rows_ordered():
    items = [
        ("look back.", 0.9, (520, 60, 200, 40)),
        ("Just keep driving", 0.95, (100, 62, 400, 38)),
        ("I told you,", 0.99, (150, 5, 300, 40)),
    ]
    lines = group_rows(items)
    assert [line.text for line in lines] == ["I told you,", "Just keep driving look back."]
    assert lines[1].box == (100, 60, 620, 40)
    assert lines[1].confidence == pytest.approx(0.925)


def test_low_confidence_lines_are_dropped():
    result = OCRResult((OCRLine("real", 0.95, (0, 0, 1, 1)), OCRLine("~#~", 0.3, (0, 2, 1, 1))))
    assert [line.text for line in filter_lines(result, 0.6)] == ["real"]


# --- worker ------------------------------------------------------------------------------
class FakeEngine(BaseOCR):
    name = "rapid"

    def __init__(self, text="Hello there"):
        self.text = text
        self.calls = 0
        self.closed = False

    def recognize(self, image, scale=1.0):
        self.calls += 1
        if not self.text:
            return OCRResult()
        return OCRResult((OCRLine(self.text, 0.97, (0, 0, image.shape[1], image.shape[0])),))

    def close(self):
        self.closed = True


class FrameFeed:
    def __init__(self):
        self.frame = None
        self._index = 0

    def show(self, image):
        self.frame = Frame(image, time.perf_counter(), self._index)
        self._index += 1

    def __call__(self):
        return self.frame


def _frame(value=0):
    img = np.full((360, 640, 3), 60, np.uint8)
    img[300:330, 200:440] = 250 - value
    return img


def _worker(engine, feed, ticks, states, **cfg):
    config = load_config(None, {"ocr": {"hz": 30, **cfg}})
    metrics = MetricsHub()
    worker = OCRWorker(config, metrics, feed, engine_factory=lambda c: engine,
                       on_tick=ticks.append, on_state=lambda s, m: states.append(s))  # fmt: skip
    return worker, metrics, config


def _wait(cond, timeout=3.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_worker_reads_new_frames_and_skips_unchanged_ones():
    engine, feed, ticks, states = FakeEngine(), FrameFeed(), [], []
    worker, metrics, _ = _worker(engine, feed, ticks, states)
    worker.start()
    try:
        feed.show(_frame())
        assert _wait(lambda: len(ticks) >= 1)
        first = ticks[0]
        assert not first.skipped and first.text == "Hello there" and first.confidence > 0.9
        assert first.crop.shape[2] == 3 and first.processed is not None

        feed.show(_frame())  # same picture, new frame index
        assert _wait(lambda: len(ticks) >= 2)
        assert ticks[1].skipped and ticks[1].text == "Hello there"  # previous result reused
        assert engine.calls == 1

        n = len(ticks)
        time.sleep(0.1)  # no new frame: nothing to do
        assert len(ticks) == n

        engine.text = "Second line"
        feed.show(_frame(120))  # changed region
        assert _wait(lambda: ticks[-1].text == "Second line")
        snap = metrics.snapshot()
        assert snap.counter(M.OCR_SKIPPED) == 1
        assert snap.gauge(M.OCR_TEXT) == "Second line"
        assert snap.stat(M.OCR_MS).count >= 2
    finally:
        worker.stop()
        worker.join(2)
    assert states[0] == OCRState.LOADING and OCRState.RUNNING in states
    assert states[-1] == OCRState.OFF and engine.closed


def test_force_runs_ocr_on_an_unchanged_region():
    engine, feed, ticks, states = FakeEngine(), FrameFeed(), [], []
    worker, _, _ = _worker(engine, feed, ticks, states)
    worker.start()
    try:
        feed.show(_frame())
        assert _wait(lambda: engine.calls == 1)
        worker.force()
        feed.show(_frame())
        assert _wait(lambda: engine.calls == 2)
    finally:
        worker.stop()
        worker.join(2)


def test_moving_the_region_resets_change_detection():
    engine, feed, ticks, states = FakeEngine(), FrameFeed(), [], []
    worker, _, config = _worker(engine, feed, ticks, states)
    worker.start()
    try:
        feed.show(_frame())
        assert _wait(lambda: engine.calls == 1)
        config.roi.rect = [0.0, 0.0, 0.5, 0.5]  # a different, equally blank-ish area
        feed.show(_frame())
        assert _wait(lambda: engine.calls == 2)
    finally:
        worker.stop()
        worker.join(2)


def test_engine_failure_is_reported_not_raised():
    def broken(_config):
        raise RuntimeError("model file missing")

    states, messages = [], []
    worker = OCRWorker(AppConfig(), MetricsHub(), lambda: None, engine_factory=broken,
                       on_tick=lambda t: None,
                       on_state=lambda s, m: (states.append(s), messages.append(m)))  # fmt: skip
    worker.start()
    worker.join(2)
    assert states[-1] == OCRState.ERROR and "model file missing" in messages[-1]


def test_a_failing_tick_does_not_stop_the_worker():
    class Flaky(FakeEngine):
        def recognize(self, image, scale=1.0):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("bad frame")
            return super().recognize(image, scale)

    engine, feed, ticks, states = Flaky(), FrameFeed(), [], []
    worker, _, _ = _worker(engine, feed, ticks, states)
    worker.start()
    try:
        feed.show(_frame())
        assert _wait(lambda: engine.calls == 1)
        feed.show(_frame(100))
        assert _wait(lambda: len(ticks) >= 1 and ticks[-1].text == "Hello there")
    finally:
        worker.stop()
        worker.join(2)


def test_worker_stops_promptly_even_at_a_slow_rate():
    engine, feed, ticks, states = FakeEngine(), FrameFeed(), [], []
    config = load_config(None, {"ocr": {"hz": 0.1}})
    worker = OCRWorker(config, MetricsHub(), feed, engine_factory=lambda c: engine,
                       on_tick=ticks.append, on_state=lambda s, m: None)  # fmt: skip
    worker.start()
    time.sleep(0.05)
    started = time.perf_counter()
    worker.stop()
    worker.join(2)
    assert not worker.is_alive() and time.perf_counter() - started < 1.0
    assert threading.active_count() >= 1
