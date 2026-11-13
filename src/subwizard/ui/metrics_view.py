"""Turns a metrics Snapshot into labelled, color-graded rows.

One source of truth for the F9 overlay, the Diagnostics page and the copied report,
so the three can never disagree about a number.
"""

from __future__ import annotations

from dataclasses import dataclass

from subwizard.config import AppConfig
from subwizard.core.metrics import Grade, M, Snapshot, grade
from subwizard.i18n import t
from subwizard.sources.capture_card import mode_hints

DASH = "—"
GRADE_COLORS: dict[Grade | None, str] = {
    "ok": "#3FB950",
    "warn": "#D29922",
    "bad": "#F85149",
    None: "",  # informational: theme text color
}


@dataclass(frozen=True, slots=True)
class Row:
    section: str  # i18n key of the group
    label: str  # i18n key
    value: str
    grade: Grade | None = None
    compact: bool = False  # shown in the compact F9 overlay


def target_fps(snap: Snapshot) -> float:
    requested, actual = snap.gauge(M.SOURCE_REQUESTED), snap.gauge(M.SOURCE_ACTUAL)
    if requested is not None:
        return requested.fps
    return actual.fps if actual is not None else 0.0


def _fps_grade(value: float, target: float, config: AppConfig) -> Grade | None:
    if target <= 0:
        return None
    m = config.metrics
    return grade(value, target * m.fps_warn_ratio, target * m.fps_bad_ratio)


def _db(value: float | None) -> str:
    return DASH if value is None else f"{value:.0f}"


