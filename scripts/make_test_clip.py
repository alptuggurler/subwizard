"""Generate a synthetic "game" clip with English subtitles for development and tests.

    uv run python scripts/make_test_clip.py [--out clip.mp4] [--size 1920x1080] [--fps 60] [--seconds 12]

Every frame is unique (moving background + frame counter), so dropped and repeated
frames are detectable; subtitle lines change every few seconds for the OCR stages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

SUBTITLES = [
    "",
    "Jason: We need to get out of here, now.",
    "Lucia: Not without the money.",
    "",
    "You hear that? Somebody called the cops.",
    "Just drive. I'll handle the rest.",
]
SUBTITLE_SECONDS = 2.0


def make_clip(path: Path, width: int, height: int, fps: int, seconds: float) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"cannot open video writer for {path}")
    scale = height / 1080
    xs = np.linspace(0, 255, width, dtype=np.float32)[None, :]
    ys = np.linspace(0, 255, height, dtype=np.float32)[:, None]
    font = cv2.FONT_HERSHEY_DUPLEX
    try:
        for i in range(int(fps * seconds)):
            shift = i * 2.0
            frame = np.empty((height, width, 3), dtype=np.uint8)
            frame[..., 0] = ((xs + shift) % 255 * 0.5 + 40).astype(np.uint8)
            frame[..., 1] = ((ys + shift) % 255 * 0.4 + 30).astype(np.uint8)
            frame[..., 2] = (((xs + ys) / 2 + shift) % 255 * 0.3 + 20).astype(np.uint8)

            cv2.putText(frame, f"frame {i:05d}", (int(30 * scale), int(60 * scale)), font,
                        1.2 * scale, (255, 255, 255), max(1, int(2 * scale)), cv2.LINE_AA)  # fmt: skip

            text = SUBTITLES[int(i / fps / SUBTITLE_SECONDS) % len(SUBTITLES)]
            if text:
                size, thickness = 1.5 * scale, max(1, int(2 * scale))
                (text_w, _), _ = cv2.getTextSize(text, font, size, thickness)
                origin = ((width - text_w) // 2, int(height * 0.9))
                shadow = (origin[0] + max(1, int(3 * scale)), origin[1] + max(1, int(3 * scale)))
                cv2.putText(frame, text, shadow, font, size, (0, 0, 0), thickness + 2, cv2.LINE_AA)
                cv2.putText(frame, text, origin, font, size, (255, 255, 255), thickness, cv2.LINE_AA)
            writer.write(frame)
    finally:
        writer.release()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("clip.mp4"))
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--seconds", type=float, default=12.0)
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.lower().split("x"))
    make_clip(args.out, width, height, args.fps, args.seconds)
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
