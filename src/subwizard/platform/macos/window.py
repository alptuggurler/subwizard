"""The PC-game overlay window on macOS: over fullscreen apps, and out of screen grabs.
Qt-free; AppKit through the Objective-C runtime (ctypes), like dock.py.

A browser or game in fullscreen lives in its own Space, and an ordinary window never
shows there. The overlay (a Qt Tool window, i.e. an NSPanel) gets:
- collection behavior CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary,
  so it is present in every Space, the fullscreen ones included;
- the non-activating panel style, which lets a panel of a regular (Dock) app float over
  another app's fullscreen Space;
- a window level above the menu bar (NSStatusWindowLevel);
- sharing type "none", so screen grabs (mss uses CGWindowListCreateImage) leave it out
  and OCR never reads the Turkish text back.
"""

from __future__ import annotations

import ctypes
import logging

from subwizard.platform.macos.dock import _send

log = logging.getLogger(__name__)

CAN_JOIN_ALL_SPACES = 1 << 0
STATIONARY = 1 << 4
IGNORES_CYCLE = 1 << 6
FULL_SCREEN_AUXILIARY = 1 << 8
NONACTIVATING_PANEL = 1 << 7  # NSWindowStyleMaskNonactivatingPanel
STATUS_WINDOW_LEVEL = 25  # NSStatusWindowLevel: above normal and floating windows
SHARING_NONE = 0  # NSWindowSharingNone


def _window(view_id: int) -> int:
    """The NSWindow of a Qt widget (QWidget.winId() is its NSView on macOS)."""
    window = _send(view_id, "window")
    if not window:
        raise ValueError("the view has no window yet")
    return window


def float_over_fullscreen(view_id: int) -> bool:
    try:
        window = _window(view_id)
        behavior = CAN_JOIN_ALL_SPACES | STATIONARY | IGNORES_CYCLE | FULL_SCREEN_AUXILIARY
        _send(window, "setCollectionBehavior:", (ctypes.c_ulong, behavior), restype=None)
        mask = _send(window, "styleMask", restype=ctypes.c_ulong)
        _send(window, "setStyleMask:", (ctypes.c_ulong, mask | NONACTIVATING_PANEL), restype=None)
        _send(window, "setLevel:", (ctypes.c_long, STATUS_WINDOW_LEVEL), restype=None)
        _send(window, "setHidesOnDeactivate:", (ctypes.c_bool, False), restype=None)
        return True
    except (OSError, AttributeError, ValueError):
        log.exception("Altyazı penceresi tam ekran uygulamaların üstüne alınamadı")
        return False


def exclude_from_capture(view_id: int) -> bool:
    try:
        _send(_window(view_id), "setSharingType:", (ctypes.c_ulong, SHARING_NONE), restype=None)
        return True
    except (OSError, AttributeError, ValueError):
        log.exception("Altyazı penceresi ekran yakalamadan çıkarılamadı")
        return False


def collection_behavior(view_id: int) -> int:
    """For checks: the window's current collection behavior."""
    return int(_send(_window(view_id), "collectionBehavior", restype=ctypes.c_ulong))