def metric_rows(snap: Snapshot, config: AppConfig) -> list[Row]:
    m = config.metrics
    rows: list[Row] = []
    video, audio, system = "diag.section.video", "diag.section.audio", "diag.section.system"

    # --- video
    target = target_fps(snap)
    source_fps, render_fps = snap.rate(M.SOURCE_FPS), snap.rate(M.RENDER_FPS)
    rows.append(Row(video, "diag.source_fps", f"{source_fps.short:.1f}  ({source_fps.long:.1f})",
                    _fps_grade(source_fps.short, target, config), compact=True))  # fmt: skip
    game_fps = snap.rate(M.SOURCE_GAME_FPS)
    rows.append(Row(video, "diag.game_fps", f"{game_fps.short:.1f}  ({game_fps.long:.1f})",
                    None, compact=True))  # fmt: skip
    # Nothing is rendered while the video is not on screen (another page is open):
    # that is "not measured", not a 0 FPS problem.
    age = snap.stat(M.RENDER_FRAME_AGE_MS)
    drawing = age.count > 0
    rows.append(Row(video, "diag.render_fps",
                    f"{render_fps.short:.1f}  ({render_fps.long:.1f})" if drawing else DASH,
                    _fps_grade(render_fps.short, target, config) if drawing else None,
                    compact=True))  # fmt: skip
    rows.append(Row(video, "diag.frame_age",
                    f"{age.mean:.1f} / {age.worst:.1f} ms" if drawing else DASH,
                    grade(age.mean, m.frame_age_warn_ms, m.frame_age_bad_ms) if drawing else None,
                    compact=True))  # fmt: skip

    requested, actual = snap.gauge(M.SOURCE_REQUESTED), snap.gauge(M.SOURCE_ACTUAL)
    rows.append(Row(video, "diag.requested_mode", str(requested) if requested else DASH))
    mismatch = bool(mode_hints(requested, actual))
    backend = snap.gauge(M.SOURCE_BACKEND) or ""
    rows.append(Row(video, "diag.actual_mode",
                    f"{actual}  [{backend}]" if actual else DASH,
                    ("warn" if mismatch else "ok") if actual else None))  # fmt: skip
    interval = snap.stat(M.SOURCE_INTERVAL_MS)
    rows.append(Row(video, "diag.frame_interval",
                    f"{interval.mean:.1f} / {interval.worst:.1f} ms  ±{interval.jitter:.1f}"))  # fmt: skip
    dropped, repeated = snap.counter(M.SOURCE_DROPPED), snap.counter(M.SOURCE_REPEATED)
    rows.append(Row(video, "diag.dropped_repeated", f"{dropped} / {repeated}",
                    "warn" if dropped or repeated else "ok"))  # fmt: skip
    reconnects = snap.counter(M.SOURCE_RECONNECTS)
    rows.append(Row(video, "diag.reconnects", str(reconnects), "warn" if reconnects else None))

    # --- audio
    left, right = snap.gauge(M.AUDIO_LEVEL_L), snap.gauge(M.AUDIO_LEVEL_R)
    if snap.gauge(M.AUDIO_CLIPPING):
        level_grade: Grade | None = "bad"
    elif snap.gauge(M.AUDIO_SILENT):
        level_grade = "warn"
    else:
        level_grade = "ok" if left is not None else None
    rows.append(Row(audio, "diag.audio_level", f"{_db(left)} / {_db(right)} dBFS", level_grade))
    rate, block = snap.gauge(M.AUDIO_SAMPLE_RATE), snap.gauge(M.AUDIO_BLOCK)
    rows.append(Row(audio, "diag.audio_format",
                    f"{rate} Hz, {block}" if rate else DASH))  # fmt: skip
    latency = snap.gauge(M.AUDIO_LATENCY_MS)
    rows.append(Row(audio, "diag.audio_latency",
                    f"{latency:.0f} ms" if latency is not None else DASH,
                    grade(latency, m.audio_latency_warn_ms, m.audio_latency_bad_ms)
                    if latency is not None else None))  # fmt: skip
    under, over = snap.gauge(M.AUDIO_UNDERRUN), snap.gauge(M.AUDIO_OVERRUN)
    rows.append(Row(audio, "diag.audio_glitches",
                    f"{under} / {over}" if under is not None else DASH,
                    ("warn" if under or over else "ok") if under is not None else None))  # fmt: skip

    # --- OCR, translation
    rows.extend(_ocr_rows(snap, config))
    rows.extend(_translation_rows(snap, config))

    # --- system
    cpu_proc, cpu_total = snap.gauge(M.SYS_CPU_PROC, 0.0), snap.gauge(M.SYS_CPU_TOTAL, 0.0)
    rows.append(Row(system, "diag.cpu", f"{cpu_proc:.0f}% / {cpu_total:.0f}%"))
    ram_proc, ram_total = snap.gauge(M.SYS_RAM_PROC_MB, 0.0), snap.gauge(M.SYS_RAM_TOTAL, 0.0)
    rows.append(Row(system, "diag.ram", f"{ram_proc:.0f} MB / {ram_total:.0f}%"))
    return rows


OCR_TEXT_MAX_CHARS = 70  # longer text is cut in the overlay / Diagnostics


def _ocr_rows(snap: Snapshot, config: AppConfig) -> list[Row]:
    m = config.metrics
    ocr = "diag.section.ocr"
    runs, ticks = snap.rate(M.OCR_RUNS), snap.rate(M.OCR_TICKS)
    active = ticks.long > 0
    rows = [Row(ocr, "diag.ocr_rate",
                f"{runs.short:.1f} / {ticks.short:.1f}" if active else DASH, compact=True)]  # fmt: skip
    # Share of looked-at frames that needed no OCR call, over the long window.
    skipped = max(0.0, 1.0 - runs.long / ticks.long) if active else None
    rows.append(Row(ocr, "diag.ocr_skipped",
                    f"{skipped * 100:.0f}%  ({snap.counter(M.OCR_SKIPPED)})"
                    if skipped is not None else DASH))  # fmt: skip
    # OCR only runs when the subtitle changes, so the 2 s window is often empty:
    # fall back to the 60 s one rather than showing nothing.
    ms = snap.stat(M.OCR_MS)
    mean, worst = (ms.mean, ms.worst) if ms.count else (ms.mean_long, ms.worst_long)
    rows.append(Row(ocr, "diag.ocr_ms",
                    f"{mean:.0f} / {worst:.0f} ms" if mean else DASH,
                    grade(mean, m.ocr_ms_warn, m.ocr_ms_bad) if mean else None,
                    compact=True))  # fmt: skip
    confidence = snap.gauge(M.OCR_CONFIDENCE)
    rows.append(Row(ocr, "diag.ocr_confidence",
                    f"{confidence * 100:.0f}%" if confidence else DASH))  # fmt: skip
    text = snap.gauge(M.OCR_TEXT)
    if text and len(text) > OCR_TEXT_MAX_CHARS:
        text = text[: OCR_TEXT_MAX_CHARS - 1] + "…"
    rows.append(Row(ocr, "diag.ocr_text", text or DASH))
    return rows


