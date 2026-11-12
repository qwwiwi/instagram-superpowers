"""Cut plan logic: pause trimming, drops, candidates, validity."""

from __future__ import annotations

import pytest

from audio import Span, envelope_from_pcm, percentile_threshold, quiet_spans
from common import Word
from phrases import build_phrases
from plan_cut import (
    PlanError,
    check_keep_covers_speech,
    find_candidates,
    keep_from_removals,
    make_plan,
    parse_drop_list,
    plan_drop_removals,
    plan_pause_removals,
    validate_keep,
    Removal,
)

from conftest import CLIP_DURATION, fake_words


def items_of(words: list[Word]) -> list[dict]:
    return [p.to_dict() for p in build_phrases(words)]


def total(keep: list[dict]) -> float:
    return sum(k["end"] - k["start"] for k in keep)


# --- pauses -------------------------------------------------------------------------------------------------


def test_long_pause_is_trimmed_to_target_gap() -> None:
    words = [Word("раз", 0.2, 0.6), Word("два", 2.0, 2.4)]
    removals, notes = plan_pause_removals(words, 2.6, min_pause=0.4, target_gap=0.25, head_air=0.2, tail_air=0.5)
    pause = [r for r in removals if r.kind == "pause"]
    assert len(pause) == 1
    # 1.4 s gap -> 0.25 s left: 0.125 s of air on each side of the cut
    assert pause[0].start == pytest.approx(0.6 + 0.125)
    assert pause[0].end == pytest.approx(2.0 - 0.125)
    assert notes == []


def test_short_pause_is_left_alone() -> None:
    words = [Word("раз", 0.2, 0.6), Word("два", 0.9, 1.3)]  # 0.3 s gap, threshold 0.4
    removals, _ = plan_pause_removals(words, 1.5, min_pause=0.4, tail_air=0.5)
    assert [r for r in removals if r.kind == "pause"] == []


def test_head_and_tail_silence_keep_their_air() -> None:
    words = [Word("раз", 1.5, 1.9), Word("два", 1.95, 2.3)]
    removals, _ = plan_pause_removals(words, 5.0, head_air=0.2, tail_air=0.5)
    head = next(r for r in removals if r.kind == "head")
    tail = next(r for r in removals if r.kind == "tail")
    assert (head.start, head.end) == (0.0, pytest.approx(1.3))
    assert (tail.start, tail.end) == (pytest.approx(2.8), 5.0)


def test_no_pause_is_cut_where_the_sound_is_not_quiet() -> None:
    words = [Word("раз", 0.2, 0.6), Word("два", 2.0, 2.4)]
    removals, notes = plan_pause_removals(words, 2.6, quiet=[Span(0.0, 0.2), Span(2.4, 2.6)])
    assert [r for r in removals if r.kind == "pause"] == []
    assert len(notes) == 1 and "not quiet" in notes[0]


def test_pause_cut_stays_inside_the_quiet_stretch() -> None:
    words = [Word("раз", 0.2, 0.6), Word("два", 2.0, 2.4)]
    quiet = [Span(0.7, 1.5)]  # a cough from 1.5 s on
    removals, _ = plan_pause_removals(words, 2.6, quiet=quiet, head_air=0.0, tail_air=10.0)
    pause = [r for r in removals if r.kind == "pause"]
    assert len(pause) == 1
    assert pause[0].start >= 0.7 + 0.125 - 1e-9 and pause[0].end <= 1.5 - 0.125 + 1e-9


# --- drops: the script never drops meaning on its own ---------------------------------------------------------


def test_nothing_is_dropped_without_a_confirmed_list() -> None:
    words = fake_words()
    plan = make_plan(words, CLIP_DURATION, items_of(words), drop=[])
    assert plan["drop"] == []
    assert not [r for r in plan["removals"] if r["kind"] == "drop"]
    flagged = {c["phrase"] for c in plan["candidates"]}
    assert flagged == {1, 3}
    # Every spoken second is still inside the kept pieces: only silence went.
    spoken = sum(w.end - w.start for w in words)
    covered = 0.0
    for w in words:
        covered += sum(max(0.0, min(w.end, k["end"]) - max(w.start, k["start"])) for k in plan["keep"])
    assert covered == pytest.approx(spoken, abs=1e-6)


