"""Test engine (`translator.engine: test`): no request is sent anywhere. Qt-free.

The text that was read comes back as its own "translation", at once and for free, so the
reading region, the subtitle position and the look can be set up without spending quota.
"""

from __future__ import annotations

from subwizard.translator.base import BaseTranslator, TranslationRequest, TranslationResult


class EchoTranslator(BaseTranslator):
    name = "test"

    def translate(self, request: TranslationRequest) -> TranslationResult:
        return TranslationResult(request.text, self.name, 0.0)
