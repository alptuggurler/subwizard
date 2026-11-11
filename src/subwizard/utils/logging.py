"""Logging setup: console + `logs/session-YYYYMMDD-HHMM.log` (SPEC 3.9)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"
ROOT_LOGGER = "subwizard"


def session_log_path(log_dir: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return log_dir / f"session-{stamp}.log"


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> Path | None:
    """Configure the `subwizard` logger. Returns the session log file, if any.

    A log directory that cannot be created must never stop the app from starting,
    so file logging degrades to console-only with a warning.
    """
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(level.upper())
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = False

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_dir is None:
        return None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = session_log_path(log_dir)
        file_handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        logger.warning("Log dosyası açılamadı (%s); yalnızca konsola yazılıyor.", exc)
        return None
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return path