def test_candidates_kinds_and_pointers() -> None:
    words = fake_words()
    found = {c["phrase"]: c for c in find_candidates(items_of(words))}
    assert found[1]["kind"] == "false_start" and found[1]["similar_to"] == 2
    assert found[3]["kind"] == "repeat" and found[3]["similar_to"] == 4 and found[3]["similarity"] == 1.0
    assert 2 not in found and 4 not in found and 5 not in found


def test_candidates_ignore_unrelated_phrases() -> None:
    words = [Word("привет", 0.0, 0.5), Word("друзья", 0.55, 1.1), Word("сегодня", 2.0, 2.5), Word("поговорим", 2.55, 3.2), Word("о", 3.25, 3.3), Word("монтаже", 3.35, 4.0)]
    assert find_candidates(items_of(words)) == []


def test_confirmed_drops_remove_the_phrases_and_keep_air() -> None:
    words = fake_words()
    plan = make_plan(words, CLIP_DURATION, items_of(words), drop=[1, 3])
    assert plan["drop"] == [1, 3]
    drops = [r for r in plan["removals"] if r["kind"] == "drop"]
    assert len(drops) == 2
    first, second = drops
    # P1 starts the take: cut from 0 to 0.2 s before P2 (3.6 s)
    assert first["start"] == 0.0 and first["end"] == pytest.approx(3.4)
    # P3 (6.7-7.85) sits between P2 (ends 5.95) and P4 (starts 8.5): 0.125 s of air stays on each side
    assert second["start"] == pytest.approx(5.95 + 0.125)
    assert second["end"] == pytest.approx(8.5 - 0.125)
    # The words of phrases 2, 4 and 5 are whole inside the kept pieces
    for w in words:
        if w.start >= 3.6 and not (6.7 <= w.start < 8.0):
            assert any(k["start"] <= w.start and w.end <= k["end"] for k in plan["keep"]), w
    # and the words of the dropped phrases are gone
    for w in words:
        if w.start < 3.0 or 6.7 <= w.start < 7.9:
            assert not any(k["start"] < w.end and w.start < k["end"] for k in plan["keep"]), w


def test_drop_of_unknown_phrase_and_of_everything_is_refused() -> None:
    items = items_of(fake_words())
    with pytest.raises(PlanError, match="not in the table"):
        plan_drop_removals(items, [99], CLIP_DURATION)
    with pytest.raises(PlanError, match="nothing would be left"):
        plan_drop_removals(items, [1, 2, 3, 4, 5], CLIP_DURATION)


def test_drop_without_quiet_edge_is_refused() -> None:
    items = items_of(fake_words())
    with pytest.raises(PlanError, match="REFUSED"):
        plan_drop_removals(items, [3], CLIP_DURATION, quiet=[Span(0.0, 0.5)])


def test_drop_at_the_end_keeps_the_tail_air() -> None:
    items = items_of(fake_words())
    removals = plan_drop_removals(items, [5], CLIP_DURATION, head_air=0.2, tail_air=0.5)
    assert removals[0].end == CLIP_DURATION
    # P4 ends at 9.65 s, P5 starts at 10.2 s; the 0.5 s tail air does not fit into that gap, so the
    # cut starts in the middle of it instead of eating into the kept phrase.
    assert removals[0].start == pytest.approx((9.65 + 10.2) / 2)


def test_parse_drop_list() -> None:
    assert parse_drop_list("3, 5-7,3") == [3, 5, 6, 7]
    assert parse_drop_list("") == []
    with pytest.raises(PlanError):
        parse_drop_list("a,b")
    with pytest.raises(PlanError):
        parse_drop_list("7-5")


# --- speed, validity, totals --------------------------------------------------------------------------------------


def test_speed_changes_only_the_output_length() -> None:
    words = fake_words()
    base = make_plan(words, CLIP_DURATION, items_of(words), drop=[1, 3])
    fast = make_plan(words, CLIP_DURATION, items_of(words), drop=[1, 3], speed=1.25)
    assert fast["keep"] == base["keep"]
    assert fast["stats"]["output_seconds"] == pytest.approx(base["stats"]["kept_seconds"] / 1.25)
    assert fast["speed"] == 1.25


@pytest.mark.parametrize("speed", [0.4, 2.5])
def test_speed_outside_the_atempo_range_is_refused(speed: float) -> None:
    words = fake_words()
    with pytest.raises(PlanError, match="--speed"):
        make_plan(words, CLIP_DURATION, items_of(words), drop=[], speed=speed)


