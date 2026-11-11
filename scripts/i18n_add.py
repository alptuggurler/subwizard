"""Add or update UI strings in every language file at once, keeping key parity.

    uv run python scripts/i18n_add.py strings.json

`strings.json` maps key -> {"tr": "...", "en": "..."}.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parent.parent / "src" / "subwizard" / "i18n"


def main() -> int:
    new = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    languages = sorted(p.stem for p in I18N_DIR.glob("*.json"))
    for key, texts in new.items():
        missing = set(languages) - texts.keys()
        if missing:
            print(f"{key}: missing {sorted(missing)}", file=sys.stderr)
            return 1
    for lang in languages:
        path = I18N_DIR / f"{lang}.json"
        strings = json.loads(path.read_text(encoding="utf-8"))
        strings.update({key: texts[lang] for key, texts in new.items()})
        path.write_text(json.dumps(strings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(new)} keys -> {', '.join(languages)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
