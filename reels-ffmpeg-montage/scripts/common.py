#!/usr/bin/env python3
"""Shared helpers of the reels-ffmpeg-montage scripts.

Holds what several scripts need: logging setup, running ffmpeg/ffprobe, the
media probe, the words file format and the timeline remapping that moves word
timestamps from the source to the cut video.
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

LOG = logging.getLogger("reels")

JSON_VERSION = 1
# Output frame rate of the montage; the cut grid is built on it.
OUT_FPS = 30
OUT_WIDTH = 1080
OUT_HEIGHT = 1920
# A cut shorter than this is not worth making.
MIN_CUT = 0.08
# A kept piece shorter than this is dropped (a sliver between two cuts).
MIN_KEEP = 0.05
# A word that starts more than this after the end of the media is invented by the recogniser.
WORD_END_TOLERANCE = 0.02


class MontageError(Exception):
    """A user-facing failure: the message says what happened and what to do."""


def setup_logging(verbose: bool = False) -> None:
    """Configure logging to stderr.

    Args:
        verbose: Show debug messages too.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def require_tool(name: str) -> str:
    """Return the path of an external tool or fail with an install hint.

    Args:
        name: Executable name, ``ffmpeg`` or ``ffprobe``.

    Raises:
        MontageError: The tool is not on PATH.
    """
    found = shutil.which(name)
    if not found:
        raise MontageError(
            f"`{name}` not found on PATH. Install ffmpeg (it ships ffmpeg and ffprobe) and run again."
        )
    return found


def run_ffmpeg(args: Sequence[str], what: str = "ffmpeg") -> subprocess.CompletedProcess[str]:
    """Run ffmpeg with the given arguments and return the finished process.

    Args:
        args: Arguments after the program name.
        what: Short description used in the error message.

    Raises:
        MontageError: ffmpeg is missing or exits with a non-zero code.
    """
    require_tool("ffmpeg")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", *args]
    LOG.debug("run: %s", " ".join(cmd))
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        tail = done.stderr.strip()[-700:]
        raise MontageError(f"{what} failed (ffmpeg exit {done.returncode}): {tail}")
    return done


# --- probe -------------------------------------------------------------------------------------------


@dataclass
class MediaInfo:
    """Facts about a media file taken from ffprobe."""

    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    has_audio: bool
    audio_codec: str
    sample_rate: int
    channels: int


def parse_rate(raw: str) -> float:
    """Turn an ffprobe rate such as ``30000/1001`` into a float (0 when unknown)."""
    try:
        if "/" in raw:
            num, den = raw.split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(raw)
    except ValueError:
        return 0.0


def probe(path: Path) -> MediaInfo:
    """Read duration, size, frame rate and audio facts of a media file.

    Args:
        path: Media file.

    Raises:
        MontageError: ffprobe is missing, the file is unreadable, or it has no video.
    """
    require_tool("ffprobe")
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=False,
    )
    if done.returncode != 0:
        raise MontageError(f"ffprobe cannot read {path}: {done.stderr.strip()[-300:]}")
    data = json.loads(done.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MontageError(f"{path} has no video stream")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    fps = parse_rate(str(video.get("avg_frame_rate", "0"))) or parse_rate(str(video.get("r_frame_rate", "0")))
    return MediaInfo(
        duration=duration,
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=fps,
        video_codec=str(video.get("codec_name", "")),
        has_audio=audio is not None,
        audio_codec=str(audio.get("codec_name", "")) if audio else "",
        sample_rate=int(audio.get("sample_rate", 0)) if audio else 0,
        channels=int(audio.get("channels", 0)) if audio else 0,
    )


# --- JSON files ----------------------------------------------------------------------------------------


def write_json(path: Path, data: Any) -> None:
    """Write a JSON document (UTF-8, readable, Cyrillic kept as is)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    """Read a JSON document.

    Raises:
        MontageError: The file is missing or is not valid JSON.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MontageError(f"File not found: {path}") from exc
    except ValueError as exc:
        raise MontageError(f"{path} is not valid JSON: {exc}") from exc


# --- words -------------------------------------------------------------------------------------------------


@dataclass
class Word:
    """One recognised word, seconds on the clock of the file it belongs to."""

    text: str
    start: float
    end: float

    def to_dict(self) -> dict[str, Any]:
        """JSON form of the word."""
        return {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}


@dataclass
class WordsFile:
    """Content of a ``words.json`` / ``words-cut.json``."""

    words: list[Word]
    duration: float
    language: str = ""
    source: str = ""


def parse_words(items: Any) -> list[Word]:
    """Read a list of word objects (keys ``text`` or ``word``, ``start``, ``end``).

    Raises:
        MontageError: An entry has no text or no usable times.
    """
    if not isinstance(items, list):
        raise MontageError("`words` must be a list")
    out: list[Word] = []
    for item in items:
        if not isinstance(item, dict):
            raise MontageError(f"A word entry is not an object: {item!r}")
        text = item.get("text", item.get("word"))
        start, end = item.get("start"), item.get("end", item.get("start"))
        numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (start, end))
        if not isinstance(text, str) or not numbers:
            raise MontageError(f"A word entry needs text, start and end: {item!r}")
        out.append(Word(text=text.strip(), start=float(start), end=max(float(end), float(start))))
    return out


def load_words(path: Path) -> WordsFile:
    """Read a words file written by ``transcribe.py`` or ``render_cut.py``.

    Raises:
        MontageError: The file is not a words file.
    """
    data = read_json(path)
    if not isinstance(data, dict) or "words" not in data:
        raise MontageError(f"{path} is not a words file (no `words` list)")
    words = parse_words(data["words"])
    duration = data.get("duration")
    if not isinstance(duration, (int, float)):
        duration = max((w.end for w in words), default=0.0)
    return WordsFile(
        words=words,
        duration=float(duration),
        language=str(data.get("language", "")),
        source=str(data.get("source", "")),
    )


