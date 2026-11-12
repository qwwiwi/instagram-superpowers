"""Edges of speech: the cut follows the word stamps with a pad, the sound energy only widens it, the air limit
holds after speed and the frame grid, and verify.py reads where the audio ends from its packet timestamps."""

from __future__ import annotations

import array
import math
import subprocess
from pathlib import Path

import pytest

from audio import ENV_RATE, Span, analyse_pcm
from common import OUT_FPS, Word, probe, quantize_keep
from phrases import build_phrases
from plan_cut import DEFAULT_HEAD_AIR, DEFAULT_TAIL_AIR, EDGE_LIMIT, make_plan
from verify import run_checks

from conftest import NEED_FFMPEG


def _pcm(levels: list[tuple[float, int]], rate: int = ENV_RATE) -> bytes:
    samples = array.array("h")
    for seconds, amplitude in levels:
        for i in range(int(round(seconds * rate))):
            samples.append(int(amplitude * math.sin(2 * math.pi * 200 * i / rate)))
    return samples.tobytes()


def _items(words: list[Word]) -> list[dict]:
    return [p.to_dict() for p in build_phrases(words)]


def _keep(plan: dict) -> tuple[float, float]:
    return plan["keep"][0]["start"], plan["keep"][-1]["end"]


# --- quiet phonemes inside the stamps survive, whatever the energy says ----------------------------------------


def test_quiet_consonants_at_both_ends_survive() -> None:
    # A take with almost no room noise (it is under 10 % of the file) and quiet consonants at both ends of the
    # voice (0.08-0.23 s and 2.23-2.38 s, about -35 dB). The pause threshold calls them silence; the cut must not
    # go inside the word stamps that hold them.
    raw = _pcm([(0.08, 30), (0.15, 600), (2.0, 8000), (0.15, 600), (0.08, 30)])
    sound = analyse_pcm(raw)
    words = [Word("сказал", 0.09, 1.2), Word("всё", 1.25, 2.38)]
    start, end = _keep(make_plan(words, 2.46, _items(words), [], quiet=sound.quiet))
    assert start <= 0.08 and end >= 2.38


def test_a_take_with_no_internal_pauses_widens_to_the_voice() -> None:
    # Continuous voice 0.40-2.40 s, room noise around it; the stamps are 0.05 s late on both sides. The energy
    # moves the edges OUT to the voice, and the pad is kept outside it.
    raw = _pcm([(0.4, 20), (2.0, 8000), (0.6, 20)])
    sound = analyse_pcm(raw)
    words = [Word("раз", 0.45, 1.4), Word("два", 1.45, 2.35)]
    plan = make_plan(words, 3.0, _items(words), [], quiet=sound.quiet)
    start, end = _keep(plan)
    assert plan["speech_edges"]["onset"] == pytest.approx(0.40, abs=0.03)
    assert plan["speech_edges"]["offset"] == pytest.approx(2.40, abs=0.03)
    assert start == pytest.approx(0.40 - DEFAULT_HEAD_AIR, abs=0.04)
    assert end == pytest.approx(2.40 + DEFAULT_TAIL_AIR, abs=0.04)


def test_noise_floor_near_the_speech_level_keeps_the_stamps_and_the_pad() -> None:
    # The room is almost as loud as the voice: every window counts as sound. The edges stay at the stamps with
    # the pad (no inward cut, no runaway outward cut through 1 s of noise).
    raw = _pcm([(1.0, 5000), (1.0, 8000), (1.0, 5000)])
    sound = analyse_pcm(raw)
    words = [Word("раз", 1.0, 1.5), Word("два", 1.55, 2.0)]
    start, end = _keep(make_plan(words, 3.0, _items(words), [], quiet=sound.quiet))
    assert start == pytest.approx(1.0 - DEFAULT_HEAD_AIR, abs=0.04)
    assert end == pytest.approx(2.0 + DEFAULT_TAIL_AIR, abs=0.04)


# --- stale stamps of a one-word take --------------------------------------------------------------------------


@pytest.mark.parametrize(("stamp", "voice"), [((1.30, 1.60), (0.50, 0.90)), ((0.20, 0.40), (0.80, 1.20))])
def test_stale_single_word_stamp_plans_and_keeps_the_voice(stamp: tuple[float, float],
                                                            voice: tuple[float, float]) -> None:
    words = [Word("нет", *stamp)]
    quiet = [Span(0.0, voice[0]), Span(voice[1], 2.0)]
    plan = make_plan(words, 2.0, _items(words), [], quiet=quiet)  # no PlanError
    start, end = _keep(plan)
    assert start <= voice[0] and end >= voice[1]  # the voice is kept
    assert start <= stamp[0] and end >= stamp[1]  # the stamp is never cut into


# --- the air limit, in output seconds, after speed and the frame grid --------------------------------------------


@pytest.mark.parametrize("speed", [1.0, 1.3, 0.5])
@pytest.mark.parametrize("air", [0.13, 0.11, 0.4])
def test_edge_air_stays_within_the_limit_after_speed_and_frames(speed: float, air: float) -> None:
    duration = 2.0
    onset, offset = air, duration - air
    words = [Word("раз", onset, 0.9), Word("два", 1.0, offset)]
    quiet = [Span(0.0, onset), Span(offset, duration)]
    plan = make_plan(words, duration, _items(words), [], speed=speed, quiet=quiet)
    keep = quantize_keep([(k["start"], k["end"]) for k in plan["keep"]], OUT_FPS)
    lead = (onset - keep[0][0]) / speed
    trail = (keep[-1][1] - offset) / speed
    assert lead <= EDGE_LIMIT + 1e-9 and trail <= EDGE_LIMIT + 1e-9
    assert keep[0][0] <= onset and keep[-1][1] >= offset  # no speech lost


# --- verify.py: where the audio ends -----------------------------------------------------------------------------


@NEED_FFMPEG
def test_speech_end_fails_when_the_audio_stops_before_the_video(tmp_path: Path) -> None:
    clip = tmp_path / "short-audio.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=2",
         "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(clip)],
        check=True,
    )
    info = probe(clip)
    assert info.duration > 1.8
    checks = {c.name: c for c in run_checks(clip, info, None, 90.0, 99.0, 5.0)}
    assert not checks["speech end"].ok, checks["speech end"].line()


@NEED_FFMPEG
def test_a_gap_inside_the_audio_is_not_silence_at_the_end(tmp_path: Path) -> None:
    # The tone has a 0.20 s timestamp gap at 0.5 s; audio and video both end at about 1.27 s. The decoded sound is
    # 0.2 s shorter than the timeline; the end must be read from the packet timestamps.
    audio = tmp_path / "gap.m4a"
    clip = tmp_path / "gap.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=1.066",
         "-af", "asetpts='if(gte(T,0.5),PTS+0.2/TB,PTS)'", "-c:a", "aac", str(audio)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=1.266667",
         "-i", str(audio), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
         "-shortest", str(clip)],
        check=True,
    )
    info = probe(clip)
    checks = {c.name: c for c in run_checks(clip, info, None, 90.0, 99.0, 5.0, max_trail=0.1)}
    assert checks["speech end"].ok, checks["speech end"].line()
