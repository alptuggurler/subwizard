"""M7 acceptance: compare `ocr+text` and `vision` on the same fixtures (SPEC 6, M7).

For every frame in tests/fixtures/ocr: reading accuracy (against the known English line),
latency (read + translate) and cost. Writes a Markdown report.

    uv run python scripts/benchmark.py                      # report -> docs/benchmark.md
    uv run python scripts/benchmark.py --translator deep    # text path without a Gemini key
    uv run python scripts/benchmark.py --translator none --skip-vision   # OCR only, offline

`vision` needs a Gemini API key (GEMINI_API_KEY / the app's saved keys); without one its
column says so and only the OCR side is measured. The translation itself is not scored
(no reference Turkish); both translations are listed for reading side by side.
"""

from __future__ import annotations

import argparse
import difflib
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from subwizard.config import AppConfig  # noqa: E402
from subwizard.core.metrics import MetricsHub  # noqa: E402
from subwizard.core.secrets import load_api_keys  # noqa: E402
from subwizard.ocr.preprocess import preprocess  # noqa: E402
from subwizard.ocr.roi import TEMPLATES, crop  # noqa: E402
from subwizard.ocr.worker import filter_lines  # noqa: E402
from subwizard.pipeline.text_utils import join_lines  # noqa: E402
from subwizard.translator.base import TranslationError, TranslationRequest  # noqa: E402
from subwizard.translator.cache import TranslationCache  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "ocr"
JPEG_QUALITY = 90  # what vision mode sends: smaller than PNG, text stays sharp
TOKENS_PER_PRICE_UNIT = 1_000_000


@dataclass
class Row:
    file: str
    expected: str
    read: str = ""
    accuracy: float = 0.0
    read_ms: float = 0.0
    translate_ms: float = 0.0
    translation: str = ""
    cost_usd: float = 0.0
    error: str = ""

    @property
    def total_ms(self) -> float:
        return self.read_ms + self.translate_ms


@dataclass
class Mode:
    name: str
    rows: list[Row] = field(default_factory=list)
    note: str = ""


def accuracy(got: str, expected: str) -> float:
    if not got and not expected:
        return 1.0
    return difflib.SequenceMatcher(None, got, expected).ratio()


def region_of(case: dict, config: AppConfig):  # noqa: ANN201 - numpy array
    image = cv2.imread(str(FIXTURES / case["file"]))
    return crop(image, TEMPLATES[case["template"]], config.roi.padding_pct)


def price(config: AppConfig, input_tokens: int, output_tokens: int) -> float:
    p = config.translator.prices.get(config.translator.model)
    if p is None:
        return 0.0
    return (input_tokens * p.input + output_tokens * p.output) / TOKENS_PER_PRICE_UNIT


def run_ocr_text(cases: list[dict], config: AppConfig, translate: bool = True) -> Mode:
    from subwizard.ocr.rapid import RapidOCREngine
    from subwizard.translator.service import TranslationService, build_engines

    engine = RapidOCREngine(config.ocr.det_max_side)
    service = None
    mode = Mode("ocr+text", note="OCR only (--translator none)")
    if translate:
        primary, fallback, note = build_engines(config)
        service = TranslationService(config, MetricsHub(), primary, fallback, TranslationCache(0))
        mode.note = f"engine: {primary.name if primary else 'deep'}" + (f" ({note})" if note else "")
    engine.recognize(region_of(cases[0], config))  # warm-up, not measured
    for case in cases:
        row = Row(case["file"], case["text"])
        region = region_of(case, config)
        started = time.perf_counter()
        result = engine.recognize(preprocess(region, config.ocr.preprocess["rapid"]))
        row.read = join_lines([ln.text for ln in filter_lines(result, config.ocr.min_confidence)])
        row.read_ms = (time.perf_counter() - started) * 1000
        row.accuracy = accuracy(row.read, row.expected)
        if row.read and service is not None:
            try:
                tr = service.translate(TranslationRequest(row.read))
                row.translation, row.translate_ms, row.cost_usd = tr.text, tr.latency_ms, tr.cost_usd
            except TranslationError as exc:
                row.error = str(exc)
        mode.rows.append(row)
    if service is not None:
        service.close()
    return mode


