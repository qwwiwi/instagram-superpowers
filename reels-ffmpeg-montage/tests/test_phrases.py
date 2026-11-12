"""Phrase table built from word timestamps."""

from __future__ import annotations

import pytest

from common import MontageError, Word, load_words, sanitize_words, save_words, WordsFile
from phrases import build_phrases, format_table, load_phrase_items, main as phrases_main


def w(text: str, start: float, end: float) -> Word:
    return Word(text, start, end)


def test_split_on_pause_and_join_short_phrase() -> None:
    words = [w("раз", 1.0, 1.3), w("два", 1.35, 1.7), w("три", 1.75, 2.1),
             w("ну", 3.0, 3.2),  # alone: 0.2 s long, joins the previous phrase
             w("четыре", 4.5, 4.9), w("пять", 4.95, 5.4)]
    phrases = build_phrases(words, min_pause=0.32, min_phrase=0.6)
    assert [p.text for p in phrases] == ["раз два три ну", "четыре пять"]
    assert [p.id for p in phrases] == [1, 2]


def test_first_short_phrase_joins_next() -> None:
    words = [w("а", 0.5, 0.7), w("давайте", 2.0, 2.5), w("начнём", 2.55, 3.1)]
    phrases = build_phrases(words)
    assert len(phrases) == 1
    assert phrases[0].text == "а давайте начнём"


def test_pause_before_and_padding_never_exceed_half_the_gap() -> None:
    words = [w("первая", 0.2, 0.9), w("фраза", 0.95, 1.5), w("вторая", 1.7, 2.3), w("фраза", 2.35, 2.9)]
    phrases = build_phrases(words, min_pause=0.15, min_phrase=0.3, pad=0.15)
    assert len(phrases) == 2
    first, second = phrases
    assert first.pause_before == pytest.approx(0.2)  # from the start of the file
    assert second.pause_before == pytest.approx(0.2)
    # The gap between the phrases is 0.2 s, so each side keeps at most 0.1 s of it.
    assert first.pad_after <= 0.1 + 1e-9 and second.pad_before <= 0.1 + 1e-9
    assert first.end <= second.start + 1e-9
    assert first.start >= 0.0


def test_table_has_ids_times_text_and_pause() -> None:
    phrases = build_phrases([w("привет", 0.5, 1.2), w("мир", 1.25, 1.8), w("пока", 3.0, 3.8)], min_phrase=0.3)
    table = format_table(phrases)
    lines = table.splitlines()
    assert "pause before" in lines[0]
    assert lines[2].startswith("| 1 | 0.50 | 1.80 |")
    assert "привет мир" in lines[2]
    assert lines[3].startswith("| 2 | 3.00 | 3.80 |") and "| 1.20 |" in lines[3]


def test_table_escapes_pipe_in_text() -> None:
    table = format_table(build_phrases([w("a|b", 0.0, 0.8)]))
    assert "a/b" in table


def test_sanitize_drops_invented_words_and_caps_absurd_durations() -> None:
    words = [w("привет", 0.0, 29.86), w("мир", 1.0, 1.4), w("", 1.5, 1.6), w("призрак", 12.5, 13.0)]
    clean, dropped = sanitize_words(words, duration=10.0)
    assert dropped == 2
    assert [c.text for c in clean] == ["привет", "мир"]
    assert clean[0].end <= 1.0  # capped, and never past the next word
    assert clean[1].end == pytest.approx(1.4)


def test_sanitize_keeps_recogniser_order() -> None:
    words = [w("б", 2.0, 2.3), w("а", 1.0, 1.3)]
    clean, _ = sanitize_words(words, duration=5.0)
    assert [c.text for c in clean] == ["б", "а"]


def test_cli_writes_json_and_table(tmp_path) -> None:
    words_file = tmp_path / "words.json"
    save_words(words_file, WordsFile(words=[w("привет", 0.5, 1.2), w("мир", 1.25, 1.8), w("пока", 3.0, 3.8)], duration=5.0, language="ru"))
    out, table = tmp_path / "phrases.json", tmp_path / "phrases.md"
    code = phrases_main([str(words_file), "--out", str(out), "--table", str(table), "--min-phrase", "0.3"])
    assert code == 0
    items = load_phrase_items(out)
    assert [i["id"] for i in items] == [1, 2]
    assert "привет мир" in table.read_text(encoding="utf-8")
    assert load_words(words_file).duration == 5.0


def test_cli_rejects_a_file_that_is_not_words(tmp_path) -> None:
    bad = tmp_path / "words.json"
    bad.write_text('{"nothing": 1}', encoding="utf-8")
    assert phrases_main([str(bad), "--out", str(tmp_path / "p.json"), "--table", str(tmp_path / "p.md")]) == 2
    with pytest.raises(MontageError):
        load_words(bad)
