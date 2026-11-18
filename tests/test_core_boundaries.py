"""The core must stay importable and usable without Qt (SPEC 3.12, M0 acceptance)."""

import subprocess
import sys
from datetime import datetime

import pytest

from subwizard.audio.base import BaseAudioPassthrough
from subwizard.ocr.base import BaseOCR, OCRLine, OCRResult
from subwizard.sources.base import BaseSource, VideoMode
from subwizard.translator.base import BaseTranslator
from subwizard.utils.logging import session_log_path, setup_logging

QT_FREE_MODULES = [
    "subwizard.cli",
    "subwizard.config",
    "subwizard.i18n",
    "subwizard.sources.base",
    "subwizard.audio.base",
    "subwizard.ocr.base",
    "subwizard.ocr.change_detect",
    "subwizard.ocr.preprocess",
    "subwizard.ocr.rapid",
    "subwizard.ocr.roi",
    "subwizard.ocr.worker",
    "subwizard.pipeline.board",
    "subwizard.pipeline.history",
    "subwizard.pipeline.subtitle_state",
    "subwizard.pipeline.text_utils",
    "subwizard.pipeline.translate_worker",
    "subwizard.core.secrets",
    "subwizard.pipeline.corpus",
    "subwizard.pipeline.subtitle_stack",
    "subwizard.translator.cache",
    "subwizard.translator.spend",
    "subwizard.translator.ollama",
    "subwizard.translator.openrouter",
    "subwizard.platform.macos.dock",
    "subwizard.translator.deep",
    "subwizard.translator.echo",
    "subwizard.translator.gemini",
    "subwizard.translator.prompt",
    "subwizard.translator.service",
    "subwizard.translator.test_run",
    "subwizard.translator.base",
    "subwizard.core.metrics",
    "subwizard.core.session",
    "subwizard.core.sysmon",
    "subwizard.sources.runner",
    "subwizard.sources.capture_card",
    "subwizard.sources.video_file",
    "subwizard.audio.passthrough",
    "subwizard.audio.devices",
    "subwizard.platform",
    "subwizard.utils.timing",
    "subwizard.utils.hotkeys",
    "subwizard.web.server",
    "subwizard.web.qr",
    "subwizard.core.support",
    "subwizard.core.metrics_log",
    "subwizard.utils.logging",
    "subwizard.profiles",
    "subwizard.audio.test_tone",
    "subwizard.ocr.vision",
    "subwizard.audio.avsync",
    "subwizard.sources.screen",
    "subwizard.tts",
    "subwizard.tts.base",
    "subwizard.tts.edge",
    "subwizard.tts.say",
    "subwizard.tts.worker",
    "subwizard.utils.paths",
]


def test_core_modules_do_not_import_qt():
    code = (
        "import importlib, sys\n"
        f"for name in {QT_FREE_MODULES!r}:\n"
        "    importlib.import_module(name)\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'qfluentwidgets'))\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


@pytest.mark.parametrize("base", [BaseSource, BaseOCR, BaseTranslator, BaseAudioPassthrough])
def test_bases_are_abstract(base):
    with pytest.raises(TypeError):
        base()


def test_ocr_result_aggregates_lines():
    result = OCRResult((OCRLine("Hello", 0.9, (0, 0, 1, 1)), OCRLine("there", 0.7, (0, 2, 1, 1))))
    assert result.text == "Hello there"
    assert result.confidence == pytest.approx(0.8)
    assert OCRResult().confidence == 0.0


def test_video_mode_reads_like_the_diagnostics_line():
    assert str(VideoMode(1920, 1080, 60.0, "MJPG")) == "1920×1080@60 MJPG"


def test_session_log_name_and_file(tmp_path):
    assert session_log_path(tmp_path, datetime(2026, 11, 19, 9, 5)).name == "session-20261119-0905.log"
    path = setup_logging("INFO", tmp_path / "logs")
    assert path is not None and path.exists()
    setup_logging("INFO", None)  # release the file handle


def test_unwritable_log_dir_degrades_to_console(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert setup_logging("INFO", blocker / "logs") is None
    setup_logging("INFO", None)
