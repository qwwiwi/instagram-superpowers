"""Timestamp remapping after cuts, the frame grid, and the render graphs."""

from __future__ import annotations

import pytest

from common import Word, build_map, quantize_keep, remap_words, tokens
from render_cut import (
    build_audio_graph,
    build_video_graph,
    cut_map_payload,
    loudnorm_second_pass,
    parse_loudnorm_json,
)
from common import MontageError


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
