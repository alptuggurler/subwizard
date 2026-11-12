"""Window tweaks via user32 (Windows only; not run on macOS, unverified there)."""

from __future__ import annotations

import ctypes
import logging

log = logging.getLogger(__name__)

WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004+: the window is left out of screen captures


def exclude_from_capture(hwnd: int) -> bool:
    ok = bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    if not ok:
        log.warning("Overlay ekran yakalamadan çıkarılamadı (Windows 10 2004 öncesi?); "
                    "altyazı konumunu 'üst' yap ki OCR çeviriyi okumasın.")  # fmt: skip
    return ok
