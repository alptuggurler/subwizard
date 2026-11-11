"""Grab every page of the main window into docs/screenshots/ (SPEC 7).

    uv run python scripts/screenshot_pages.py [--out DIR] [--lang tr|en] [--theme dark|light]
                                              [--clip clip.mp4] [--overlay compact|detailed]

With --clip the pipeline runs on that video while pages are grabbed, and the measured
source/render FPS are printed at the end (the M1 acceptance measurement).
Uses a throwaway config so the user's real settings are never touched.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer

from subwizard.app import create_app
from subwizard.config import load_config
from subwizard.core.metrics import M

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SETTLE_MS = 400  # lets page-switch animations finish before grabbing


def _settle(ms: int = SETTLE_MS) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--lang", choices=["tr", "en"], default="tr")
    parser.add_argument("--theme", choices=["dark", "light"], default="dark")
    parser.add_argument("--clip", type=Path, help="run the pipeline on this video")
    parser.add_argument("--overlay", choices=["off", "compact", "detailed"], default="off")
    parser.add_argument("--warmup", type=float, default=3.0, help="seconds before grabbing")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.yaml"
        overrides = {
            "ui": {"language": args.lang, "theme": args.theme, "perf_overlay": args.overlay}
        }
        if args.clip:
            # OCR's "N of M skipped" summary lines go to the console (SPEC M2 acceptance).
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
            overrides["source"] = {"kind": "file", "path": str(args.clip)}
        config = load_config(None, overrides)
        app, window = create_app(config, config_path)
        if window.tray is not None:
            window.tray.hide()
        window.show()
        _settle()
        if args.clip:
            window.controller.start()
            _settle(int(args.warmup * 1000))
            # Measure now, while the Home page (the video) is what is on screen.
            snap = window.controller.snapshot()
            source, render = snap.rate(M.SOURCE_FPS), snap.rate(M.RENDER_FPS)
            age = snap.stat(M.RENDER_FRAME_AGE_MS)
            print(f"mode: {snap.gauge(M.SOURCE_ACTUAL)}")
            print(f"source FPS: {source.short:.1f} (2 s) / {source.long:.1f} (60 s)")
            print(f"render FPS: {render.short:.1f} (2 s) / {render.long:.1f} (60 s)")
            print(f"frame age: {age.mean_long:.1f} ms avg / {age.worst_long:.1f} ms worst")
            print(f"dropped / repeated: {snap.counter(M.SOURCE_DROPPED)} / "
                  f"{snap.counter(M.SOURCE_REPEATED)}")
            ticks, runs, ms = snap.rate(M.OCR_TICKS), snap.rate(M.OCR_RUNS), snap.stat(M.OCR_MS)
            skipped = max(0.0, 1 - runs.long / ticks.long) if ticks.long else 0.0
            print(f"OCR: {runs.long:.2f} runs/s of {ticks.long:.2f} checks/s "
                  f"({skipped:.0%} skipped), {ms.mean_long:.0f} ms avg / "
                  f"{ms.worst_long:.0f} ms worst, text: {snap.gauge(M.OCR_TEXT)!r}")
        for page in window.pages:
            window.switchTo(page)
            _settle()
            target = args.out / f"{page.key}-{args.lang}-{args.theme}.png"
            if not window.grab().save(str(target)):
                print(f"could not save {target}", file=sys.stderr)
                return 1
            print(target)
        if args.clip:
            window.controller.stop()
        window.controller.config.ui.close_to_tray = False
        window.close()
        app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
