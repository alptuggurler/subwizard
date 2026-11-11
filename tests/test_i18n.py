import json
import re
from pathlib import Path

import pytest

import subwizard
from subwizard.i18n import FALLBACK_LANGUAGE, I18n, available_languages, match_language

I18N_DIR = Path(subwizard.__file__).parent / "i18n"
SRC_DIR = Path(subwizard.__file__).parent
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _strings(code: str) -> dict[str, str]:
    return json.loads((I18N_DIR / f"{code}.json").read_text(encoding="utf-8"))


def test_turkish_and_english_available():
    assert available_languages() == {"en": "English", "tr": "Türkçe"}


@pytest.mark.parametrize("code", sorted(available_languages()))
def test_every_language_has_same_keys_and_placeholders(code):
    reference = _strings(FALLBACK_LANGUAGE)
    strings = _strings(code)
    assert strings.keys() == reference.keys()
    for key, text in reference.items():
        assert set(PLACEHOLDER.findall(strings[key])) == set(PLACEHOLDER.findall(text)), key


def test_keys_used_in_code_exist():
    """Literal t("...") keys in the source must exist (dynamic f-string keys are skipped)."""
    reference = _strings(FALLBACK_LANGUAGE)
    used = set()
    for file in SRC_DIR.rglob("*.py"):
        used.update(re.findall(r"""\bt\(\s*["']([\w.]+)["']""", file.read_text(encoding="utf-8")))
    assert used, "no t() calls found — the pattern is stale"
    assert not used - reference.keys()


def test_translate_format_and_switch():
    i18n = I18n("tr")
    assert i18n.t("nav.home") == "Ana Sayfa"
    assert i18n.t("about.version", version="1.2") == "Sürüm 1.2"
    i18n.set_language("en")
    assert i18n.t("nav.home") == "Home"


def test_listeners_fire_only_on_real_change():
    i18n = I18n("tr")
    seen = []
    i18n.subscribe(seen.append)
    i18n.set_language("tr")
    i18n.set_language("en")
    assert seen == ["en"]


def test_unknown_key_and_language_fall_back():
    i18n = I18n("xx")
    assert i18n.language == FALLBACK_LANGUAGE
    assert i18n.t("no.such.key") == "no.such.key"


@pytest.mark.parametrize(
    ("locale_name", "expected"),
    [
        ("tr_TR", "tr"),
        ("Turkish_Türkiye", "tr"),
        ("en_US", "en"),
        ("de_DE", "en"),
        (None, "en"),
        ("", "en"),
    ],
)
def test_match_language(locale_name, expected):
    assert match_language(locale_name) == expected
