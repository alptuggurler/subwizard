"""Build the Windows app folder with PyInstaller (SPEC M5).

    uv sync --group build
    uv run python scripts/build_windows.py            # -> dist/SubWizard/SubWizard.exe
    uv run python scripts/build_windows.py --zip      # + dist/SubWizard-<version>-portable.zip

Then `installer/subwizard.iss` (Inno Setup) turns dist/SubWizard into setup.exe; the
GitHub Actions release workflow runs both on a version tag.

Why PyInstaller (not Nuitka): no C compiler needed on the build machine, and a one-folder
build starts fast. Runs on macOS/Linux too, which only checks that the bundle is complete
(data files, imports); the real target is Windows.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "subwizard"
DIST = ROOT / "dist"
NAME = "SubWizard"
# Data files the code reads next to its modules (Path(__file__) lookups).
DATA = [
    (SRC / "i18n", "subwizard/i18n"),
    (SRC / "web" / "static", "subwizard/web/static"),
]
# Imported lazily or by name, so PyInstaller's analysis cannot see them.
HIDDEN = ["subwizard.ui.wizard", "subwizard.app", "rapidocr", "deep_translator",
          "pynput.keyboard._win32", "keyring.backends.Windows"]  # fmt: skip
COLLECT = ["rapidocr", "qfluentwidgets"]  # packages with model / resource files


def version() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from subwizard import __version__

    return __version__


def build() -> Path:
    sep = ";" if sys.platform == "win32" else ":"
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", NAME,
            "--windowed", "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
            "--specpath", str(ROOT / "build"), "--paths", str(ROOT / "src")]  # fmt: skip
    for src, dest in DATA:
        args += ["--add-data", f"{src}{sep}{dest}"]
    for module in HIDDEN:
        args += ["--hidden-import", module]
    for package in COLLECT:
        args += ["--collect-all", package]
    args.append(str(ROOT / "run.py"))
    subprocess.run(args, check=True)
    folder = DIST / NAME
    if sys.platform == "darwin":
        folder = DIST / NAME  # the .app is next to it; the folder build is what we check
    return folder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", action="store_true", help="also make the portable zip")
    args = parser.parse_args()
    folder = build()
    print(f"built: {folder}")
    if args.zip:
        archive = shutil.make_archive(str(DIST / f"{NAME}-{version()}-portable"), "zip",
                                      root_dir=DIST, base_dir=NAME)  # fmt: skip
        print(f"portable: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
