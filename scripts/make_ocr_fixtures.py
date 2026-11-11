"""Generate the OCR fixture frames in tests/fixtures/ocr/ (run once; results are committed).

    uv run python scripts/make_ocr_fixtures.py [--fonts-dir DIR]

Each case is a 1920x1080 "game frame" with a subtitle drawn the way games draw them
(outline or drop shadow, sometimes over a bright background), saved as JPEG so the
compression artifacts of an MJPG capture card are part of the test.
The expected text lives in cases.json next to the images.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ocr"
SIZE = (1920, 1080)
JPEG_QUALITY = 85
DEFAULT_FONTS = Path("/System/Library/Fonts/Supplemental")

CASES = [
    # name, lines, font file, px, style, background, color
    ("dialogue_outline", ["Jason: We need to get out of here, now."], "Arial Bold.ttf", 46, "outline", "city", "white"),
    ("two_lines", ["I told you, the money's in the trunk.", "Just keep driving and don't look back."], "Arial Bold.ttf", 42, "shadow", "night", "white"),
    ("bright_sky", ["Where the hell did you park the car?"], "Tahoma Bold.ttf", 44, "outline", "sky", "white"),
    ("small_font", ["You hear that? Somebody called the cops."], "Arial.ttf", 30, "shadow", "city", "white"),
    ("yellow_speaker", ["LUCIA: Not without the money."], "Trebuchet MS Bold.ttf", 46, "outline", "night", "yellow"),
    ("numbers_punct", ["Meet me at 7:30 on 42nd Street. Don't be late!"], "Arial Bold.ttf", 44, "shadow", "beach", "white"),
    ("serif", ["It's not about the cash, it's about respect."], "Georgia Bold.ttf", 44, "shadow", "city", "white"),
    ("box_background", ["Press the gas, we're losing them!"], "Arial Bold.ttf", 44, "box", "beach", "white"),
    ("empty", [], "Arial Bold.ttf", 44, "outline", "city", "white"),
]
COLORS = {"white": (245, 245, 245), "yellow": (250, 215, 60)}


def background(kind: str, rng: np.random.Generator) -> Image.Image:
    w, h = SIZE
    y = np.linspace(0, 1, h)[:, None, None]
    palettes = {
        "city": ((40, 45, 60), (120, 110, 100)),
        "night": ((10, 10, 25), (40, 30, 60)),
        "sky": ((150, 190, 235), (245, 245, 240)),  # bright behind white text
        "beach": ((90, 160, 210), (230, 210, 170)),
    }
    top, bottom = (np.array(c, dtype=np.float32) for c in palettes[kind])
    base = np.broadcast_to(top + (bottom - top) * y, (h, w, 3)).copy()
    img = Image.fromarray(base.astype(np.uint8))
    draw = ImageDraw.Draw(img)
    for _ in range(60):  # buildings, cars, palm trees: blurred shapes of random color
        x0, y0 = rng.integers(0, w), rng.integers(0, h)
        x1, y1 = x0 + rng.integers(40, 400), y0 + rng.integers(40, 300)
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        (draw.rectangle if rng.random() < 0.6 else draw.ellipse)((x0, y0, x1, y1), fill=color)
    img = img.filter(ImageFilter.GaussianBlur(6))
    noise = rng.normal(0, 6, (h, w, 3))
    return Image.fromarray(np.clip(np.asarray(img, np.float32) + noise, 0, 255).astype(np.uint8))


def draw_subtitle(img: Image.Image, lines: list[str], font: ImageFont.FreeTypeFont,
                  style: str, color: tuple[int, int, int]) -> None:  # fmt: skip
    draw = ImageDraw.Draw(img)
    w, h = img.size
    line_h = int(font.size * 1.25)
    top = int(h * 0.88) - line_h * len(lines)
    for i, line in enumerate(lines):
        text_w = draw.textlength(line, font=font)
        x, y = (w - text_w) / 2, top + i * line_h
        if style == "box":
            pad = font.size * 0.3
            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(overlay).rectangle((x - pad, y - pad / 2, x + text_w + pad, y + line_h),
                                              fill=(0, 0, 0, 150))  # fmt: skip
            img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
            draw = ImageDraw.Draw(img)
            draw.text((x, y), line, font=font, fill=color)
        elif style == "shadow":
            offset = max(2, font.size // 16)
            draw.text((x + offset, y + offset), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=color)
        else:  # outline
            draw.text((x, y), line, font=font, fill=color, stroke_width=max(2, font.size // 18),
                      stroke_fill=(0, 0, 0))  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fonts-dir", type=Path, default=DEFAULT_FONTS)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1119)  # GTA 6 release day; fixed so reruns are identical
    manifest = []
    for name, lines, font_file, px, style, bg, color in CASES:
        font = ImageFont.truetype(str(args.fonts_dir / font_file), px)
        img = background(bg, rng)
        draw_subtitle(img, lines, font, style, COLORS[color])
        path = OUT / f"{name}.jpg"
        img.save(path, quality=JPEG_QUALITY)
        manifest.append({"file": path.name, "text": " ".join(lines), "lines": len(lines),
                         "template": "bottom_center"})  # fmt: skip
        print(path, path.stat().st_size // 1024, "KB")
    (OUT / "cases.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
