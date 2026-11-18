"""Command line entry (developer / advanced use; SPEC 5). Qt is imported lazily."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from subwizard import APP_NAME, __version__
from subwizard.config import ConfigError, load_config
from subwizard.i18n import detect_system_language, i18n
from subwizard.utils.logging import setup_logging
from subwizard.utils.paths import default_config_path, default_log_dir

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_NOT_IMPLEMENTED = 3


def _device(value: str) -> int | str:
    """Capture device given as index (`1`) or name (`"USB Video"`)."""
    return int(value) if value.isdigit() else value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="subwizard", description=f"{APP_NAME} {__version__}")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    p.add_argument("--config", type=Path, help="config.yaml path (default: user data dir)")
    p.add_argument("--headless", action="store_true", help="no window, web overlay only")
    p.add_argument("--list-devices", action="store_true", help="list video + audio devices")
    p.add_argument("--source", choices=["capture", "file"], help="video source kind")
    p.add_argument("--device", type=_device, help="capture device index or name")
    p.add_argument("--path", type=Path, help="video file for --source file")
    p.add_argument("--audio-device", help="capture card audio input name")
    p.add_argument("--calibrate", action="store_true", help="open ROI calibration")
    p.add_argument("--translator", choices=["gemini", "openrouter", "deep", "ollama"], help="translation engine")
    p.add_argument("--profile", help="game profile name (profiles/<name>.yaml)")
    p.add_argument("--display", choices=["window", "web", "both"], help="Mode A / Mode B / both")
    p.add_argument("--debug", action="store_true", help="verbose logging + debug overlay")
    return p


def overrides_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """CLI flags as a nested dict in config.yaml shape. Only flags actually given."""
    out: dict[str, Any] = {}

    def put(section: str | None, key: str, value: Any) -> None:
        if value is None:
            return
        if section is None:
            out[key] = value
        else:
            out.setdefault(section, {})[key] = value

    put("source", "kind", args.source)
    put("source", "device", args.device)
    put("source", "path", args.path)
    put("audio", "input_device", args.audio_device)
    put("translator", "engine", args.translator)
    put("display", "mode", args.display)
    put(None, "profile", args.profile)
    if args.debug:
        put(None, "debug", True)
        put("logging", "level", "DEBUG")
    return out


def print_devices() -> None:
    """--list-devices: video + audio devices, as the names config.yaml accepts."""
    from subwizard import platform
    from subwizard.audio.devices import list_audio_devices
    from subwizard.i18n import t

    print(t("cli.video_devices"))
    video = platform.list_video_devices()
    if not video:
        print("  " + t("cli.no_video_devices"))
    for device in video:
        print(f"  [{device.index}] {device.name}")
        for mode in platform.list_video_modes(device.index):
            print(f"        {mode}")
    for kind, title in (("input", "cli.audio_inputs"), ("output", "cli.audio_outputs")):
        print(t(title))
        for device in list_audio_devices(kind):
            default = " *" if device.is_default else ""
            print(f"  [{device.index}] {device.name} ({device.hostapi}, {device.sample_rate} Hz){default}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.source == "file" and args.path is None:
        parser.error("--source file requires --path")

    config_path = args.config or default_config_path()
    try:
        config = load_config(config_path, overrides_from_args(args))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # Messages printed before the UI exists (errors, --list-devices) follow the
    # configured language too; the controller refines "auto" via Qt later.
    language = config.ui.language
    i18n.set_language(detect_system_language() if language == "auto" else language)

    log_file = setup_logging(config.logging.level, config.logging.dir or default_log_dir())
    log.info("%s %s başlıyor (ayar: %s, log: %s)", APP_NAME, __version__, config_path, log_file)

    # Not built yet: say so and exit non-zero rather than silently opening the GUI.
    if args.list_devices:
        print_devices()
        return EXIT_OK

    if args.headless:
        from subwizard.app import run_headless

        return run_headless(config, config_path)

    from subwizard.app import run_gui  # lazy: keeps the CLI importable without Qt

    return run_gui(config, config_path, calibrate=args.calibrate)
