#!/usr/bin/env python3
"""Check a finished reel file before it goes into Edits: size, frame rate, audio, length.

Runs ffprobe and ffmpeg ``volumedetect`` on the file and prints a verdict. The
length it prints is the frozen length: after the import the Edits timeline must
have the same length (``import_reel.py`` compares them).

Example::

    check_reel.py ~/Movies/my-reel/reel.mp4 --max-duration 90

Exit codes: 0 PASS, 1 FAIL (problems listed), 2 the file or ffmpeg is missing.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("edits.check_reel")

EXPECT_SIZE = (1080, 1920)
DEFAULT_FPS = 30.0
DEFAULT_SILENCE_DB = -60.0
FPS_TOLERANCE = 0.1


class CheckError(RuntimeError):
    """The file cannot be probed."""


def parse_rate(raw: str) -> float:
    """Turn an ffprobe rate such as ``30/1`` or ``30000/1001`` into a float (0 if unknown)."""
    num, _, den = str(raw).partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def parse_volumedetect(stderr: str) -> Dict[str, float]:
    """Read mean and max volume (dB) from ffmpeg ``volumedetect`` output.

    Args:
        stderr: ffmpeg stderr text.

    Returns:
        ``{"mean_volume": ..., "max_volume": ...}``; keys missing if not printed.
    """
    found: Dict[str, float] = {}
    for key in ("mean_volume", "max_volume"):
        match = re.search(rf"{key}:\s*(-?[\d.]+|-inf)\s*dB", stderr)
        if match:
            found[key] = float("-inf") if match.group(1) == "-inf" else float(match.group(1))
    return found


def facts_from_ffprobe(info: Dict[str, Any]) -> Dict[str, Any]:
    """Pick the facts the verdict needs from ffprobe JSON (``-show_streams -show_format``).

    Args:
        info: Decoded ffprobe output.

    Returns:
        Keys: duration, width, height, video_codec, fps, has_audio.
    """
    streams = info.get("streams", []) or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "duration": float((info.get("format") or {}).get("duration", 0) or 0),
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "fps": parse_rate(video.get("avg_frame_rate", "")),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def probe(path: Path) -> Dict[str, Any]:
    """Run ffprobe and ffmpeg volumedetect on a file.

    Returns:
        :func:`facts_from_ffprobe` plus mean_volume, max_volume and, on a failed
        decode, audio_error.

    Raises:
        CheckError: ffprobe or ffmpeg is missing or fails.
    """
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        raise CheckError("ffprobe/ffmpeg not found; install ffmpeg to check the file")
    raw = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )
    if raw.returncode != 0:
        raise CheckError(f"ffprobe failed: {raw.stderr.strip()[:300]}")
    facts = facts_from_ffprobe(json.loads(raw.stdout))
    if facts["has_audio"]:
        # -xerror: a corrupted stream prints decode errors but exits 0 without it.
        vol = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-xerror", "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
            capture_output=True, text=True, check=False,
        )
        facts.update(parse_volumedetect(vol.stderr))
        if vol.returncode != 0:
            # Partial statistics of a failed decode prove nothing; flag it for verdict().
            facts["audio_error"] = f"ffmpeg exit code {vol.returncode}: {vol.stderr.strip()[-200:]}"
    return facts


def verdict(
    facts: Dict[str, Any],
    silence_db: float = DEFAULT_SILENCE_DB,
    expect_fps: Optional[float] = DEFAULT_FPS,
    max_duration: Optional[float] = None,
) -> List[str]:
    """Compare probe facts with the reel requirements.

    Args:
        facts: Result of :func:`probe`.
        silence_db: Mean volume at or below this counts as silent.
        expect_fps: Expected frame rate, or None to skip the check.
        max_duration: Longest allowed length in seconds, or None to skip.

    Returns:
        Problem lines; empty means the file passes.
    """
    problems: List[str] = []
    if (facts.get("width"), facts.get("height")) != EXPECT_SIZE:
        problems.append(f"size is {facts.get('width')}x{facts.get('height')}, expected 1080x1920")
    if expect_fps is not None and abs(float(facts.get("fps") or 0) - expect_fps) > FPS_TOLERANCE:
        problems.append(f"frame rate is {facts.get('fps')}, expected {expect_fps:g}")
    if not facts.get("has_audio"):
        problems.append("no audio stream")
    elif facts.get("audio_error"):
        problems.append(f"audio decode failed, the volume check cannot be trusted ({facts['audio_error']})")
    elif facts.get("mean_volume", float("-inf")) <= silence_db:
        problems.append(f"audio looks silent (mean {facts.get('mean_volume')} dB <= {silence_db} dB)")
    duration = float(facts.get("duration") or 0.0)
    if duration <= 0:
        problems.append("duration is unknown or zero")
    elif max_duration is not None and duration > max_duration:
        problems.append(f"duration {duration:.2f} s is longer than {max_duration:g} s")
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments, probe, print the verdict. Returns the exit code."""
    parser = argparse.ArgumentParser(description="Check a finished reel: 1080x1920, frame rate, audio, length")
    parser.add_argument("file", type=Path, help="the finished .mp4")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help="expected frame rate (default 30)")
    parser.add_argument("--max-duration", type=float, help="longest allowed length in seconds (check Instagram's current limit)")
    parser.add_argument("--silence-db", type=float, default=DEFAULT_SILENCE_DB, help="mean volume at or below this is silent")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    path = args.file.expanduser()
    if not path.is_file():
        LOG.error("No such file: %s", path)
        return 2
    try:
        facts = probe(path)
    except (CheckError, ValueError) as exc:
        LOG.error("%s", exc)
        return 2
    print(f"file: {path}")
    print(
        f"codec {facts['video_codec']}, {facts['width']}x{facts['height']}, {facts['fps']:g} fps, "
        f"{facts['duration']:.2f} s, audio {'yes' if facts['has_audio'] else 'NO'}, "
        f"mean {facts.get('mean_volume', 'n/a')} dB, max {facts.get('max_volume', 'n/a')} dB"
    )
    problems = verdict(facts, args.silence_db, args.fps, args.max_duration)
    for line in problems:
        print(f"PROBLEM: {line}")
    if not problems:
        print(f"freeze this length: import_reel.py --expect-duration {facts['duration']:.2f}")
    print("VERDICT: " + ("FAIL" if problems else "PASS (captions and safe zones: look at the frames yourself)"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
