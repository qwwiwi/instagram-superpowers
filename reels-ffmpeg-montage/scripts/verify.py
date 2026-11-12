#!/usr/bin/env python3
"""Checks on the finished reel and seam frames for the agent to look at.

Usage:
    verify.py reel.mp4 [--cut-map cut-map.json] [--seams-dir seams] [--max-duration 90]

Checks (each prints PASS or FAIL; the exit code is 1 when any FAIL):
  * duration within ``--max-duration`` (and within 0.15 s of the plan when a cut map is given);
  * resolution 1080x1920, frame rate 30, H.264 video, AAC audio, ``faststart`` (moov before mdat);
  * integrated loudness about -14 LUFS (``--lufs-tolerance``) and true peak not above -1 dBTP + 0.5;
  * no silent gap longer than ``--max-silence`` seconds;
  * speech starts within ``--max-lead`` (0.10 s) of the first frame and ends within ``--max-trail``
    (0.10 s) of the last one, measured on the sound itself (the speech-edge quiet spans of ``audio.py``,
    which keep quiet phonemes); a video that runs on after its audio ends counts that rest as silence;
  * the first frame is not black.

With a cut map the script also exports a frame just before and just after every
seam into ``--seams-dir`` (and the two side by side), plus ``index.txt``. The
agent must LOOK at them: a jump of the pose or a cut through a word is not
visible to numbers.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from audio import Span, analyse_audio
from common import (
    MontageError,
    OUT_FPS,
    OUT_HEIGHT,
    OUT_WIDTH,
    MediaInfo,
    probe,
    read_json,
    require_tool,
    run_ffmpeg,
    setup_logging,
)

LOG = logging.getLogger("reels.verify")

DEFAULT_MAX_DURATION = 90.0
TARGET_LUFS = -14.0
DEFAULT_LUFS_TOLERANCE = 1.5
MAX_TRUE_PEAK = -0.5
DEFAULT_MAX_SILENCE = 0.8
# A reel opens on the first sound of the hook and closes right after the last word.
DEFAULT_MAX_LEAD = 0.10
DEFAULT_MAX_TRAIL = 0.10
SILENCE_DB = -45.0
DURATION_TOLERANCE = 0.15
SEAM_OFFSET = 0.04
# Mean luma (0-255) at or below which the first frame counts as black.
BLACK_LUMA = 18.0


@dataclass
class Check:
    """Result of one check."""

    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        """One printable line."""
        return f"{'PASS' if self.ok else 'FAIL'}  {self.name}: {self.detail}"


def moov_before_mdat(path: Path) -> bool | None:
    """Whether the ``moov`` atom comes before ``mdat`` (the file starts playing before it is fully loaded).

    Returns:
        True / False, or None when the file is not an MP4 box file.
    """
    seen: list[str] = []
    with path.open("rb") as handle:
        offset = 0
        size_total = path.stat().st_size
        while offset + 8 <= size_total and len(seen) < 64:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                break
            size, kind = struct.unpack(">I4s", header)
            name = kind.decode("latin-1")
            seen.append(name)
            if name in ("moov", "mdat"):
                return name == "moov"
            if size == 1:
                size = struct.unpack(">Q", handle.read(8))[0]
            elif size == 0:
                break
            if size < 8:
                break
            offset += size
    return None


def measure_loudness(path: Path) -> tuple[float, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) of the audio.

    Raises:
        MontageError: ffmpeg fails or no measurement is printed.
    """
    done = run_ffmpeg(["-nostats", "-i", str(path), "-vn", "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
                       "-f", "null", "-"], "loudness measurement")
    for block in reversed(re.findall(r"\{[^{}]*\}", done.stderr)):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and "input_i" in data:
            return float(data["input_i"]), float(data["input_tp"])
    raise MontageError("No loudness measurement in the ffmpeg output")


def parse_silences(stderr: str) -> list[tuple[float, float]]:
    """Silent stretches ``(start, length)`` from ``silencedetect`` output."""
    starts = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", stderr)]
    ends = re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", stderr)
    out: list[tuple[float, float]] = []
    for index, start in enumerate(starts):
        if index < len(ends):
            out.append((max(0.0, start), float(ends[index][1])))
        else:
            out.append((max(0.0, start), -1.0))  # runs to the end of the file; length filled by the caller
    return out


def find_silences(path: Path, min_len: float, duration: float) -> list[tuple[float, float]]:
    """Silent stretches of at least ``min_len`` seconds."""
    done = run_ffmpeg(["-nostats", "-i", str(path), "-vn", "-af", f"silencedetect=noise={SILENCE_DB:g}dB:d={min_len:g}",
                       "-f", "null", "-"], "silence detection")
    return [(s, length if length >= 0 else max(0.0, duration - s)) for s, length in parse_silences(done.stderr)]


