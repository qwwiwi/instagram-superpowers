"""Regression tests on a real transcript (tests/fixtures/real_take_words.json)."""

from __future__ import annotations

from pathlib import Path

import pytest

from audio import Span
from common import Word, sanitize_words
from phrases import build_phrases
from plan_cut import PlanError, make_plan, main as plan_main

from conftest import real_take


def clean_words() -> tuple[list[Word], float]:
    take = real_take()
    words, _ = sanitize_words(take.words, take.duration)
    return words, take.duration


def spoken_inside_keep(words: list[Word], plan: dict) -> float:
    """Seconds of speech that survive in the kept pieces, minus the total speech: 0 means nothing lost."""
    total = sum(w.end - w.start for w in words)
    kept = sum(
        max(0.0, min(w.end, k["end"]) - max(w.start, k["start"])) for w in words for k in plan["keep"]
    )
    return kept - total


def test_default_plan_on_the_real_take_is_built() -> None:
    # Before the fix: PlanError "The cut would eat speech of kept phrases (phrase 1: 0.28 s)":
    # phrase 1 holds a 0.53 s pause after the first word and the guard counted that cut as lost speech.
    words, duration = clean_words()
    items = [p.to_dict() for p in build_phrases(words)]
    plan = make_plan(words, duration, items, drop=[])
    assert plan["drop"] == []
    assert spoken_inside_keep(words, plan) == pytest.approx(0.0, abs=1e-6)
    inner = [r for r in plan["removals"] if r["kind"] == "pause" and r["start"] < 1.18]
    assert inner, "the pause after the first word is still trimmed"


def test_default_plan_with_quiet_spans_of_the_gaps() -> None:
    words, duration = clean_words()
    items = [p.to_dict() for p in build_phrases(words)]
    # The quiet sound is exactly the gaps between words, plus the head and the tail.
    edges = [0.0] + [x for w in words for x in (w.start, w.end)] + [duration]
    quiet = [Span(a, b) for a, b in zip(edges[::2], edges[1::2]) if b - a > 0.05]
    plan = make_plan(words, duration, items, drop=[], quiet=quiet)
    assert spoken_inside_keep(words, plan) == pytest.approx(0.0, abs=1e-6)


def test_cli_default_plan_on_the_real_take_exits_zero(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "real_take_words.json"
    code = plan_main([str(fixture), "--no-energy", "--out", str(tmp_path / "plan.json"), "--report", str(tmp_path / "plan.md")])
    assert code == 0
    assert (tmp_path / "plan.json").is_file()


def test_auto_cut_shrinks_to_the_speech_instead_of_refusing() -> None:
    # The phrase table (edited by hand) says there is speech inside the pause; the word list does not.
    words = [Word("раз", 0.2, 0.6), Word("два", 3.0, 3.4)]
    items = [
        {"id": 1, "core_start": 0.2, "core_end": 1.6, "text": "раз", "words": [
            {"text": "раз", "start": 0.2, "end": 0.6}, {"text": "ещё", "start": 1.2, "end": 1.6}]},
        {"id": 2, "core_start": 3.0, "core_end": 3.4, "text": "два"},
    ]
    plan = make_plan(words, 4.0, items, drop=[])
    speech = [(0.2, 0.6), (1.2, 1.6)]
    for r in plan["removals"]:
        for lo, hi in speech:
            assert r["end"] <= lo + 1e-9 or r["start"] >= hi - 1e-9, r
    assert [r for r in plan["removals"] if r["kind"] == "pause"], "the pauses around the speech are still trimmed"
    assert any("shortened" in n for n in plan["notes"])


def test_drop_that_cuts_kept_speech_names_the_phrase_and_the_remedy() -> None:
    words = [Word("раз", 0.2, 0.6), Word("два", 1.0, 1.4), Word("три", 3.0, 3.4)]
    # Phrase 2 claims to end at 1.4 s but phrase 1 (kept) also claims 0.9-1.1 s: the cut of phrase 2 starts inside it.
    items = [
        {"id": 1, "core_start": 0.2, "core_end": 1.1, "text": "раз", "words": [
            {"text": "раз", "start": 0.2, "end": 0.6}, {"text": "ну", "start": 0.9, "end": 1.1}]},
        {"id": 2, "core_start": 1.0, "core_end": 1.4, "text": "два"},
        {"id": 3, "core_start": 3.0, "core_end": 3.4, "text": "три"},
    ]
    with pytest.raises(PlanError) as info:
        make_plan(words, 4.0, items, drop=[2])
    message = str(info.value)
    assert "phrase 1" in message and "--drop" in message and "--no-energy" not in message
