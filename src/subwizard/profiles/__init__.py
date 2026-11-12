"""Game profiles (SPEC 3.6): per-game prompt context, glossary, ignore list and subtitle
region in one YAML file. Qt-free.

Built-in profiles ship next to this module (`default.yaml`, `gta6.yaml`); the user's own
live in `<user data>/profiles/` and win over a built-in one of the same name, so editing
a built-in profile in the app saves a user copy instead of touching the install.

    title: GTA 6
    game_info: "Vice City / Leonida. Main characters Jason and Lucia. Crime, street talk."
    prompt_extra: "Keep the street tone."
    roi_template: bottom_center
    ignore_phrases: ["Press .* to skip"]
    glossary:
      Jason: keep          # never translated
      homie: kanka         # always translated like this
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from subwizard.utils.paths import user_data_dir

log = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).parent
SUFFIX = ".yaml"
DEFAULT = "default"
KEEP = "keep"  # glossary value: leave the term as it is
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")  # file name; also --profile


class GameProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = DEFAULT  # from the file name, not stored in the file
    title: str = ""
    game_info: str = ""
    prompt_extra: str = ""
    roi_template: str = ""  # "" = keep the user's region
    ignore_phrases: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)

    @field_validator("glossary", mode="before")
    @classmethod
    def _clean_glossary(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {str(k).strip(): str(v).strip() for k, v in value.items()
                if str(k).strip() and v is not None and str(v).strip()}  # fmt: skip

    @property
    def keep_terms(self) -> list[str]:
        return [term for term, target in self.glossary.items() if target.lower() == KEEP]

    def expected(self, term: str) -> str:
        """What `term` should look like in the translation."""
        target = self.glossary[term]
        return term if target.lower() == KEEP else target

    def to_yaml(self) -> str:
        data = self.model_dump(exclude={"name"})
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def user_dir() -> Path:
    return user_data_dir() / "profiles"


def valid_name(name: str) -> bool:
    return bool(NAME_PATTERN.match(name))


def _read(path: Path) -> GameProfile | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("not a mapping")
        return GameProfile(**{**data, "name": path.stem})
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        log.warning("Oyun profili okunamadı (%s): %s", path, exc)
        return None


def _files() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for folder in (BUILTIN_DIR, user_dir()):  # user files override built-ins
        if folder.is_dir():
            for path in sorted(folder.glob(f"*{SUFFIX}")):
                if valid_name(path.stem):
                    found[path.stem] = path
    return found


def list_profiles() -> dict[str, GameProfile]:
    """Every readable profile by name, `default` first."""
    profiles = {name: p for name, path in _files().items() if (p := _read(path)) is not None}
    if DEFAULT not in profiles:
        profiles[DEFAULT] = GameProfile()
    return {DEFAULT: profiles.pop(DEFAULT), **dict(sorted(profiles.items()))}


def load_profile(name: str) -> GameProfile:
    """The named profile; an unknown or broken one falls back to `default` (logged)."""
    path = _files().get(name)
    profile = _read(path) if path is not None else None
    if profile is not None:
        return profile
    if name != DEFAULT:
        log.warning("Oyun profili %r bulunamadı; varsayılan kullanılıyor.", name)
        return load_profile(DEFAULT)
    return GameProfile()


def is_builtin(name: str) -> bool:
    return (BUILTIN_DIR / f"{name}{SUFFIX}").is_file()


def is_user(name: str) -> bool:
    return (user_dir() / f"{name}{SUFFIX}").is_file()


def save_profile(profile: GameProfile) -> Path:
    if not valid_name(profile.name):
        raise ValueError(f"invalid profile name: {profile.name!r}")
    path = user_dir() / f"{profile.name}{SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.to_yaml(), encoding="utf-8")
    return path


def delete_profile(name: str) -> bool:
    """Removes the user's file; a built-in profile then comes back as shipped."""
    path = user_dir() / f"{name}{SUFFIX}"
    if path.is_file():
        path.unlink()
        return True
    return False


def import_profile(path: Path) -> GameProfile:
    """Reads a .yaml from anywhere and saves it as a user profile (name from the file)."""
    stem = re.sub(r"[^a-z0-9_-]", "-", path.stem.lower()).strip("-_") or "profile"
    profile = _read(path)
    if profile is None:
        raise ValueError(f"not a profile: {path}")
    profile = profile.model_copy(update={"name": stem[:40]})
    save_profile(profile)
    return profile


def unique_name(base: str) -> str:
    existing = _files()
    name, n = base, 2
    while name in existing:
        name = f"{base}-{n}"
        n += 1
    return name


# --- glossary ---------------------------------------------------------------------------
def _term_pattern(term: str) -> re.Pattern[str]:
    # Whole words only ("Jason" not inside "Jasonville"); Turkish suffixes after an
    # apostrophe ("Jason'a") still match because the apostrophe is a boundary.
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)


def terms_in(text: str, profile: GameProfile) -> list[str]:
    return [term for term in profile.glossary if _term_pattern(term).search(text)]


def glossary_violations(source: str, translation: str, profile: GameProfile) -> list[str]:
    """Glossary terms in `source` whose expected form is missing from `translation`.
    A Turkish suffix glued on ("Lucia'ya", "kankam") still counts as present."""
    lowered = translation.casefold()
    return [term for term in terms_in(source, profile)
            if profile.expected(term).casefold() not in lowered]  # fmt: skip