def speech_margins(quiet: list[Span], duration: float, audio_end: float | None = None,
                   edge: float = 0.03) -> tuple[float, float]:
    """Non-speech at the start and at the end of a file, from its quiet spans.

    Args:
        quiet: Quiet spans in time order (see :func:`audio.quiet_spans`), on the audio clock.
        duration: File (video) length, seconds.
        audio_end: Where the decoded audio stops (default: ``duration``). A video that runs on after the
            audio shows that rest without sound: it counts as non-speech.
        edge: A span that begins (ends) this close to the audio edge counts as touching it (envelope windows).

    Returns:
        ``(seconds before the first sound, seconds after the last sound)``.
    """
    end = duration if audio_end is None else min(audio_end, duration)
    lead = next((s.end for s in quiet if s.start <= edge), 0.0)
    last_sound = next((s.start for s in reversed(quiet) if s.end >= end - edge), end)
    return max(0.0, lead), max(0.0, duration - last_sound)


def first_frame_luma(path: Path) -> float:
    """Mean luma (0-255) of the first video frame.

    Raises:
        MontageError: No frame could be decoded.
    """
    require_tool("ffmpeg")
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-frames:v", "1", "-vf", "scale=32:32,format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, check=False,
    )
    if done.returncode != 0 or not done.stdout:
        raise MontageError("Cannot decode the first frame")
    return sum(done.stdout) / len(done.stdout)


def run_checks(path: Path, info: MediaInfo, expected: float | None, max_duration: float, lufs_tolerance: float,
               max_silence: float, max_lead: float = DEFAULT_MAX_LEAD, max_trail: float = DEFAULT_MAX_TRAIL) -> list[Check]:
    """Run all numeric checks on a reel."""
    checks: list[Check] = []
    checks.append(Check("duration", info.duration <= max_duration,
                        f"{info.duration:.2f} s (limit {max_duration:g} s)"))
    if expected is not None:
        off = abs(info.duration - expected)
        checks.append(Check("duration vs plan", off <= DURATION_TOLERANCE,
                            f"{info.duration:.2f} s, plan {expected:.2f} s, difference {off:.2f} s (allowed {DURATION_TOLERANCE})"))
    checks.append(Check("resolution", (info.width, info.height) == (OUT_WIDTH, OUT_HEIGHT),
                        f"{info.width}x{info.height} (need {OUT_WIDTH}x{OUT_HEIGHT})"))
    checks.append(Check("frame rate", abs(info.fps - OUT_FPS) <= 0.1, f"{info.fps:.2f} fps (need {OUT_FPS})"))
    checks.append(Check("codecs", info.video_codec == "h264" and info.audio_codec == "aac",
                        f"video {info.video_codec or '-'}, audio {info.audio_codec or '-'} (need h264 + aac)"))
    atoms = moov_before_mdat(path)
    checks.append(Check("faststart", atoms is True, {True: "moov before mdat", False: "moov after mdat: re-encode with -movflags +faststart",
                                                      None: "no moov/mdat found"}[atoms]))
    if not info.has_audio:
        checks.append(Check("audio", False, "no audio stream"))
        return checks
    lufs, peak = measure_loudness(path)
    checks.append(Check("loudness", abs(lufs - TARGET_LUFS) <= lufs_tolerance,
                        f"{lufs:.1f} LUFS (target {TARGET_LUFS:g} +-{lufs_tolerance:g}), true peak {peak:.1f} dBTP (max {MAX_TRUE_PEAK:g})"))
    checks.append(Check("true peak", peak <= MAX_TRUE_PEAK, f"{peak:.1f} dBTP"))
    gaps = find_silences(path, max_silence, info.duration)
    where = ", ".join(f"{s:.2f}-{s + n:.2f} s" for s, n in gaps[:5])
    checks.append(Check("silent gaps", not gaps, f"none over {max_silence:g} s" if not gaps else f"{len(gaps)} over {max_silence:g} s: {where}"))
    sound = analyse_audio(path)
    lead, trail = speech_margins(sound.edge_quiet, info.duration, sound.audio_duration)
    checks.append(Check("speech start", lead <= max_lead, f"first sound of speech at {lead:.2f} s (max {max_lead:g} s)"))
    checks.append(Check("speech end", trail <= max_trail, f"{trail:.2f} s after the last sound of speech (max {max_trail:g} s)"))
    luma = first_frame_luma(path)
    checks.append(Check("first frame", luma > BLACK_LUMA, f"mean luma {luma:.0f}/255 ({'not black' if luma > BLACK_LUMA else 'BLACK'})"))
    return checks


