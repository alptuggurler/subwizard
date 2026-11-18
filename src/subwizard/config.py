"""Application config: `config.yaml` + env, validated with pydantic-settings. Qt-free.

Priority: explicit overrides (CLI) > config.yaml > environment (SUBWIZARD_*) > defaults.
Sections grow with each milestone; unknown keys are ignored so an older or newer
config file never prevents the app from starting.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

Language = Literal["auto", "tr", "en"]
ThemeMode = Literal["auto", "dark", "light"]
PerfOverlay = Literal["off", "compact", "detailed"]
AudioProfileName = Literal["low", "balanced", "safe"]


class ConfigError(Exception):
    """config.yaml could not be read or failed validation (message is user-facing)."""


class _Section(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="ignore")


class UIConfig(_Section):
    language: Language = "en"  # auto = the system language
    theme: ThemeMode = "dark"
    close_to_tray: bool = True
    show_in_dock: bool = True  # macOS: False = menu bar only (no Dock icon, no app menu)
    perf_overlay: PerfOverlay = "off"  # F9 cycles off -> compact -> detailed
    cursor_hide_ms: int = Field(2000, ge=0)  # fullscreen: hide idle cursor after this
    wizard_done: bool = False  # the setup wizard opens on start until finished or closed


class LoggingConfig(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    dir: Path | None = None


class SourceConfig(_Section):
    kind: Literal["capture", "file", "screen"] = "capture"
    device: int | str = 0
    path: Path | None = None
    width: int = Field(1920, gt=0)
    height: int = Field(1080, gt=0)
    fps: int = Field(60, gt=0)
    fourcc: str = Field("MJPG", min_length=4, max_length=4)
    buffer_size: int = Field(1, ge=1)
    loop: bool = True  # kind: file — restart the clip when it ends
    monitor: int = Field(1, ge=1)  # kind: screen — which monitor (1 = first)
    screen_fps: float = Field(10.0, ge=1, le=60)  # kind: screen — frames for OCR only
    reconnect_initial_s: float = Field(0.5, gt=0)  # backoff after a lost device
    reconnect_max_s: float = Field(8.0, gt=0)
    max_read_failures: int = Field(30, ge=1)  # consecutive failed reads = device lost


class AudioProfile(_Section):
    block: int = Field(gt=0)  # frames per callback
    buffer_ms: int = Field(ge=0)  # baseline delay between input and output


def _default_profiles() -> dict[str, AudioProfile]:
    return {
        "low": AudioProfile(block=128, buffer_ms=15),
        "balanced": AudioProfile(block=256, buffer_ms=30),
        "safe": AudioProfile(block=512, buffer_ms=60),
    }


class AudioConfig(_Section):
    mode: Literal["passthrough", "off"] = "passthrough"
    input_device: str | None = None  # null = not chosen yet
    output_device: str | None = None  # null = system default
    sample_rate: int = Field(48000, gt=0)
    channels: int = Field(2, ge=1, le=2)
    profile: AudioProfileName = "balanced"
    profiles: dict[str, AudioProfile] = Field(default_factory=_default_profiles)
    delay_ms: int = Field(0, ge=-200, le=500)  # A/V sync; audio can only be delayed
    gain_db: float = Field(0.0, ge=-60.0, le=6.0)
    muted: bool = False
    silence_db: float = -60.0  # below this for silence_s = "no audio" warning
    silence_s: float = Field(3.0, gt=0)
    clip_db: float = -0.1  # at or above this = clipping warning


RectList = Annotated[list[float], Field(min_length=4, max_length=4)]


class RoiConfig(_Section):
    template: Literal["bottom_center", "bottom_wide", "lower_half", "top", "custom"] = "bottom_center"
    # x, y, w, h as fractions of the frame; templates fill this in, F2 draws it.
    rect: list[float] = Field(default_factory=lambda: [0.15, 0.78, 0.70, 0.18],
                              min_length=4, max_length=4)  # fmt: skip
    padding_pct: float = Field(4.0, ge=0, le=50)  # grow every side by this % of the region
    # More regions read together with `rect` (e.g. a speaker name at the top), and areas
    # blacked out before OCR (minimap, HUD counters). Same x, y, w, h fractions.
    extra_rects: list[RectList] = Field(default_factory=list)
    exclude_rects: list[RectList] = Field(default_factory=list)


class PreprocessConfig(_Section):
    grayscale: bool = True
    contrast_stretch: bool = False
    threshold: Literal["none", "otsu", "adaptive"] = "none"
    upscale_below_px: int = Field(48, ge=0)  # regions shorter than this are enlarged 2x
    white_text_mask: bool = False  # keep only bright, unsaturated (white) text
    mask_min_value: int = Field(180, ge=0, le=255)
    mask_max_saturation: int = Field(60, ge=0, le=255)


def _default_preprocess() -> dict[str, PreprocessConfig]:
    return {
        "rapid": PreprocessConfig(),
        "windows": PreprocessConfig(),
        "tesseract": PreprocessConfig(contrast_stretch=True, threshold="otsu",
                                      upscale_below_px=400),  # fmt: skip
    }


class OCRConfig(_Section):
    engine: Literal["rapid", "windows"] = "rapid"  # windows: built-in Windows OCR (M7)
    hz: float = Field(4.0, gt=0, le=30)
    min_confidence: float = Field(0.6, ge=0, le=1)  # lines below this are dropped
    change_threshold: float = Field(2.0, ge=0)  # mean abs diff (0-255) below which OCR is skipped
    change_thumb_width: int = Field(160, ge=16)
    # A small part of the region (one cell of the thumbnail) changing this much is a
    # change too: a short line in a wide region hardly moves the whole-region mean. 0 = off.
    change_cell_threshold: float = Field(6.0, ge=0)
    # After this many skipped ticks in a row OCR runs anyway (a change too small to
    # notice, e.g. one letter). 0 = never forced.
    max_skips: int = Field(8, ge=0)
    # "gray": compare the whole region (SPEC 3.3). "bright": compare only pixels at least
    # `change_bright_min` bright, i.e. where subtitle letters are; a moving game scene
    # behind the subtitle then no longer counts as a change (measured in M2).
    change_mode: Literal["gray", "bright"] = "bright"
    change_bright_min: int = Field(180, ge=0, le=255)
    det_max_side: int = Field(960, ge=64)  # detection input is scaled down to this
    threads: int = Field(0, ge=0)  # 0 = onnxruntime decides
    preprocess: dict[str, PreprocessConfig] = Field(default_factory=_default_preprocess)

    def prep(self, engine: str | None = None) -> PreprocessConfig:
        """Preprocessing for `engine` (default: the chosen one); added if a saved config
        predates that engine."""
        return self.preprocess.setdefault(engine or self.engine, PreprocessConfig())


class MetricsConfig(_Section):
    short_window_s: float = Field(2.0, gt=0)
    long_window_s: float = Field(60.0, gt=0)
    ui_hz: float = Field(4.0, gt=0)  # how often the UI gets a snapshot
    # Color thresholds (green / yellow / red). FPS is relative to the requested rate.
    fps_warn_ratio: float = Field(0.965, gt=0, le=1)  # 58 of 60
    fps_bad_ratio: float = Field(0.8, gt=0, le=1)
    frame_age_warn_ms: float = Field(20.0, gt=0)
    frame_age_bad_ms: float = Field(50.0, gt=0)
    audio_latency_warn_ms: float = Field(80.0, gt=0)
    audio_latency_bad_ms: float = Field(150.0, gt=0)
    ocr_ms_warn: float = Field(250.0, gt=0)  # one OCR call
    ocr_ms_bad: float = Field(600.0, gt=0)
    translate_ms_warn: float = Field(1000.0, gt=0)  # one translation (cache misses)
    translate_ms_bad: float = Field(2500.0, gt=0)
    subtitle_ms_warn: float = Field(1500.0, gt=0)  # subtitle seen -> translation shown
    subtitle_ms_bad: float = Field(3000.0, gt=0)


class SubtitleConfig(_Section):
    """The subtitle state machine (SPEC 3.5)."""

    similarity: float = Field(0.85, ge=0, le=1)  # same line if at least this similar
    stable_repeats: int = Field(1, ge=0)  # extra sightings before a line is final
    empty_ticks: int = Field(2, ge=1)  # empty reads before the line counts as gone
    min_chars: int = Field(3, ge=0)  # shorter texts are noise
    min_letter_ratio: float = Field(0.5, ge=0, le=1)  # share of letters among non-spaces
    ignore_phrases: list[str] = Field(default_factory=list)  # regexes, removed before use
    # Drop text that is not the game's subtitle: lines already in the target language
    # (the console's own menus) and glyphs from other scripts read out of icons.
    skip_foreign: bool = True
    # "LUCIA: ...", "Jason: ...": 1-3 capitalised words before a colon.
    speaker_pattern: str = r"^((?:[A-Z][\w'.-]*\s?){1,3}):\s+"
    min_display_ms: int = Field(1500, ge=0)  # a shown translation stays at least this long
    # ...plus this much per character of the translation (a long line needs longer to
    # read), up to max_display_ms. 0 = always min_display_ms.
    ms_per_char: int = Field(50, ge=0)
    max_display_ms: int = Field(7000, ge=0)
    # A line ending with . ! ? … is sent on its first sighting (no second OCR tick).
    fast_punctuation: bool = True
    # Drop OCR garbage words ("ou$3e") and a HUD number stuck to the start of the line.
    clean_noise: bool = True
    # Keep every translated line per game in <data>/subtitles.sqlite (pipeline/corpus.py).
    save_lines: bool = True


class ModelPrice(_Section):
    input: float = Field(ge=0)  # USD per 1M tokens
    output: float = Field(ge=0)  # USD per 1M tokens, thinking tokens included
    # Input tokens Google serves from its implicit cache (the repeated system prompt).
    # None = a tenth of `input`, Google's usual cache discount.
    cached_input: float | None = Field(None, ge=0)

    def cached(self) -> float:
        return self.input / 10 if self.cached_input is None else self.cached_input


def _default_prices() -> dict[str, ModelPrice]:
    # Paid tier, standard, checked against ai.google.dev pricing on 2026-11-12.
    return {
        "gemini-3.5-flash-lite": ModelPrice(input=0.30, output=2.50, cached_input=0.03),
        "gemini-3.1-flash-lite": ModelPrice(input=0.25, output=1.50),
        "gemini-3.6-flash": ModelPrice(input=0.75, output=3.75),
        # Claude API, prompts under 100K tokens (platform.claude.com pricing, 2026-11-18).
        "claude-haiku-5-5": ModelPrice(input=0.10, output=0.50, cached_input=0.01),
    }


class TranslatorConfig(_Section):
    # "test": nothing is sent; the text that was read is shown as the translation.
    # "ollama": a local model on this PC (translator/ollama.py), free and offline.
    # "openrouter": the same prompts through openrouter.ai (prepaid, no Google billing).
    # "anthropic": Claude through the Claude API (prepaid credits, translator/anthropic_api.py).
    engine: Literal["gemini", "openrouter", "anthropic", "deep", "ollama", "test"] = "gemini"
    # "vision" (experimental, M7): the region image goes to Gemini, which reads and
    # translates it in one call (ocr/vision.py); needs a key, else local OCR is used.
    mode: Literal["ocr", "vision"] = "ocr"
    vision_media_resolution: Literal["low", "medium", "high"] = "medium"
    model: str = "gemini-3.5-flash-lite"
    # Translation starts turned off; the pause key (F8 by default) turns it on and off.
    start_paused: bool = False
    source_language: str = "en"
    target_language: str = "tr"
    context_lines: int = Field(5, ge=0)
    # A sentence shown in pieces is translated as a whole: the unfinished part so far is
    # sent with each new piece (pipeline/translate_worker.FragmentJoiner).
    merge_fragments: bool = True
    # Two speakers read at once ("Good. Lita: The way out is up ahead.") are translated
    # one by one and shown as one block, one line each.
    split_speakers: bool = True
    timeout_s: float = Field(2.5, gt=0)  # per Gemini request; on timeout -> fallback
    retries: int = Field(1, ge=0)  # extra tries on server / network errors
    thinking_level: Literal["minimal", "low", "medium", "high"] = "minimal"
    quota_cooldown_s: float = Field(60.0, ge=0)  # a key that hit 429 rests this long
    system_prompt: str = ""  # empty = built-in prompt (translator/prompt.py)
    fallback: bool = True  # use deep-translator when Gemini fails or has no key
    fallback_timeout_s: float = Field(4.0, gt=0)
    # Local model through Ollama: as the engine, or tried first when Gemini fails
    # (before Google / MyMemory) with local_fallback.
    local_fallback: bool = False
    ollama_model: str = "translategemma:4b"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_timeout_s: float = Field(4.0, gt=0)  # per line, once the model is loaded
    # engine "openrouter": prepaid USD balance, many models (translator/openrouter.py).
    # Uses timeout_s, retries, system_prompt, context and the daily limit like Gemini.
    openrouter_model: str = "google/gemini-2.5-flash-lite"
    # engine "anthropic": same shared settings; priced from `prices`.
    anthropic_model: str = "claude-haiku-5-5"
    offline_after: int = Field(3, ge=1)  # this many failed lines in a row = "no connection" notice
    cache_size: int = Field(2000, ge=0)  # in-memory LRU entries
    disk_cache: bool = True  # cache/translations.sqlite, kept across sessions
    # A line that differs from a cached one only by what OCR mixes up (I/l/1, O/0,
    # spacing, punctuation) uses the cached translation instead of a new request.
    loose_cache: bool = True
    prices: dict[str, ModelPrice] = Field(default_factory=_default_prices)
    # Gemini / OpenRouter / Claude stop for the day once today's cost reaches this (USD; the
    # fallback translates instead). 0 = no limit.
    daily_limit_usd: float = Field(0.0, ge=0)


class OverlayConfig(_Section):
    """How the Turkish subtitle looks on the video (SPEC 3.7). Sizes are % of the video."""

    visible: bool = True  # F10
    # "replace" (default) covers the English text with the translation in a dark rounded box,
    # like a game's own translated subtitle: no extra room on screen.
    # above: right over the English text, its size, no box (as if the game wrote it)
    position: Literal["above", "bottom", "top", "replace"] = "replace"
    font_family: str = ""  # "" = Segoe UI Variable -> Segoe UI -> Arial
    font_size_pct: float = Field(3.5, gt=0, le=20)  # of the video height
    bold: bool = True
    max_width_pct: float = Field(80.0, ge=10, le=100)  # word wrap width
    margin_pct: float = Field(1.5, ge=0, le=20)  # gap to the region / screen edge
    text_color: str = "#FFFFFF"
    background_color: str = "#000000"
    background_opacity: float = Field(0.95, ge=0, le=1)  # hides the English under the box
    speaker_color: str = "#FFD24A"
    outline: bool = True
    auto_bg: bool = False  # replace mode: the box takes the color around the English text
    show_source: bool = False  # also show the English line, small
    history_lines: int = Field(10, ge=1, le=50)  # F12 panel
    # How lines enter and leave (pipeline/subtitle_stack.py): the line before the current
    # one stays above it, small and dimmed, for a moment; lines fade instead of popping.
    stack_lines: int = Field(2, ge=1, le=3)  # 1 = only the current line
    fade_ms: int = Field(150, ge=0, le=1000)  # 0 = no fades, no gliding


def _default_hotkeys() -> dict[str, str]:
    return {
        "calibrate": "F2",
        "copy_last": "F5",
        "mute": "F6",
        "retranslate": "F7",
        "pause_translation": "F8",
        "perf_overlay": "F9",
        "toggle_subtitles": "F10",
        "fullscreen": "F11",
        "history_panel": "F12",
    }


class HotkeysConfig(_Section):
    # Global (works while the game window has focus) via pynput; Windows only, the
    # in-app shortcuts work everywhere.
    global_hotkeys: bool = True
    keys: dict[str, str] = Field(default_factory=_default_hotkeys)


DisplayMode = Literal["window", "web", "both"]


class DisplayConfig(_Section):
    mode: DisplayMode = "window"


class WebConfig(_Section):
    """Web overlay for a phone / tablet / OBS (SPEC 3.10). Runs when display.mode is
    `web` or `both`."""

    host: str = "0.0.0.0"  # local network; 127.0.0.1 = this PC only
    port: int = Field(8787, ge=0, le=65535)  # 0 = any free port
    token: str = ""  # optional: pages and /ws then need ?t=<token>
    font_px: int = Field(48, ge=8, le=200)  # page defaults, overridable with ?font=&lines=&source=
    lines: int = Field(3, ge=1, le=10)
    show_source: bool = False
    reconnect_ms: int = Field(1000, ge=100, le=60000)  # page retries the WebSocket this often
    heartbeat_s: float = Field(10.0, gt=0)  # WebSocket ping: notices a phone that went away


class TTSConfig(_Section):
    """Read subtitles aloud (SPEC 3.11, M7, optional)."""

    mode: Literal["off", "translation", "source"] = "off"  # source: read English, no translation
    # auto: SAPI on Windows, say on macOS; edge: online neural voices (good Turkish, internet)
    engine: Literal["auto", "sapi", "say", "edge"] = "auto"
    voice: str = ""  # engine voice id; empty: the first voice for the language
    rate: float = Field(1.0, ge=0.5, le=2.0)
    catch_up: Literal["skip", "speedup"] = "skip"  # lines pile up: drop old ones / talk faster


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUBWIZARD_",
        env_nested_delimiter="__",
        validate_assignment=True,
        extra="ignore",
    )

    ui: UIConfig = UIConfig()
    logging: LoggingConfig = LoggingConfig()
    source: SourceConfig = SourceConfig()
    audio: AudioConfig = AudioConfig()
    metrics: MetricsConfig = MetricsConfig()
    roi: RoiConfig = RoiConfig()
    ocr: OCRConfig = OCRConfig()
    subtitle: SubtitleConfig = SubtitleConfig()
    translator: TranslatorConfig = TranslatorConfig()
    overlay: OverlayConfig = OverlayConfig()
    hotkeys: HotkeysConfig = HotkeysConfig()
    display: DisplayConfig = DisplayConfig()
    web: WebConfig = WebConfig()
    tts: TTSConfig = TTSConfig()
    profile: str = "default"
    debug: bool = False


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _describe(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        where = ".".join(str(part) for part in err["loc"])
        lines.append(f"  - {where}: {err['msg']}")
    return "\n".join(lines)


def load_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> AppConfig:
    """Load and validate config. A missing file just means defaults."""
    data: dict[str, Any] = {}
    if path is not None and path.is_file():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"Ayar dosyası okunamadı: {path}\n  {exc}") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"Ayar dosyası geçersiz (en üst düzey bir sözlük olmalı): {path}")
        data = loaded
    if overrides:
        data = _deep_merge(data, overrides)
    try:
        return AppConfig(**data)
    except ValidationError as exc:
        source = path if path is not None else "ayarlar"
        raise ConfigError(f"Ayarlar geçersiz ({source}):\n{_describe(exc)}") from exc


def save_config(config: AppConfig, path: Path) -> None:
    """Write config atomically so a crash mid-save cannot corrupt the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        config.model_dump(mode="json"), sort_keys=False, allow_unicode=True
    )
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
