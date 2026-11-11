"""Grab every page of the main window into docs/screenshots/ (SPEC 7).

    uv run python scripts/screenshot_pages.py [--out DIR] [--lang tr|en] [--theme dark|light]

Uses a throwaway config so the user's real settings are never touched.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer

from subwizard.app import create_app
from subwizard.config import load_config

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
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "config.yaml"
        config = load_config(None, {"ui": {"language": args.lang, "theme": args.theme}})
        app, window = create_app(config, config_path)
        if window.tray is not None:
            window.tray.hide()
        window.show()
        _settle()
        for page in window.pages:
            window.switchTo(page)
            _settle()
            target = args.out / f"{page.key}-{args.lang}-{args.theme}.png"
            if not window.grab().save(str(target)):
                print(f"could not save {target}", file=sys.stderr)
                return 1
            print(target)
        window.close()
        app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
