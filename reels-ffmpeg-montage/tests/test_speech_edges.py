"""Edges of speech: quiet phonemes stay, a short answer is not lost, the 0.10 s edge limit holds after speed
and the frame grid, and a missing audio tail counts as silence."""

from __future__ import annotations

import array
import math
import subprocess
from pathlib import Path

import pytest

from audio import ENV_RATE, Span, analyse_pcm
from common import OUT_FPS, Word, quantize_keep
from phrases import build_phrases
from plan_cut import EDGE_LIMIT, make_plan, speech_edges
from verify import run_checks
from common import probe

from conftest import NEED_FFMPEG


def _pcm(levels: list[tuple[float, int]], rate: int = ENV_RATE) -> bytes:
    samples = array.array("h")
    for seconds, amplitude in levels:
        for i in range(int(round(seconds * rate))):
            samples.append(int(amplitude * math.sin(2 * math.pi * 200 * i / rate)))
    return samples.tobytes()


def _items(words: list[Word]) -> list[dict]:
    return [p.to_dict() for p in build_phrases(words)]


# --- finding 1: quiet phonemes at the edges are speech, not silence ---------------------------------------------


def test_quiet_phonemes_at_the_edges_are_kept() -> None:
    # Room noise (-61 dB), a quiet consonant 0.50-0.70 s (-41 dB), the loud vowel 0.70-1.40 s, a quiet consonant
    # 1.40-1.60 s, room noise to 2.6 s. The pause threshold (-30 dB) calls the consonants silence; the edges must not.
    raw = _pcm([(0.5, 30), (0.2, 300), (0.7, 8000), (0.2, 300), (1.0, 30)])
    sound = analyse_pcm(raw)
    assert sound.edge_threshold < -41.0 < sound.threshold
    words = [Word("сказал", 0.55, 1.0), Word("всё", 1.05, 1.55)]
    onset, offset = speech_edges(words, sound.edge_quiet)
    assert onset == pytest.approx(0.50, abs=0.03) and offset == pytest.approx(1.60, abs=0.03)
    plan = make_plan(words, 2.6, _items(words), [], quiet=sound.quiet, edge_quiet=sound.edge_quiet)
    assert plan["keep"][0]["start"] <= 0.50 and plan["keep"][-1]["end"] >= 1.60


# --- finding 2: a one-word answer with a stale stamp ----------------------------------------------------------------


def test_one_word_stamped_in_the_silence_before_its_sound_is_kept() -> None:
    # The stamp says 0.20-0.40 s, the voice sounds 0.60-1.00 s.
    words = [Word("да", 0.20, 0.40)]
    quiet = [Span(0.0, 0.6), Span(1.0, 2.0)]
    assert speech_edges(words, quiet, duration=2.0) == (0.6, 1.0)
    plan = make_plan(words, 2.0, _items(words), [], quiet=quiet)
    assert plan["keep"][0]["start"] <= 0.6 and plan["keep"][-1]["end"] >= 1.0


def test_one_word_stamped_in_the_silence_after_its_sound_is_kept() -> None:
    words = [Word("нет", 1.30, 1.60)]
    quiet = [Span(0.0, 0.5), Span(0.9, 2.0)]
    assert speech_edges(words, quiet, duration=2.0) == (0.5, 0.9)


# --- finding 3: at most 0.10 s of non-speech at each end, in output seconds, after the frame grid ------------------


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


# --- finding 4: a video longer than its audio ends on silence ------------------------------------------------------


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