def run_vision(cases: list[dict], config: AppConfig) -> Mode:
    from subwizard.translator.gemini import GeminiTranslator

    keys, _source = load_api_keys()
    if not keys:
        return Mode("vision", note="not run: no Gemini API key")
    tr = config.translator
    gemini = GeminiTranslator(keys, model=tr.model, timeout_s=max(tr.timeout_s, 10.0),
                              retries=tr.retries, thinking_level=tr.thinking_level,
                              prompt=tr.system_prompt, quota_cooldown_s=tr.quota_cooldown_s,
                              media_resolution=tr.vision_media_resolution)  # fmt: skip
    mode = Mode("vision", note=f"model: {tr.model}, media_resolution: {tr.vision_media_resolution}")
    for case in cases:
        row = Row(case["file"], case["text"])
        ok, jpeg = cv2.imencode(".jpg", region_of(case, config),
                                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])  # fmt: skip
        try:
            result = gemini.translate_image(jpeg.tobytes(), "image/jpeg")
        except TranslationError as exc:
            row.error = str(exc)
        else:
            row.read, row.translation = result.source, result.translation
            row.translate_ms = result.result.latency_ms  # one call does both
            row.cost_usd = price(config, result.result.input_tokens, result.result.output_tokens)
        row.accuracy = accuracy(row.read, row.expected)
        mode.rows.append(row)
    return mode


def summary(mode: Mode) -> str:
    rows = [r for r in mode.rows if not r.error]
    if not rows:
        return f"| {mode.name} | — | — | — | — | {mode.note} |"
    totals = [r.total_ms for r in rows]
    return (f"| {mode.name} | {statistics.mean(r.accuracy for r in mode.rows):.3f} "
            f"| {statistics.median(totals):.0f} | {max(totals):.0f} "
            f"| ${sum(r.cost_usd for r in rows) / len(rows):.6f} | {mode.note} |")  # fmt: skip


def report(modes: list[Mode]) -> str:
    out = ["# OCR+metin ve görsel mod karşılaştırması (M7)", "",
           f"{time.strftime('%Y-%m-%d %H:%M')} · {len(modes[0].rows)} fixture kare "
           "(`tests/fixtures/ocr`). Doğruluk: okunan İngilizce ile beklenen satırın benzerliği "
           "(0–1). Süre: okuma + çeviri (vision'da tek çağrı).", "",
           "| Mod | Ort. doğruluk | Medyan ms | En kötü ms | Satır başı maliyet | Not |",
           "|---|---|---|---|---|---|"]  # fmt: skip
    out += [summary(m) for m in modes]
    for mode in modes:
        if not mode.rows:
            continue
        out += ["", f"## {mode.name}", "", "| Kare | Doğruluk | ms | Okunan | Çeviri |",
                "|---|---|---|---|---|"]  # fmt: skip
        for r in mode.rows:
            text = r.error and f"hata: {r.error}" or r.translation
            out.append(f"| {r.file} | {r.accuracy:.3f} | {r.total_ms:.0f} | {r.read} | {text} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "benchmark.md")
    parser.add_argument("--translator", choices=("gemini", "deep", "none"), default="gemini",
                        help="none: measure OCR only (no network)")
    parser.add_argument("--skip-vision", action="store_true")
    args = parser.parse_args(argv)
    config = AppConfig()
    if args.translator != "none":
        config.translator.engine = args.translator
    cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
    modes = [run_ocr_text(cases, config, translate=args.translator != "none")]
    if not args.skip_vision:
        modes.append(run_vision(cases, config))
    text = report(modes)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
