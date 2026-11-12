#!/usr/bin/env python3
"""Sound-energy helpers: where the audio is really quiet.

Recogniser word times drift, so a pause is trusted only where the sound
energy is low too. The silence threshold follows the speech level, not the
room noise: ``clamp(p75 - 6.5 dB, -46, -30)`` of the loudness envelope.

The edges of speech (first and last sound of the reel) use a second, lower threshold that follows the
room noise instead: ``clamp(p10 + 10 dB, -70, pause threshold)``. Quiet phonemes (a soft consonant at
the start of the first word, a fading ending of the last one) are well above the room noise but below
the pause threshold; with the pause threshold they would be cut off.
"""

from __future__ import annotations

import array
import itertools
import logging
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from common import MontageError, require_tool

LOG = logging.getLogger("reels.audio")

ENV_RATE = 16000
ENV_WINDOW_MS = 20
ENV_HOP_MS = 10
SPEECH_HEADROOM_DB = 6.5
FLOOR_MIN_DB = -46.0
FLOOR_MAX_DB = -30.0
SILENT_DB = -120.0
# A quiet stretch shorter than this is a gap between sounds, not a pause.
MIN_QUIET = 0.1
# Edges of speech: anything this far above the room noise is sound (a quiet phoneme counts).
EDGE_MARGIN_DB = 10.0
EDGE_MIN_DB = -70.0
NOISE_PERCENTILE = 0.10


@dataclass(frozen=True)
class Span:
    """A time range in seconds."""

    start: float
    end: float

    @property
    def length(self) -> float:
        """Length in seconds."""
        return self.end - self.start


def envelope_from_pcm(raw: bytes, rate: int = ENV_RATE) -> list[float]:
    """Loudness envelope (dB re full scale) of raw signed 16-bit mono PCM.

    Args:
        raw: Little-endian s16 samples, as ``ffmpeg -f s16le`` writes them.
        rate: Sample rate in Hz.

    Returns:
        One value per hop: RMS over a 20 ms window every 10 ms; -120 for digital silence.
    """
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()
    win = int(rate * ENV_WINDOW_MS / 1000)
    hop = int(rate * ENV_HOP_MS / 1000)
    cumulative = [0, *itertools.accumulate(v * v for v in samples)]
    out: list[float] = []
    for start in range(0, len(samples) - win + 1, hop):
        rms = math.sqrt((cumulative[start + win] - cumulative[start]) / win)
        out.append(20 * math.log10(rms / 32768) if rms > 0 else SILENT_DB)
    return out


def percentile_threshold(envelope_db: list[float]) -> float:
    """Silence threshold from the speech level of an envelope.

    Args:
        envelope_db: Loudness of consecutive windows in dB.

    Raises:
        ValueError: The envelope is empty.
    """
    if not envelope_db:
        raise ValueError("The loudness envelope is empty, there is no sound to measure")
    ordered = sorted(envelope_db)
    speech = ordered[min(len(ordered) - 1, int(0.75 * len(ordered)))]
    return max(FLOOR_MIN_DB, min(FLOOR_MAX_DB, speech - SPEECH_HEADROOM_DB))


def quiet_spans(envelope_db: list[float], threshold_db: float, min_len: float = MIN_QUIET) -> list[Span]:
    """Stretches where the envelope stays below the threshold.

    Args:
        envelope_db: Result of :func:`envelope_from_pcm`.
        threshold_db: Level below which the sound counts as quiet.
        min_len: Shortest stretch to report, seconds.

    Returns:
        Quiet spans in seconds, time order. The first and last window edges are the
        file edges, so a file that starts quiet reports a span from 0.
    """
    hop = ENV_HOP_MS / 1000
    win = ENV_WINDOW_MS / 1000
    spans: list[Span] = []
    run_start: int | None = None
    for index, level in enumerate(envelope_db):
        if level < threshold_db:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            spans.append(Span(run_start * hop, (index - 1) * hop + win))
            run_start = None
    if run_start is not None:
        spans.append(Span(run_start * hop, (len(envelope_db) - 1) * hop + win))
    return [s for s in spans if s.length >= min_len]


def edge_threshold(envelope_db: list[float], pause_threshold: float) -> float:
    """Threshold for the edges of speech: a little above the room noise, never above the pause threshold.

    Args:
        envelope_db: Loudness of consecutive windows in dB.
        pause_threshold: Result of :func:`percentile_threshold` for the same envelope.

    Raises:
        ValueError: The envelope is empty.
    """
    if not envelope_db:
        raise ValueError("The loudness envelope is empty, there is no sound to measure")
    ordered = sorted(envelope_db)
    noise = ordered[min(len(ordered) - 1, int(NOISE_PERCENTILE * len(ordered)))]
    return max(EDGE_MIN_DB, min(pause_threshold, noise + EDGE_MARGIN_DB))


@dataclass(frozen=True)
class SoundMap:
    """Quiet spans of an audio track for pauses and for the edges of speech."""

    quiet: list[Span]
    threshold: float
    edge_quiet: list[Span]
    edge_threshold: float
    # Length of the decoded audio, seconds; the video may be longer.
    audio_duration: float


def analyse_pcm(raw: bytes, rate: int = ENV_RATE) -> SoundMap:
    """Quiet spans of raw signed 16-bit mono PCM, for pauses and for the edges of speech.

    Raises:
        ValueError: There is no sound to measure.
    """
    envelope = envelope_from_pcm(raw, rate)
    threshold = percentile_threshold(envelope)
    edge = edge_threshold(envelope, threshold)
    return SoundMap(quiet_spans(envelope, threshold), threshold, quiet_spans(envelope, edge), edge,
                    (len(raw) // 2) / rate)


def analyse_audio(source: Path) -> SoundMap:
    """Decode the audio of a file and map its quiet spans (see :func:`analyse_pcm`).

    Raises:
        MontageError: ffmpeg is missing, cannot decode the file, or there is no sound.
    """
    require_tool("ffmpeg")
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(source), "-vn", "-f", "s16le", "-ac", "1",
         "-ar", str(ENV_RATE), "-"],
        capture_output=True, check=False,
    )
    if done.returncode != 0:
        raise MontageError(f"ffmpeg could not decode the audio of {source}: {done.stderr.decode(errors='replace')[-300:]}")
    try:
        sound = analyse_pcm(done.stdout)
    except ValueError as exc:
        raise MontageError(f"{source}: {exc}") from exc
    LOG.info("silence threshold %.1f dB (from the speech level), speech-edge threshold %.1f dB (from the room noise)",
             sound.threshold, sound.edge_threshold)
    return sound


def analyse_quiet(source: Path) -> tuple[list[Span], float]:
    """Decode the audio of a file and find its quiet spans (pause threshold).

    Returns:
        ``(quiet spans, threshold in dB)``.

    Raises:
        MontageError: ffmpeg is missing, cannot decode the file, or there is no sound.
    """
    sound = analyse_audio(source)
    return sound.quiet, sound.threshold
