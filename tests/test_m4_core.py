"""M4 core: subtitle history + SRT log, hotkey names and conflicts."""

from subwizard.pipeline.history import SubtitleHistory, srt_time, to_srt, to_text
from subwizard.pipeline.subtitle_state import SubtitleLine
from subwizard.translator.base import TranslationResult
from subwizard.utils.hotkeys import GlobalHotkeys, conflicts, normalize_key, to_pynput


def _result(text, engine="gemini", fallback=False):
    return TranslationResult(text=text, engine=engine, latency_ms=100.0, fallback=fallback)


def test_srt_time_format():
    assert srt_time(0) == "00:00:00,000"
    assert srt_time(3723.456) == "01:02:03,456"


def test_history_records_lines_and_writes_srt_log(tmp_path):
    log = tmp_path / "logs" / "subtitles.srt"
    h = SubtitleHistory(started_at=100.0, log_path=log, clock=lambda: 105.0)
    h.add(SubtitleLine(1, "Keep your head down.", "LUCIA", 101.0))
    h.translated(1, _result("Kafanı eğ."))
    assert not log.exists()  # still on screen: end time not known yet
    h.close(1, 104.0)
    entry = h.entries()[0]
    assert (entry.start_s, entry.end_s, entry.translation) == (1.0, 4.0, "Kafanı eğ.")
    assert entry.source == "LUCIA: Keep your head down."
    text = log.read_text(encoding="utf-8")
    assert "1\n00:00:01,000 --> 00:00:04,000\nKafanı eğ.\nLUCIA: Keep your head down." in text


def test_translation_arriving_after_the_line_left_is_still_logged(tmp_path):
    log = tmp_path / "s.srt"
    h = SubtitleHistory(0.0, log, clock=lambda: 3.0)
    h.add(SubtitleLine(1, "Go!", None, 1.0))
    h.close(1, 2.0)
    h.translated(1, _result("Git!"))
    assert "Git!" in log.read_text(encoding="utf-8")


def test_typewriter_line_replaces_its_shorter_form():
    h = SubtitleHistory(0.0)
    h.add(SubtitleLine(1, "We need", None, 1.0))
    h.add(SubtitleLine(2, "We need to go.", None, 1.5, replaces=1))
    entries = h.entries()
    assert len(entries) == 1 and entries[0].line_id == 2 and entries[0].start_s == 1.0


def test_search_mark_bad_and_text_export():
    h = SubtitleHistory(0.0)
    h.add(SubtitleLine(1, "Run!", None, 1.0))
    h.add(SubtitleLine(2, "Hide.", None, 2.0))
    h.translated(1, _result("Koş!"))
    assert [e.line_id for e in h.search("koş")] == [1]
    assert [e.line_id for e in h.search("HIDE")] == [2]
    h.mark_bad(1)
    assert h.get(1).bad
    h.set_translation(1, "Kaç!", "deep", True)
    assert not h.get(1).bad and h.get(1).fallback  # a new translation clears the mark
    assert "Run!" in to_text(h.entries()) and "Kaç!" in to_text(h.entries())


def test_srt_gives_short_lines_a_minimum_duration():
    h = SubtitleHistory(0.0)
    h.add(SubtitleLine(1, "Hi", None, 1.0))
    h.close(1, 1.1)
    assert "00:00:01,000 --> 00:00:02,000" in to_srt(h.entries())


def test_close_all_closes_open_lines():
    h = SubtitleHistory(0.0, clock=lambda: 9.0)
    h.add(SubtitleLine(1, "A line", None, 1.0))
    h.close_all()
    assert h.entries()[0].end_s == 9.0


def test_normalize_and_conflicts():
    assert normalize_key(" ctrl + shift + s ") == "Ctrl+Shift+S"
    assert normalize_key("f12") == "F12"
    assert conflicts({"a": "F5", "b": "f5", "c": "", "d": "F6"}) == {"F5": ["a", "b"]}


def test_pynput_translation():
    assert to_pynput("Ctrl+F5") == "<ctrl>+<f5>"
    assert to_pynput("Alt+S") == "<alt>+s"
    assert to_pynput("Space") is None  # not expressible: in-app only, warned in the log


def test_global_hotkeys_do_nothing_off_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    assert GlobalHotkeys().start({"copy_last": "F5"}, lambda a: None) is False