def test_keep_is_the_complement_of_the_removals() -> None:
    keep = keep_from_removals([Removal(1.0, 2.0, "pause"), Removal(5.0, 6.0, "tail")], 6.0)
    assert keep == [(0.0, 1.0), (2.0, 5.0)]
    assert keep_from_removals([Removal(0.0, 6.0, "drop")], 6.0) == []


def covered(word: Word, keep: list[dict]) -> bool:
    return any(k["start"] <= word.start + 1e-3 and word.end <= k["end"] + 1e-3 for k in keep)


def test_target_gap_zero_does_not_delete_a_short_word_between_two_pauses() -> None:
    # The middle word is 40 ms long; both pauses around it are cut right up to it, which leaves a 40 ms kept piece.
    words = [Word("раз", 0.2, 0.7), Word("а", 2.00, 2.04), Word("три", 3.0, 3.5)]
    plan = make_plan(words, 4.0, items_of(words), drop=[], target_gap=0.0)
    assert all(covered(w, plan["keep"]) for w in words), plan["keep"]
    assert all(k["end"] - k["start"] >= 0.05 - 1e-6 for k in plan["keep"])


def test_short_piece_at_the_edge_is_widened_not_deleted() -> None:
    words = [Word("а", 0.0, 0.03), Word("два", 1.5, 2.0)]
    plan = make_plan(words, 2.5, items_of(words), drop=[], target_gap=0.0, head_air=0.0)
    assert all(covered(w, plan["keep"]) for w in words), plan["keep"]


def test_keep_check_refuses_a_plan_that_loses_speech() -> None:
    words = [Word("раз", 0.2, 0.7), Word("а", 2.00, 2.04)]
    with pytest.raises(PlanError, match="loses"):
        check_keep_covers_speech(words, [(0.1, 0.8)], [])


def test_validate_keep_rejects_overlap_and_empty() -> None:
    validate_keep([(0.0, 1.0), (2.0, 3.0)], 3.0)
    with pytest.raises(PlanError):
        validate_keep([(0.0, 2.0), (1.0, 3.0)], 3.0)
    with pytest.raises(PlanError):
        validate_keep([], 3.0)
    with pytest.raises(PlanError):
        validate_keep([(0.0, 5.0)], 3.0)


def test_pause_cut_that_would_touch_a_kept_phrase_is_shortened() -> None:
    # The phrase table says phrase 1 runs to 2.0 s; the word list has a gap from 1.0 s on. The pause cut shrinks
    # to start after the phrase instead of the plan being refused.
    words = [Word("раз", 0.2, 1.0), Word("два", 3.0, 3.4)]
    items = [{"id": 1, "core_start": 0.2, "core_end": 2.0, "text": "раз"}, {"id": 2, "core_start": 3.0, "core_end": 3.4, "text": "два"}]
    plan = make_plan(words, 4.0, items, drop=[])
    pause = [r for r in plan["removals"] if r["kind"] == "pause"]
    assert len(pause) == 1 and pause[0]["start"] == pytest.approx(2.0)
    assert any("shortened" in n for n in plan["notes"])


# --- energy helpers ---------------------------------------------------------------------------------------------------


def _pcm(levels: list[tuple[float, int]], rate: int = 16000) -> bytes:
    import array
    import math

    samples = array.array("h")
    for seconds, amplitude in levels:
        for i in range(int(seconds * rate)):
            samples.append(int(amplitude * math.sin(2 * math.pi * 200 * i / rate)))
    return samples.tobytes()


def test_quiet_spans_found_in_pcm() -> None:
    raw = _pcm([(0.5, 0), (1.0, 8000), (0.5, 0), (1.0, 8000)])
    envelope = envelope_from_pcm(raw)
    threshold = percentile_threshold(envelope)
    assert -46.0 <= threshold <= -30.0
    spans = quiet_spans(envelope, threshold)
    assert len(spans) == 2
    assert spans[0].start == pytest.approx(0.0, abs=0.02) and spans[0].end == pytest.approx(0.5, abs=0.03)
    assert spans[1].start == pytest.approx(1.5, abs=0.03) and spans[1].length == pytest.approx(0.5, abs=0.04)


def test_empty_envelope_is_an_error() -> None:
    with pytest.raises(ValueError):
        percentile_threshold([])