def _ms_pair(stat) -> tuple[float, float]:  # noqa: ANN001
    """Short window if it has samples, else the long one (translations are sparse)."""
    return (stat.mean, stat.worst) if stat.count else (stat.mean_long, stat.worst_long)


def _translation_rows(snap: Snapshot, config: AppConfig) -> list[Row]:
    m = config.metrics
    sec = "diag.section.translation"
    rows = [Row(sec, "diag.tr_engine", snap.gauge(M.TR_ENGINE) or DASH)]
    # M4 acceptance target is the median (p50) over the last minute.
    sub = snap.stat(M.SUB_LATENCY_MS)
    rows.append(Row(sec, "diag.sub_latency",
                    f"{sub.p50_long:.0f} / {sub.worst_long:.0f} ms  (n={sub.count_long})"
                    if sub.count_long else DASH,
                    grade(sub.p50_long, m.subtitle_ms_warn, m.subtitle_ms_bad)
                    if sub.count_long else None, compact=True))  # fmt: skip
    mean, worst = _ms_pair(snap.stat(M.TR_MS))
    rows.append(Row(sec, "diag.tr_ms", f"{mean:.0f} / {worst:.0f} ms" if mean else DASH,
                    grade(mean, m.translate_ms_warn, m.translate_ms_bad) if mean else None))  # fmt: skip
    requests, hits = snap.counter(M.TR_REQUESTS), snap.counter(M.TR_CACHE_HITS)
    fallbacks, errors = snap.counter(M.TR_FALLBACKS), snap.counter(M.TR_ERRORS)
    rows.append(Row(sec, "diag.tr_counts", f"{requests} / {hits} / {fallbacks} / {errors}",
                    "warn" if fallbacks or errors else None))  # fmt: skip
    misses = snap.counter(M.TR_GLOSSARY_MISSES)
    rows.append(Row(sec, "diag.tr_glossary", str(misses), "warn" if misses else None))
    rows.append(Row(sec, "diag.tr_tokens",
                    f"{snap.counter(M.TR_TOKENS_IN)} / {snap.counter(M.TR_TOKENS_OUT)}"))  # fmt: skip
    cost, per_hour = snap.gauge(M.TR_COST), snap.gauge(M.TR_COST_PER_HOUR)
    rows.append(Row(sec, "diag.tr_cost",
                    f"${cost:.4f}  (~${per_hour or 0:.3f}/h)" if cost is not None else DASH,
                    compact=True))  # fmt: skip
    text = snap.gauge(M.TR_TEXT)
    if text and len(text) > OCR_TEXT_MAX_CHARS:
        text = text[: OCR_TEXT_MAX_CHARS - 1] + "…"
    rows.append(Row(sec, "diag.tr_text", text or DASH))
    return rows


def report_text(snap: Snapshot, config: AppConfig, header: list[str]) -> str:
    """Plain text for pasting into a GitHub issue (always English labels would hide
    nothing here, but the current UI language keeps it readable for the user)."""
    lines = list(header)
    section = None
    for row in metric_rows(snap, config):
        if row.section != section:
            section = row.section
            lines += ["", f"[{t(section)}]"]
        flag = {"warn": "  (!)", "bad": "  (!!)"}.get(row.grade or "", "")
        lines.append(f"{t(row.label)}: {row.value}{flag}")
    return "\n".join(lines)