def export_frame(video: Path, at: float, target: Path) -> None:
    """Write one PNG frame at ``at`` seconds."""
    run_ffmpeg(["-y", "-nostats", "-ss", f"{max(0.0, at):.3f}", "-i", str(video), "-frames:v", "1", str(target)], "frame export")


def export_seams(video: Path, seams: list[float], duration: float, folder: Path) -> list[str]:
    """Export a frame before and after every seam and the pair side by side.

    Args:
        video: The reel.
        seams: Seam times on the reel clock.
        duration: Reel length.
        folder: Output folder (created).

    Returns:
        Index lines (also written to ``index.txt``).
    """
    folder.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for number, seam in enumerate(seams, 1):
        before = folder / f"seam-{number:03d}-before.png"
        after = folder / f"seam-{number:03d}-after.png"
        pair = folder / f"seam-{number:03d}.png"
        export_frame(video, seam - SEAM_OFFSET, before)
        export_frame(video, min(seam + SEAM_OFFSET, max(0.0, duration - 0.05)), after)
        run_ffmpeg(["-y", "-nostats", "-i", str(before), "-i", str(after), "-filter_complex", "hstack=inputs=2", "-frames:v", "1",
                    str(pair)], "seam pair")
        lines.append(f"seam {number:03d} at {seam:.2f} s: {pair.name} (left {seam - SEAM_OFFSET:.2f} s, right {seam + SEAM_OFFSET:.2f} s)")
    (folder / "index.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return lines


def load_cut_map(path: Path) -> dict[str, Any]:
    """Read a ``cut-map.json``.

    Raises:
        MontageError: The file is not a cut map.
    """
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("seams"), list):
        raise MontageError(f"{path} is not a cut map (no `seams` list)")
    return data


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``verify.py``."""
    parser = argparse.ArgumentParser(description="Check the finished reel and export seam frames")
    parser.add_argument("video", type=Path, help="the reel to check")
    parser.add_argument("--cut-map", type=Path, help="cut-map.json from render_cut.py (default: next to the video if present)")
    parser.add_argument("--seams-dir", type=Path, default=Path("seams"), help="folder for seam frames (default ./seams)")
    parser.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION, help=f"longest allowed reel, s (default {DEFAULT_MAX_DURATION:g}; set the current Instagram limit)")
    parser.add_argument("--lufs-tolerance", type=float, default=DEFAULT_LUFS_TOLERANCE, help=f"allowed distance from -14 LUFS (default {DEFAULT_LUFS_TOLERANCE})")
    parser.add_argument("--max-silence", type=float, default=DEFAULT_MAX_SILENCE, help=f"longest allowed silent gap, s (default {DEFAULT_MAX_SILENCE})")
    parser.add_argument("--max-lead", type=float, default=DEFAULT_MAX_LEAD, help=f"longest non-speech before the first word, s (default {DEFAULT_MAX_LEAD})")
    parser.add_argument("--max-trail", type=float, default=DEFAULT_MAX_TRAIL, help=f"longest non-speech after the last word, s (default {DEFAULT_MAX_TRAIL})")
    parser.add_argument("--no-seams", action="store_true", help="skip the seam frames")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run. Exit code 1 when a check fails."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        video = args.video.expanduser().resolve()
        if not video.is_file():
            raise MontageError(f"File not found: {video}")
        info = probe(video)
        map_path = args.cut_map
        if map_path is None and (video.parent / "cut-map.json").is_file():
            map_path = video.parent / "cut-map.json"
        cut_map = load_cut_map(map_path) if map_path else None
        expected = float(cut_map["expected_duration"]) if cut_map and "expected_duration" in cut_map else None
        checks = run_checks(video, info, expected, args.max_duration, args.lufs_tolerance, args.max_silence,
                            args.max_lead, args.max_trail)
        for check in checks:
            print(check.line())
        if cut_map and not args.no_seams:
            lines = export_seams(video, [float(s) for s in cut_map["seams"]], info.duration, args.seams_dir)
            print(f"\n{len(lines)} seam(s) exported to {args.seams_dir}/ (index.txt). LOOK at them before you say the cut is clean.")
        elif not cut_map:
            print("\nNo cut map: seam frames skipped (pass --cut-map).")
    except MontageError as exc:
        LOG.error("%s", exc)
        return 2
    failed = [c for c in checks if not c.ok]
    print(f"\n{'FAIL' if failed else 'PASS'}: {len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
