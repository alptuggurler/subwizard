"""M5: support bundle — contents, secret masking, missing logs."""

import zipfile

from subwizard.config import AppConfig
from subwizard.core.support import make_support_bundle, scrub

KEY = "AIza" + "B" * 35


def test_bundle_has_logs_config_report_and_no_secrets(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "session-20261112-0100.log").write_text(
        f"start\nkey loaded {KEY}\nGET /ws?t=hunter22secret\n", encoding="utf-8")
    (logs / "subtitles-20261112-0100.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nKoş!\n",
                                                       encoding="utf-8")  # fmt: skip
    config = AppConfig()
    config.web.token = "hunter22secret"
    out = make_support_bundle(tmp_path / "out" / "b.zip", config, logs, "report: 60 FPS")
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        everything = "".join(z.read(n).decode("utf-8") for n in names)
    assert {"system.txt", "config.yaml", "diagnostics.txt", "logs/session-20261112-0100.log",
            "logs/subtitles-20261112-0100.srt"} <= names  # fmt: skip
    assert KEY not in everything and "hunter22secret" not in everything
    assert "Koş!" in everything and "60 FPS" in everything


def test_bundle_without_logs(tmp_path):
    out = make_support_bundle(tmp_path / "b.zip", AppConfig(), tmp_path / "nope")
    with zipfile.ZipFile(out) as z:
        assert set(z.namelist()) == {"system.txt", "config.yaml"}


def test_scrub_masks_keys_and_tokens():
    text = scrub(f"k={KEY} url http://x/?t=abcdefgh&lines=3 api_key=zzzzzzzzzz")
    assert KEY not in text and "abcdefgh" not in text and "zzzzzzzzzz" not in text
    assert "lines=3" in text


def test_metrics_recorder_keeps_one_row_per_interval_and_writes_csv():
    from subwizard.core.metrics import M, MetricsHub
    from subwizard.core.metrics_log import COLUMNS, MetricsRecorder

    now = [100.0]
    rec = MetricsRecorder(max_rows=3, interval_s=1.0, clock=lambda: now[0])
    hub = MetricsHub()
    hub.observe(M.OCR_MS, 40.0)
    assert rec.record(hub.snapshot())
    assert not rec.record(hub.snapshot())  # same second
    for _ in range(4):
        now[0] += 1.0
        rec.record(hub.snapshot())
    assert len(rec) == 3  # bounded
    lines = rec.to_csv().splitlines()
    assert lines[0].split(",") == ["time", *COLUMNS]
    assert len(lines) == 4 and "40.0" in lines[1]