def save_words(path: Path, wf: WordsFile) -> None:
    """Write a words file."""
    write_json(path, {
        "version": JSON_VERSION,
        "source": wf.source,
        "language": wf.language,
        "duration": round(wf.duration, 3),
        "words": [w.to_dict() for w in wf.words],
    })


def sanitize_words(words: list[Word], duration: float) -> tuple[list[Word], int]:
    """Clean the recogniser's words without re-sorting them.

    The order given by the recogniser is kept. Words with no text, or that
    start beyond the end of the media (the recogniser invents text past the end
    of the audio), are dropped. An end time is clamped to the media length, to the
    start of the next word, and to a length plausible for the word (a word with a
    29 s duration is a recogniser artefact, not speech).

    Args:
        words: Words as recognised.
        duration: Media length in seconds; 0 or less skips the length checks.

    Returns:
        ``(clean words, number of dropped words)``.
    """
    kept: list[Word] = []
    for word in words:
        if not word.text:
            continue
        if duration > 0 and word.start > duration + WORD_END_TOLERANCE:
            continue
        kept.append(Word(word.text, max(0.0, word.start), max(word.end, word.start)))
    for index, word in enumerate(kept):
        limit = word.start + 0.35 + 0.1 * len(word.text)
        end = min(word.end, limit)
        if duration > 0:
            end = min(end, duration)
        if index + 1 < len(kept) and kept[index + 1].start >= word.start:
            end = min(end, kept[index + 1].start)
        word.end = max(end, word.start)
    return kept, len(words) - len(kept)


# --- timeline remapping ---------------------------------------------------------------------------------


@dataclass
class MapSegment:
    """One kept piece of the source and where it lands in the output."""

    src_start: float
    src_end: float
    out_start: float
    out_end: float


def build_map(keep: Sequence[tuple[float, float]], speed: float = 1.0) -> list[MapSegment]:
    """Place the kept pieces one after another on the output clock.

    Args:
        keep: Kept pieces ``(start, end)`` in source seconds, time order.
        speed: Playback speed applied after the concat (1.3 means 30 % faster).

    Returns:
        The map from source pieces to output times.
    """
    out: list[MapSegment] = []
    cursor = 0.0
    for start, end in keep:
        length = (end - start) / speed
        out.append(MapSegment(start, end, cursor, cursor + length))
        cursor += length
    return out


def remap_words(words: Sequence[Word], segments: Sequence[MapSegment], speed: float = 1.0) -> list[Word]:
    """Move word times from the source clock to the output clock after cuts.

    A word whose middle lies in a removed stretch is dropped. A word that crosses
    the edge of a kept piece is clamped to the piece, so the new times never
    leave the piece that holds the word.

    Args:
        words: Words with source times.
        segments: Result of :func:`build_map`.
        speed: The same speed that was given to :func:`build_map`.

    Returns:
        Words with output times, in the given order.
    """
    out: list[Word] = []
    for word in words:
        middle = (word.start + word.end) / 2
        seg = next((s for s in segments if s.src_start <= middle <= s.src_end), None)
        if seg is None:
            continue
        start = seg.out_start + (max(word.start, seg.src_start) - seg.src_start) / speed
        end = seg.out_start + (min(word.end, seg.src_end) - seg.src_start) / speed
        out.append(Word(word.text, round(start, 3), round(max(end, start), 3)))
    return out


def quantize_keep(keep: Sequence[tuple[float, float]], fps: float = OUT_FPS) -> list[tuple[float, float]]:
    """Snap kept pieces outward to the frame grid so video and audio pieces have the same length.

    Args:
        keep: Kept pieces in source seconds.
        fps: Frame rate of the grid.

    Returns:
        Pieces on the grid, each at least as long as the original; slivers under half a frame are dropped,
        touching or overlapping pieces are joined.
    """
    # Outward: the start goes down and the end goes up to the next frame, so a word that the plan keeps
    # is never cut at its edge. A sliver that was shorter than half a frame to begin with is not worth a frame.
    eps = 1e-6
    snapped = [(math.floor(a * fps + eps) / fps, math.ceil(b * fps - eps) / fps) for a, b in keep if b - a >= 0.5 / fps]
    out: list[tuple[float, float]] = []
    for start, end in snapped:
        if out and start <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def speech_lost(words: Sequence[Word], keep: Sequence[tuple[float, float]], tolerance: float = 0.002) -> list[tuple[Word, float]]:
    """Words that the kept pieces cut into.

    Used after the pieces are snapped to the frame grid, to confirm the snapping did not eat speech.
    A word whose middle is outside every kept piece is a dropped word, not a damaged one, and is skipped.

    Args:
        words: Words with source times.
        keep: Kept pieces in source seconds.
        tolerance: Seconds of a word that may be lost without a report.

    Returns:
        ``(word, seconds lost)`` for each kept word that loses more than ``tolerance``.
    """
    lost: list[tuple[Word, float]] = []
    for word in words:
        middle = (word.start + word.end) / 2
        if not any(a <= middle <= b for a, b in keep):
            continue
        inside = sum(max(0.0, min(word.end, b) - max(word.start, a)) for a, b in keep)
        missing = (word.end - word.start) - inside
        if missing > tolerance:
            lost.append((word, missing))
    return lost


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def tokens(text: str) -> list[str]:
    """Lower-case word tokens of a text, punctuation removed, ``ё`` read as ``е``."""
    return _TOKEN.findall(text.lower().replace("ё", "е"))
