"""Timestamp remapping after cuts, the frame grid, and the render graphs."""

from __future__ import annotations

import pytest

from common import Word, build_map, quantize_keep, remap_words, speech_lost, tokens
from render_cut import (
    build_audio_graph,
    build_video_graph,
    cut_map_payload,
    loudnorm_second_pass,
    parse_loudnorm_json,
    snapping_damage,
)
from audio import Span
from common import MontageError
from verify import DEFAULT_MAX_LEAD, speech_margins


def test_words_move_left_by_the_removed_time() -> None:
    keep = [(0.0, 1.0), (2.0, 4.0), (5.0, 6.0)]
    words = [Word("а", 0.2, 0.5), Word("б", 2.1, 2.4), Word("в", 3.5, 3.9), Word("г", 5.2, 5.6)]
    out = remap_words(words, build_map(keep))
    assert [(w.text, w.start, w.end) for w in out] == [("а", 0.2, 0.5), ("б", 1.1, 1.4), ("в", 2.5, 2.9), ("г", 3.2, 3.6)]


def test_word_in_a_removed_stretch_is_dropped() -> None:
    keep = [(0.0, 1.0), (2.0, 3.0)]
    words = [Word("есть", 0.1, 0.4), Word("убран", 1.2, 1.6), Word("тоже", 2.2, 2.6)]
    out = remap_words(words, build_map(keep))
    assert [w.text for w in out] == ["есть", "тоже"]


def test_word_crossing_a_cut_edge_is_clamped_into_its_piece() -> None:
    keep = [(0.0, 1.0), (2.0, 3.0)]
    out = remap_words([Word("край", 0.8, 1.15)], build_map(keep))  # middle 0.975 is inside the first piece
    assert out[0].start == pytest.approx(0.8) and out[0].end == pytest.approx(1.0)


def test_speed_divides_the_new_clock() -> None:
    keep = [(0.0, 1.0), (2.0, 4.0)]
    segments = build_map(keep, speed=2.0)
    assert segments[-1].out_end == pytest.approx(1.5)
    out = remap_words([Word("б", 2.5, 3.0)], segments, speed=2.0)
    assert out[0].start == pytest.approx(0.5 + 0.25) and out[0].end == pytest.approx(0.5 + 0.5)


def test_new_times_are_monotonic_and_inside_the_output() -> None:
    keep = [(0.5, 1.5), (2.5, 3.0), (3.4, 5.0)]
    words = [Word(str(i), 0.5 + i * 0.3, 0.5 + i * 0.3 + 0.25) for i in range(15)]
    segments = build_map(keep, speed=1.3)
    out = remap_words(words, segments, speed=1.3)
    assert out
    starts = [w.start for w in out]
    assert starts == sorted(starts)
    assert all(0.0 <= w.start <= w.end <= segments[-1].out_end + 1e-6 for w in out)


def test_map_segments_are_back_to_back() -> None:
    segments = build_map([(1.0, 2.0), (4.0, 4.5)])
    assert [(s.out_start, s.out_end) for s in segments] == [(0.0, 1.0), (1.0, 1.5)]
    payload = cut_map_payload(__import__("pathlib").Path("x.mp4"), segments, 1.0)
    assert payload["seams"] == [1.0] and payload["expected_duration"] == 1.5


def test_quantize_snaps_to_frames_and_joins_touching_pieces() -> None:
    out = quantize_keep([(0.01, 1.004), (1.01, 2.0), (3.0, 3.01)], fps=30)
    assert out[0][0] == pytest.approx(0.0)
    assert out[0][1] == pytest.approx(2.0)  # the two touching pieces became one
    assert len(out) == 1  # the 0.3 frame sliver is gone
    for a, b in out:
        assert round(a * 30) == pytest.approx(a * 30) and round(b * 30) == pytest.approx(b * 30)


def test_quantize_rounds_outward_so_speech_at_the_edge_survives() -> None:
    # The last word ends at 4.013 s; rounding to the nearest frame (4.000) cut 13 ms of it.
    out = quantize_keep([(1.013, 4.013)], fps=30)
    assert out[0][0] <= 1.013 + 1e-9 and out[0][1] >= 4.013 - 1e-9
    assert out[0][0] == pytest.approx(1.0) and out[0][1] == pytest.approx(4.0333333)


def test_quantize_never_loses_a_second_of_a_piece_to_rounding() -> None:
    for start in (0.001, 0.016, 0.0166, 0.0167, 0.03, 0.5):
        for end in (1.0, 1.001, 1.016, 1.0167, 1.0334):
            (a, b), = quantize_keep([(start, end)], fps=30)
            assert a <= start + 1e-9 and b >= end - 1e-9


