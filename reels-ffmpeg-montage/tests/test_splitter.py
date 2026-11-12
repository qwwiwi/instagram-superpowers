"""Phrase splitter: no phrase ends on a preposition or conjunction after a short pause."""

from __future__ import annotations

from common import Word, sanitize_words
from phrases import FUNCTION_WORDS, DEFAULT_HARD_PAUSE, build_phrases, ends_on_function_word

from conftest import real_take


def real_phrases() -> list:
    take = real_take()
    words, _ = sanitize_words(take.words, take.duration)
    return build_phrases(words)


def test_phrase_does_not_end_on_a_preposition_after_a_short_pause() -> None:
    # Before: «...я боялся утонуть в» | «море кода...» (0.39 s pause after «в»).
    texts = [p.text for p in real_phrases()]
    assert not any(t.endswith(" в") for t in texts)
    assert any("утонуть в море кода" in t for t in texts)


def test_no_real_phrase_ends_on_a_function_word_unless_the_pause_is_long() -> None:
    phrases = real_phrases()
    for before, after in zip(phrases, phrases[1:]):
        if ends_on_function_word(before.words[-1].text):
            assert after.core_start - before.core_end >= DEFAULT_HARD_PAUSE, before.text


def test_real_take_second_half_is_not_cut_mid_phrase() -> None:
    # Before: «...и у» | «тебя получится разобраться» | «в CloudCode за выходные.»
    texts = [p.text for p in real_phrases()]
    assert not any(t.endswith(" и у") for t in texts)
    assert any("и у тебя получится разобраться" in t for t in texts)


def test_long_pause_stays_a_hard_break_even_after_a_function_word() -> None:
    long_gap = [Word("мы", 0.0, 0.3), Word("идём", 0.35, 0.8), Word("в", 0.85, 0.95), Word("море", 3.0, 3.5), Word("кода", 3.55, 4.0)]
    assert [p.text for p in build_phrases(long_gap, min_pause=0.32, min_phrase=0.3, hard_pause=1.5)] == ["мы идём в", "море кода"]
    short_gap = [Word("мы", 0.0, 0.3), Word("идём", 0.35, 0.8), Word("в", 0.85, 0.95), Word("море", 1.5, 2.0), Word("кода", 2.05, 2.5)]
    assert [p.text for p in build_phrases(short_gap, min_pause=0.32, min_phrase=0.3, hard_pause=1.5)] == ["мы идём в море кода"]


def test_sentence_end_and_comma_clause_break_are_kept() -> None:
    # «и.» with a full stop ends a sentence: the pause after it is a real break.
    words = [Word("это", 0.0, 0.4), Word("и.", 0.45, 0.8), Word("дальше", 1.4, 1.9), Word("текст", 1.95, 2.4)]
    assert [p.text for p in build_phrases(words, min_pause=0.32, min_phrase=0.3)] == ["это и.", "дальше текст"]


def test_function_word_detection_ignores_case_and_punctuation() -> None:
    assert ends_on_function_word("В") and ends_on_function_word("и,") and ends_on_function_word("«на»")
    assert not ends_on_function_word("море") and not ends_on_function_word("в.") and not ends_on_function_word("")
    assert "в" in FUNCTION_WORDS and "the" in FUNCTION_WORDS


def test_chain_of_function_words_joins_through() -> None:
    words = [Word("зайди", 0.0, 0.4), Word("и", 0.8, 0.9), Word("в", 1.3, 1.4), Word("дом", 1.8, 2.2)]
    assert [p.text for p in build_phrases(words, min_pause=0.32, min_phrase=0.3)] == ["зайди", "и в дом"]
