"""OCR text normalisation (SPEC 3.4). Qt-free.

Only fixes that are safe from context: a digit between letters is a letter, a
letter between digits is a digit, a lone bar is the pronoun "I". Anything that could
change the meaning of a real word is left alone.
"""

from __future__ import annotations

import re

_SPACES = re.compile(r"\s+")
_BAR_AS_I = [
    (re.compile(r"(?<![^\s\"'(\[])\|(?=[\s'’,.!?]|$)"), "I"),  # "| am", "|'m", "|."
    (re.compile(r"(?<=[A-Za-z])\|(?=[a-z])"), "l"),  # "he|lo" -> "hello"
]
_ZERO_IN_WORD = re.compile(r"(?<=[A-Za-z])0(?=[A-Za-z])|\b0(?=[a-z]{2,}\b)")
_O_IN_NUMBER = re.compile(r"(?<=\d)[Oo](?=\d)")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:])")


def normalize(text: str) -> str:
    """Collapse whitespace and line breaks, apply the context-safe OCR fixes."""
    text = _SPACES.sub(" ", text).strip()
    for pattern, replacement in _BAR_AS_I:
        text = pattern.sub(replacement, text)
    text = _ZERO_IN_WORD.sub("O", text)
    text = _O_IN_NUMBER.sub("0", text)
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text)


def join_lines(lines: list[str]) -> str:
    """Join OCR lines top to bottom into one subtitle; a hyphen at a line end that
    splits a word is removed ("some-" + "thing" -> "something")."""
    out = ""
    for line in (normalize(line) for line in lines):
        if not line:
            continue
        if out.endswith("-") and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}" if out else line
    return out
