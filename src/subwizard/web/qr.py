"""QR code for the web overlay URL (SPEC 3.10): a module matrix for the UI to paint, and
a text version for the console. Qt-free; no PIL needed (`qrcode` alone)."""

from __future__ import annotations

import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M

QUIET_ZONE = 2  # modules of white border (a phone camera needs some)


def qr_matrix(text: str) -> list[list[bool]]:
    """Rows of modules, True = dark, including the quiet zone."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=QUIET_ZONE)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.get_matrix()


def qr_text(text: str) -> str:
    """Half-block text QR for the console (two module rows per text line)."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=QUIET_ZONE)
    qr.add_data(text)
    qr.make(fit=True)
    out = io.StringIO()
    qr.print_ascii(out=out, invert=True)
    return out.getvalue()
