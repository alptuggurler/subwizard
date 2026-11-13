"""Translated lines kept per game (pipeline/corpus.py): one row per game and English text."""

import sqlite3

from subwizard.pipeline.corpus import SubtitleCorpus


def rows(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT game, source, translation, speaker, engine, complete, times "
                          "FROM lines ORDER BY id").fetchall()  # fmt: skip


def test_lines_are_kept_per_game_and_repeats_are_counted(tmp_path):
    path = tmp_path / "data" / "subtitles.sqlite"
    gta = SubtitleCorpus(path, "gta6", clock=lambda: 0.0)
    gta.record("Turn  that off.", "Kapat şunu.", "LUCIA", "gemini", True)
    gta.record("Turn that off.", "Şunu kapat.", None, "gemini", True)  # seen again
    gta.record("I was going to", "Ben şey yapacaktım", None, "mymemory", False)
    gta.record("", "boş", None, "gemini", True)
    assert gta.count() == 2
    gta.close()
    other = SubtitleCorpus(path, "default")  # same file, another game
    other.record("Turn that off.", "Kapat.", None, "gemini", True)
    assert (other.count(), other.count("gta6")) == (1, 2)
    other.close()
    other.record("After close.", "Kapandıktan sonra.", None, "gemini", True)  # no error
    assert rows(path) == [
        ("gta6", "Turn that off.", "Şunu kapat.", "LUCIA", "gemini", 1, 2),
        ("gta6", "I was going to", "Ben şey yapacaktım", None, "mymemory", 0, 1),
        ("default", "Turn that off.", "Kapat.", None, "gemini", 1, 1),
    ]


def test_a_broken_file_does_not_stop_the_session(tmp_path):
    path = tmp_path / "subtitles.sqlite"
    path.mkdir()  # a directory where the file should be
    corpus = SubtitleCorpus(path, "gta6")
    corpus.record("Hello.", "Selam.", None, "gemini", True)
    assert corpus.count() == 0
    corpus.close()


def test_session_keeps_the_translated_sentence_but_not_test_echoes(tmp_path):
    from types import SimpleNamespace

    from subwizard.core.session import Session
    from subwizard.pipeline.subtitle_state import SubtitleLine
    from subwizard.pipeline.translate_worker import Translated
    from subwizard.translator.base import TranslationResult

    path = tmp_path / "subtitles.sqlite"
    session = SimpleNamespace(
        history=SimpleNamespace(translated=lambda *a: None), _on_translated=lambda item: None,
        _corpus=SubtitleCorpus(path, "gta6"),
        _config=SimpleNamespace(tts=SimpleNamespace(mode="off")),
        _board=SimpleNamespace(translated=lambda *a: False),
    )  # fmt: skip
    piece = SubtitleLine(2, "do what's easy.", "ANTON", 1.0, None)
    whole = "Yara did not elect me to do what's easy."
    Session._translation_done(session, Translated(piece, TranslationResult("Tam cümle.", "gemini", 5.0), whole))
    Session._translation_done(session, Translated(piece, TranslationResult(piece.text, "test", 0.0), piece.text))
    Session._translation_done(session, Translated(piece, None, piece.text))
    session._corpus.close()
    assert rows(path) == [("gta6", whole, "Tam cümle.", "ANTON", "gemini", 1, 1)]
