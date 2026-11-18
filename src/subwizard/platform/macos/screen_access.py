"""macOS Screen Recording permission (CoreGraphics through ctypes, no PyObjC). Qt-free.

Without it macOS hands screen grabs back with only the wallpaper and our own windows, so
OCR silently reads nothing useful. The permission belongs to the app that started
SubWizard (Terminal, an editor...), and takes effect after that app is restarted.
"""

from __future__ import annotations

import ctypes
import logging

log = logging.getLogger(__name__)

CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"


def _call(name: str) -> bool | None:
    try:
        function = getattr(ctypes.CDLL(CORE_GRAPHICS), name)
    except (OSError, AttributeError):  # not macOS, or an old one without the call
        return None
    function.restype = ctypes.c_bool
    function.argtypes = []
    return bool(function())


def allowed() -> bool | None:
    """True / False, or None when it cannot be told."""
    return _call("CGPreflightScreenCaptureAccess")


def request() -> None:
    """Shows the system prompt (once) and puts the app in the Settings list."""
    log.info("macOS Ekran Kaydı izni isteniyor.")
    _call("CGRequestScreenCaptureAccess")