def test_speech_lost_after_quantisation_is_reported() -> None:
    words = [Word("раз", 1.0, 1.5), Word("два", 3.7, 4.013)]
    inward = [(1.0, 4.0)]  # what nearest-frame rounding used to give
    lost = speech_lost(words, inward)
    assert [w.text for w, _ in lost] == ["два"] and lost[0][1] == pytest.approx(0.013)
    assert speech_lost(words, quantize_keep([(1.0, 4.013)], fps=30)) == []


def test_snapping_damage_is_reported_only_for_what_snapping_added() -> None:
    words = [Word("раз", 1.0, 1.5), Word("два", 3.7, 4.013), Word("край", 4.6, 4.62)]
    plan = [(1.0, 4.013)]
    assert snapping_damage(words, plan, quantize_keep(plan)) == []
    damaged = snapping_damage(words, plan, [(1.0, 4.0)])  # inward rounding, as before the fix
    assert [w.text for w, _ in damaged] == ["два"]
    cut_by_plan = [(1.0, 3.9)]  # the plan itself cuts «два»: that is the plan's business, not the snapping's
    assert snapping_damage(words, cut_by_plan, quantize_keep(cut_by_plan)) == []


def test_tokens_normalise_case_punctuation_and_yo() -> None:
    assert tokens("Всё, ЕЩЁ — раз!") == ["все", "еще", "раз"]


# --- graphs -------------------------------------------------------------------------------------------------------


def test_audio_graph_fades_every_piece_and_applies_speed_last() -> None:
    graph = build_audio_graph([(0.0, 1.0), (2.0, 3.0)], speed=1.3, fade=0.008)
    assert graph.count("atrim=") == 2
    assert graph.count("afade=t=in") == 2 and graph.count("afade=t=out") == 2
    assert "d=0.008000" in graph
    assert graph.endswith("concat=n=2:v=0:a=1,atempo=1.3[aout]")
    assert "atempo" not in build_audio_graph([(0.0, 1.0)], 1.0, 0.008)


def test_video_graph_targets_1080x1920_at_30_fps() -> None:
    graph = build_video_graph([(0.0, 1.0), (2.0, 3.0)], speed=1.0)
    assert "scale=1080:1920" in graph and "pad=1080:1920" in graph and "fps=30" in graph
    assert graph.count("trim=") == 2 and graph.endswith("concat=n=2:v=1:a=0[vout]")
    assert "setpts=PTS/1.25,fps=30[vout]" in build_video_graph([(0.0, 1.0)], 1.25)


def test_loudnorm_json_is_taken_from_the_last_block() -> None:
    text = 'noise {"not": "it"}\n[Parsed_loudnorm_0] \n{\n "input_i" : "-23.50",\n "input_tp" : "-4.1",\n "input_lra" : "3.2",\n "input_thresh" : "-34.0",\n "target_offset" : "0.5"\n}\nmore lines'
    measured = parse_loudnorm_json(text)
    flt = loudnorm_second_pass(measured)
    assert "measured_I=-23.50" in flt and "linear=true" in flt and "I=-14" in flt
    with pytest.raises(MontageError):
        parse_loudnorm_json("nothing here")


def test_silent_audio_is_a_clear_error() -> None:
    measured = {"input_i": "-inf", "input_tp": "-inf", "input_lra": "0.0", "input_thresh": "-70.0", "target_offset": "inf"}
    with pytest.raises(MontageError, match="silent"):
        loudnorm_second_pass(measured)


def test_speech_margins_measure_the_non_speech_at_both_ends() -> None:
    quiet = [Span(0.0, 0.65), Span(3.0, 3.4), Span(9.8, 10.0)]
    assert speech_margins(quiet, 10.0) == (0.65, pytest.approx(0.2))
    assert speech_margins([Span(3.0, 3.4)], 10.0) == (0.0, 0.0)


def test_verify_fails_a_reel_that_opens_on_dead_air() -> None:
    lead, _ = speech_margins([Span(0.0, 0.65)], 10.0)
    assert lead > DEFAULT_MAX_LEAD


def test_speech_end_counts_a_container_longer_than_the_audio() -> None:
    # The audio stops 0.07 s before the end of the container; the quiet tail still counts.
    _, trail = speech_margins([Span(45.73, 46.33)], 46.4)
    assert trail == pytest.approx(0.67)
