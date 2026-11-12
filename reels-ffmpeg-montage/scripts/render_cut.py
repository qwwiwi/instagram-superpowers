#!/usr/bin/env python3
"""Render the cut plan with ffmpeg and remap the word times.

Usage:
    render_cut.py video.mp4 cut-plan.json --words words.json [--out reel-cut.mp4]

What it does:
  * keeps the pieces of ``cut-plan.json`` (frame-accurate trim, re-encode) and joins them;
  * a few milliseconds of audio fade in and out at every seam, so a cut does not click;
  * speed-up from the plan is applied after the join (video ``setpts``, audio ``atempo``);
  * 1080x1920 (a source of another shape is fitted with black bars, nothing is cropped),
    30 fps, H.264 + AAC, ``faststart``;
  * loudness to about -14 LUFS in two passes (measure, then a linear correction);
  * writes ``words-cut.json`` (word times on the new clock) and ``cut-map.json``
    (where each seam is, for ``verify.py``).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from common import (
    JSON_VERSION,
    OUT_FPS,
    OUT_HEIGHT,
    OUT_WIDTH,
    MapSegment,
    MontageError,
    Word,
    WordsFile,
    build_map,
    fit_words_to_edges,
    load_words,
    probe,
    quantize_keep,
    read_json,
    remap_words,
    run_ffmpeg,
    sanitize_words,
    save_words,
    setup_logging,
    speech_lost,
    write_json,
)

LOG = logging.getLogger("reels.render")

DEFAULT_FADE_MS = 8.0
TARGET_LUFS = -14.0
TARGET_TP = -1.0
AUDIO_RATE = 48000


def load_plan(path: Path, source_duration: float) -> tuple[list[tuple[float, float]], float]:
    """Read the kept pieces and the speed from a ``cut-plan.json``.

    Args:
        path: Plan file.
        source_duration: Length of the video the plan is applied to.

    Returns:
        ``(kept pieces, speed)``.

    Raises:
        MontageError: The file is not a plan, or it was made for a video of another length.
    """
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("keep"), list):
        raise MontageError(f"{path} is not a cut plan (no `keep` list)")
    try:
        keep = [(float(k["start"]), float(k["end"])) for k in data["keep"]]
        speed = float(data.get("speed", 1.0))
        planned = float(data.get("source_duration", source_duration))
    except (KeyError, TypeError, ValueError) as exc:
        raise MontageError(f"{path}: a kept piece or the speed is malformed ({exc})") from None
    if abs(planned - source_duration) > 0.5:
        raise MontageError(
            f"The plan was made for a {planned:.1f} s video, this one is {source_duration:.1f} s. "
            "Use the video that was transcribed."
        )
    if not 0.5 <= speed <= 2.0:
        raise MontageError(f"speed {speed:g} is outside 0.5-2.0")
    if not keep:
        raise MontageError("The plan keeps nothing")
    return keep, speed


def load_speech_edges(path: Path) -> tuple[float, float] | None:
    """The real start and end of the voice that ``plan_cut.py`` measured, or None for an older plan."""
    data = read_json(path)
    edges = data.get("speech_edges") if isinstance(data, dict) else None
    if not isinstance(edges, dict):
        return None
    onset, offset = edges.get("onset"), edges.get("offset")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (onset, offset)):
        raise MontageError(f"{path}: `speech_edges` needs numeric onset and offset")
    return float(onset), float(offset)


def snapping_damage(words: list[Word], plan_keep: list[tuple[float, float]],
                    snapped: list[tuple[float, float]]) -> list[tuple[Word, float]]:
    """Words that lose speech because of the snapping to the frame grid, not because the plan cut them.

    Args:
        words: Clean words with source times.
        plan_keep: Kept pieces as planned.
        snapped: The same pieces after :func:`common.quantize_keep`.

    Returns:
        ``(word, extra seconds lost)``; empty when snapping is harmless.
    """
    planned = {id(w): sec for w, sec in speech_lost(words, plan_keep, 0.0)}
    return [(w, sec - planned.get(id(w), 0.0)) for w, sec in speech_lost(words, snapped, 0.0)
            if sec - planned.get(id(w), 0.0) > 0.002]


def _t(value: float) -> str:
    """Seconds as a filter argument."""
    return f"{value:.6f}"


def build_audio_graph(keep: list[tuple[float, float]], speed: float, fade: float) -> str:
    """Filter graph that cuts, fades and joins the audio. Output label ``[aout]``.

    Args:
        keep: Kept pieces in source seconds, on the frame grid.
        speed: Speed factor (``atempo``); 1.0 adds no filter.
        fade: Fade length at both ends of every piece, seconds.
    """
    count = len(keep)
    labels = "".join(f"[s{i}]" for i in range(count))
    parts = [f"[0:a]aresample={AUDIO_RATE},asplit={count}{labels}"]
    for i, (start, end) in enumerate(keep):
        f = min(fade, (end - start) / 4)
        parts.append(
            f"[s{i}]atrim=start={_t(start)}:end={_t(end)},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={_t(f)},afade=t=out:st={_t(end - start - f)}:d={_t(f)}[a{i}]"
        )
    joined = "".join(f"[a{i}]" for i in range(count))
    tail = f",atempo={speed:g}" if abs(speed - 1.0) > 1e-9 else ""
    parts.append(f"{joined}concat=n={count}:v=0:a=1{tail}[aout]")
    return ";".join(parts)


def build_video_graph(keep: list[tuple[float, float]], speed: float) -> str:
    """Filter graph that cuts and joins the video to 1080x1920 at 30 fps. Output label ``[vout]``."""
    count = len(keep)
    labels = "".join(f"[v{i}]" for i in range(count))
    parts = [
        f"[0:v]fps={OUT_FPS},scale={OUT_WIDTH}:{OUT_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={OUT_WIDTH}:{OUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p,"
        f"split={count}{labels}"
    ]
    for i, (start, end) in enumerate(keep):
        parts.append(f"[v{i}]trim=start={_t(start)}:end={_t(end)},setpts=PTS-STARTPTS[t{i}]")
    joined = "".join(f"[t{i}]" for i in range(count))
    tail = f",setpts=PTS/{speed:g},fps={OUT_FPS}" if abs(speed - 1.0) > 1e-9 else ""
    parts.append(f"{joined}concat=n={count}:v=1:a=0{tail}[vout]")
    return ";".join(parts)


def parse_loudnorm_json(stderr: str) -> dict[str, str]:
    """Read the JSON block that ``loudnorm`` prints with ``print_format=json``.

    The last brace-delimited block that has ``input_i`` is taken, because ffmpeg
    prints more lines after it.

    Raises:
        MontageError: No block was found.
    """
    for block in reversed(re.findall(r"\{[^{}]*\}", stderr)):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and "input_i" in data:
            return {str(k): str(v) for k, v in data.items()}
    raise MontageError("No loudnorm measurement in the ffmpeg output")


def loudnorm_first_pass() -> str:
    """Filter of the measuring pass."""
    return f"loudnorm=I={TARGET_LUFS:g}:TP={TARGET_TP:g}:LRA=11:print_format=json"


def loudnorm_second_pass(measured: dict[str, str]) -> str:
    """Filter of the correcting pass, built from the first-pass numbers.

    Raises:
        MontageError: The measurement is not a finite number (the audio is silent).
    """
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        try:
            value = float(measured[key])
        except (KeyError, ValueError):
            raise MontageError(f"loudnorm measurement lacks `{key}`") from None
        if value != value or value in (float("inf"), float("-inf")):
            raise MontageError("The audio is silent (loudness could not be measured); check the source file")
    return (
        f"loudnorm=I={TARGET_LUFS:g}:TP={TARGET_TP:g}:LRA=11"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true:print_format=json"
    )


def render(video: Path, keep: list[tuple[float, float]], speed: float, out: Path, fade_ms: float = DEFAULT_FADE_MS,
           crf: int = 18) -> str:
    """Render the cut video.

    Args:
        video: Source video.
        keep: Kept pieces on the frame grid.
        speed: Speed factor.
        out: Output mp4.
        fade_ms: Audio fade at every seam, milliseconds.
        crf: x264 quality (lower is better).

    Returns:
        The ``normalization_type`` ffmpeg reported for the loudness pass (``linear`` or ``dynamic``).

    Raises:
        MontageError: ffmpeg failed, or the audio is silent.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reels-render-") as tmp:
        wav = Path(tmp) / "cut.wav"
        run_ffmpeg(["-y", "-nostats", "-i", str(video), "-filter_complex", build_audio_graph(keep, speed, fade_ms / 1000),
                    "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), str(wav)], "audio cut")
        first = run_ffmpeg(["-nostats", "-i", str(wav), "-af", loudnorm_first_pass(), "-f", "null", "-"], "loudness measurement")
        measured = parse_loudnorm_json(first.stderr)
        LOG.info("loudness before correction: %s LUFS, true peak %s dBTP", measured["input_i"], measured["input_tp"])
        audio_filter = f"[1:a]{loudnorm_second_pass(measured)},aresample={AUDIO_RATE}[ar]"
        graph = f"{build_video_graph(keep, speed)};{audio_filter}"
        done = run_ffmpeg([
            "-y", "-nostats", "-i", str(video), "-i", str(wav), "-filter_complex", graph,
            "-map", "[vout]", "-map", "[ar]",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-r", str(OUT_FPS), "-c:a", "aac", "-b:a", "160k", "-ar", str(AUDIO_RATE),
            "-movflags", "+faststart", str(out),
        ], "render")
    try:
        mode = parse_loudnorm_json(done.stderr).get("normalization_type", "")
    except MontageError:
        mode = ""
    if mode and mode != "linear":
        LOG.warning("loudnorm fell back to %r mode: the loudness may breathe. Check the result with verify.py.", mode)
    return mode


def cut_map_payload(out: Path, segments: list[MapSegment], speed: float) -> dict[str, Any]:
    """The ``cut-map.json`` document: pieces, seams and the expected length."""
    return {
        "version": JSON_VERSION,
        "output": out.name,
        "speed": speed,
        "fps": OUT_FPS,
        "expected_duration": round(segments[-1].out_end, 3),
        "segments": [
            {"src_start": round(s.src_start, 3), "src_end": round(s.src_end, 3),
             "out_start": round(s.out_start, 3), "out_end": round(s.out_end, 3)}
            for s in segments
        ],
        "seams": [round(s.out_end, 3) for s in segments[:-1]],
    }


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``render_cut.py``."""
    parser = argparse.ArgumentParser(description="Render the cut plan with ffmpeg; write words-cut.json and cut-map.json")
    parser.add_argument("video", type=Path, help="source video")
    parser.add_argument("plan", type=Path, help="cut-plan.json from plan_cut.py")
    parser.add_argument("--words", type=Path, default=Path("words.json"), help="words.json (default ./words.json)")
    parser.add_argument("--out", type=Path, default=Path("reel-cut.mp4"), help="output mp4 (default ./reel-cut.mp4)")
    parser.add_argument("--words-out", type=Path, default=Path("words-cut.json"), help="remapped words (default ./words-cut.json)")
    parser.add_argument("--map-out", type=Path, default=Path("cut-map.json"), help="seam map (default ./cut-map.json)")
    parser.add_argument("--fade-ms", type=float, default=DEFAULT_FADE_MS, help=f"audio fade at each seam, ms (default {DEFAULT_FADE_MS:g})")
    parser.add_argument("--crf", type=int, default=18, help="x264 quality, lower is better (default 18)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    """Render. Returns the exit code."""
    video = args.video.expanduser().resolve()
    if not video.is_file():
        raise MontageError(f"File not found: {video}")
    info = probe(video)
    if not info.has_audio:
        raise MontageError(f"{video} has no audio stream")
    if info.height and abs(info.width / info.height - 9 / 16) > 0.02:
        LOG.warning("The source is %dx%d, not vertical 9:16. It is fitted with black bars; shoot vertically for a full-frame reel.",
                    info.width, info.height)
    keep, speed = load_plan(args.plan, info.duration)
    wf = load_words(args.words)
    words, _ = sanitize_words(wf.words, wf.duration)
    edges = load_speech_edges(args.plan)
    if edges and words:
        # the plan moved the edge words onto the measured voice; the captions must follow it
        words = fit_words_to_edges(words, *edges)
    plan_keep = keep
    keep = quantize_keep(keep, OUT_FPS)
    if not keep:
        raise MontageError("Nothing is left after snapping the pieces to the frame grid")
    damaged = snapping_damage(words, plan_keep, keep)
    if damaged:
        raise MontageError("Snapping the pieces to the frame grid would cut into speech: " + "; ".join(
            f"«{w.text}» at {w.start:.2f} s loses {sec * 1000:.0f} ms" for w, sec in damaged) + ". Nothing is rendered.")
    segments = build_map(keep, speed)
    LOG.info("rendering %d piece(s), %.2f s -> %.2f s (speed x%g)", len(keep), sum(b - a for a, b in keep),
             segments[-1].out_end, speed)
    mode = render(video, keep, speed, args.out, args.fade_ms, args.crf)
    cut_words =remap_words(words, segments, speed)
    save_words(args.words_out, WordsFile(words=cut_words, duration=segments[-1].out_end, language=wf.language, source=str(args.out)))
    write_json(args.map_out, cut_map_payload(args.out, segments, speed))
    print(f"{args.out}: {segments[-1].out_end:.2f} s, {len(segments) - 1} seam(s), {len(cut_words)}/{len(words)} words kept"
          + (f", loudnorm {mode}" if mode else ""))
    print(f"words -> {args.words_out}, seam map -> {args.map_out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        return run(args)
    except MontageError as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
