"""Errors that are meant to reach the user. Qt-free.

They carry an i18n key instead of a fixed string, so the UI shows a
solution-oriented message in the current language (SPEC 3.12).
"""

from __future__ import annotations

from subwizard.i18n import t


class UserError(Exception):
    def __init__(self, key: str, **params: object) -> None:
        self.key = key
        self.params = params
        super().__init__(key)

    @property
    def message(self) -> str:
        return t(self.key, **self.params)

    def __str__(self) -> str:
        return self.message
