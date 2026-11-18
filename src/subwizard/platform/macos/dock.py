"""Dock icon on/off for a menu-bar-only SubWizard. Qt-free; talks to AppKit through the
Objective-C runtime with ctypes, so no PyObjC dependency is needed.

NSApplicationActivationPolicyRegular (0) shows the Dock icon and the app menu;
NSApplicationActivationPolicyAccessory (1) hides both, the menu bar item stays.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger(__name__)

POLICY_REGULAR = 0
POLICY_ACCESSORY = 1

_objc = None


def _runtime():  # noqa: ANN202
    global _objc
    if _objc is None:
        path = ctypes.util.find_library("objc")
        if not path:
            raise OSError("libobjc not found")
        lib = ctypes.cdll.LoadLibrary(path)
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("AppKit"))  # registers NSApplication
        _objc = lib
    return _objc


def _send(receiver: int, selector: str, *args: tuple[type, object], restype=ctypes.c_void_p):  # noqa: ANN001, ANN202
    """objc_msgSend with an exact prototype (required on arm64, where it is not variadic)."""
    lib = _runtime()
    proto = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *(a[0] for a in args))
    fn = proto(ctypes.cast(lib.objc_msgSend, ctypes.c_void_p).value)
    return fn(receiver, lib.sel_registerName(selector.encode()), *(a[1] for a in args))


def _ns_app() -> int:
    cls = _runtime().objc_getClass(b"NSApplication")
    return _send(cls, "sharedApplication")


def set_dock_visible(visible: bool) -> bool:
    """Show or hide the Dock icon. Returns False if AppKit could not be reached."""
    try:
        policy = POLICY_REGULAR if visible else POLICY_ACCESSORY
        return bool(_send(_ns_app(), "setActivationPolicy:", (ctypes.c_long, policy),
                          restype=ctypes.c_bool))  # fmt: skip
    except (OSError, AttributeError, ValueError):
        log.exception("Dock simgesi değiştirilemedi")
        return False


def activate() -> None:
    """Bring the app to the front. Without a Dock icon macOS does not do this when a
    window is shown, so the window would open behind the active app."""
    try:
        _send(_ns_app(), "activateIgnoringOtherApps:", (ctypes.c_bool, True), restype=None)
    except (OSError, AttributeError, ValueError):
        log.exception("Uygulama öne getirilemedi")
