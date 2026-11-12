#!/usr/bin/env python3
"""A frame with labelled horizontal guide lines, to read where the speaker's head is.

Usage:
    head_guide.py reel-cut.mp4 --time 2.0 [--time 9.5] [--out head-guide]
    head_guide.py --top 610 --eyes 830 --chin 1010          # numbers only, no video needed
    head_guide.py --top 625 --chin 1560 --y0 880            # check a crop: exit 1 when it cuts the head

The frame is drawn at full size (1080 x 1920) with a thin line every 20 px, a thicker labelled line
every 100 px and the zone lines of the regulation (y 115, 960, 1520). Read on it the top of the
hair, the eye line and the chin, then pass them as ``--top``, ``--eyes``, ``--chin``: the script
prints the split offset ``y0`` (rule 1.3 of ``montage-reglament.md``) with where the head lands in
the split, and where the caption of a full-screen speaker shot goes (rule 7.5). The whole head stays
in the window: a close-up is scaled down, never cut; a crop that cuts the head is a FAIL (exit 1).

Labels: Pillow when it is installed, otherwise ffmpeg ``drawtext`` when the build has it, otherwise
lines only (the printed legend says which line is which).
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from common import MontageError, probe, run_ffmpeg, setup_logging
from frames_sheet import have_drawtext, have_pillow, video_duration

LOG = logging.getLogger("reels.head_guide")

FRAME_W = 1080
FRAME_H = 1920
SEAM = 960
ZONE_ROWS = (115, 960, 1520)
# Split framing (rule 1.3): hair top 60 px under the seam, or 20 px when the chin would fall below 1480.
HAIR_GAP = 60
HAIR_GAP_TIGHT = 20
CHIN_TARGET = 1480
CHIN_LIMIT = 1520
CHIN_HARD = 1600
# The whole head stays inside the speaker window (rule 1.3): hair top at least 20 px under the seam,
# chin at least 40 px above the bottom of the frame. A close-up that does not fit at full size is scaled
# down (not cut) to at most MIN_SCALE; the side gaps are filled with a blurred copy of the same frame.
HEAD_MARGIN_TOP = 20
HEAD_MARGIN_BOTTOM = 40
WINDOW_BOTTOM = FRAME_H
MIN_SCALE = 0.85
# Speaker captions (rule 7.5).
CHEST_TOP = 1180
CHEST_BOTTOM = 1352
CHIN_PAD = 14
HAIR_PAD = 24
LINE_H = 80
PLATE_GAP = 12  # plates of two caption lines on the speaker do not touch (rule 7.4)
TOP_SAFE = 115


@dataclass
class SplitFit:
    """Where the head lands in the speaker half of a split.

    ``y0`` is a row of the source after scaling by ``scale`` (1.0 = the source at width 1080).
    """

    y0: int
    top: int
    chin: int
    eyes: int | None
    verdict: str
    notes: list[str]
    scale: float = 1.0


@dataclass
class CaptionSlot:
    """Caption block of a full-screen speaker shot."""

    mode: str  # chest | above_head | none
    box_top: int
    box_bottom: int
    lines: int


def guide_rows(step: int = 20, major: int = 100, height: int = FRAME_H) -> list[tuple[int, bool]]:
    """Rows of the guide lines: ``(y, is_major)``."""
    if step <= 0 or major <= 0 or major % step:
        raise MontageError("--step must be positive and divide 100")
    return [(y, y % major == 0) for y in range(step, height, step)]


def crop_fit(top: int, chin: int, y0: int, scale: float = 1.0, eyes: int | None = None) -> SplitFit:
    """Where the head of the source lands for a given split crop, and whether the crop cuts it.

    Args:
        top: Top of the hair in the source at width 1080, px (the highest of all head marks).
        chin: Chin, px (the lowest of all head marks: tilts and an open mouth included).
        y0: First source row (after scaling) shown at the seam y 960.
        scale: Scale of the speaker in the window, ``MIN_SCALE``-1.0.
        eyes: Eye line, px (optional).

    Returns:
        Landing rows and the verdict: ``fail`` when the hair top is less than ``HEAD_MARGIN_TOP`` under the
        seam or the chin less than ``HEAD_MARGIN_BOTTOM`` above the bottom (the window cuts the head),
        ``warn`` for a chin under y 1600 or eyes outside 1100-1400, else ``ok``.
    """
    land_top = round(top * scale) - y0 + SEAM
    land_chin = round(chin * scale) - y0 + SEAM
    land_eyes = round(eyes * scale) - y0 + SEAM if eyes is not None else None
    notes: list[str] = []
    verdict = "ok"
    if land_top < SEAM + HEAD_MARGIN_TOP:
        verdict = "fail"
        notes.append(f"FAIL: hair top at y {land_top}, the window cuts the head (need y >= {SEAM + HEAD_MARGIN_TOP})")
    if land_chin > WINDOW_BOTTOM - HEAD_MARGIN_BOTTOM:
        verdict = "fail"
        notes.append(f"FAIL: chin at y {land_chin}, the window cuts the head (need y <= {WINDOW_BOTTOM - HEAD_MARGIN_BOTTOM})")
    if verdict != "fail":
        if land_chin > CHIN_HARD:
            verdict = "warn"
            notes.append(f"chin at y {land_chin} > {CHIN_HARD}: the head is whole, but the take is close; "
                         "tell the student to sit further from the camera")
        elif land_chin > CHIN_LIMIT:
            verdict = "warn"
            notes.append(f"chin at y {land_chin} > {CHIN_LIMIT}: allowed, tell the student to sit further from the camera")
        if land_eyes is not None and not 1100 <= land_eyes <= 1400:
            verdict = "warn"
            notes.append(f"eyes at y {land_eyes}, outside 1100-1400")
    return SplitFit(y0, land_top, land_chin, land_eyes, verdict, notes, scale)


def split_offset(top: int, chin: int, eyes: int | None = None, src_height: int = FRAME_H) -> SplitFit:
    """Split offset ``y0`` (and scale) by rule 1.3 and where the head lands.

    Args:
        top: Top of the hair in the full frame (1080 x 1920), px.
        chin: Chin, px.
        eyes: Eye line, px (optional).
        src_height: Height of the full frame.

    Returns:
        The offset, the scale and the landing rows with a verdict: ok, warn (close-up) or fail (the head
        does not fit the window even at ``MIN_SCALE``).
    """
    if not 0 <= top < chin <= src_height:
        raise MontageError(f"need 0 <= top < chin <= {src_height}, got top={top} chin={chin}")
    y0 = top - HAIR_GAP
    if chin - y0 + SEAM > CHIN_TARGET:
        y0 = top - HAIR_GAP_TIGHT
    scale = 1.0
    if chin - y0 + SEAM > CHIN_HARD:
        # Close-up: the hair goes right under the seam and, if the chin still leaves the window, the speaker is
        # scaled down. The head is never cut.
        room = WINDOW_BOTTOM - HEAD_MARGIN_BOTTOM - SEAM - HEAD_MARGIN_TOP
        if chin - top > room:
            scale = max(MIN_SCALE, math.floor(room / (chin - top) * 1000) / 1000)
        y0 = round(top * scale) - HEAD_MARGIN_TOP
    y0 = min(max(y0, 0), round(src_height * scale) - SEAM)
    fit = crop_fit(top, chin, y0, scale, eyes)
    if scale < 1.0 and fit.verdict != "fail":
        fit.notes.insert(0, f"close-up: speaker scaled to {scale:g} (width {round(FRAME_W * scale)} px, centred; "
                            "fill the side gaps with a blurred copy of the same frame)")
    if fit.verdict == "fail" and chin - top > room_for_head():
        fit.notes.append(f"the head is {chin - top} px tall: it does not fit the window even at scale {MIN_SCALE:g}; re-shoot further away")
    return fit


def room_for_head() -> float:
    """Tallest head (hair to chin, source px) that fits the speaker window at ``MIN_SCALE``."""
    return (WINDOW_BOTTOM - HEAD_MARGIN_BOTTOM - SEAM - HEAD_MARGIN_TOP) / MIN_SCALE


def speaker_caption(top: int, chin: int) -> CaptionSlot:
    """Caption block of a full-screen speaker shot by rule 7.5."""
    if not 0 <= top < chin <= FRAME_H:
        raise MontageError(f"need 0 <= top < chin <= {FRAME_H}, got top={top} chin={chin}")
    if chin + CHIN_PAD <= CHEST_TOP:
        return CaptionSlot("chest", CHEST_TOP, CHEST_TOP + block_height(2), 2)
    bottom = top - HAIR_PAD
    room = bottom - TOP_SAFE
    lines = 2 if room >= block_height(2) else (1 if room >= block_height(1) else 0)
    if lines == 0:
        return CaptionSlot("none", 0, 0, 0)
    return CaptionSlot("above_head", bottom - block_height(lines), bottom, lines)


def block_height(lines: int) -> int:
    """Height of a caption block on the speaker: 80 px plates, 12 px between the lines."""
    return lines * LINE_H + (lines - 1) * PLATE_GAP


def draw_pillow(frame: Path, out: Path, rows: list[tuple[int, bool]]) -> None:
    """Draw the guide lines and labels on a frame with Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    with Image.open(frame) as img:
        image = img.convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    for y, major in rows:
        if y in ZONE_ROWS:
            continue
        draw.line([(0, y), (FRAME_W, y)], fill=(0, 255, 255, 200) if major else (0, 255, 255, 90), width=2 if major else 1)
        if major:
            for x in (4, FRAME_W - 70):
                draw.rectangle([x, y - 30, x + 64, y - 2], fill=(0, 0, 0, 170))
                draw.text((x + 4, y - 30), str(y), fill=(255, 255, 255, 255), font=font)
    for y in ZONE_ROWS:
        draw.line([(0, y), (FRAME_W, y)], fill=(255, 0, 200, 230), width=3)
        draw.rectangle([FRAME_W // 2 - 40, y + 2, FRAME_W // 2 + 40, y + 32], fill=(0, 0, 0, 170))
        draw.text((FRAME_W // 2 - 34, y + 2), str(y), fill=(255, 0, 200, 255), font=font)
    image.save(out)


def ffmpeg_guide_filter(rows: list[tuple[int, bool]], drawtext: bool) -> str:
    """Filter chain that draws the guides with drawbox (and labels with drawtext when available)."""
    steps = [f"scale={FRAME_W}:-2"]
    for y, major in rows:
        if y in ZONE_ROWS:
            continue
        steps.append(f"drawbox=x=0:y={y}:w={FRAME_W}:h={2 if major else 1}:color=0x00FFFF@{0.8 if major else 0.35}:t=fill")
        if major and drawtext:
            steps.append(f"drawtext=text='{y}':x=8:y={y - 30}:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.6")
    for y in ZONE_ROWS:
        steps.append(f"drawbox=x=0:y={y - 1}:w={FRAME_W}:h=3:color=0xFF00C8:t=fill")
        if drawtext:
            steps.append(f"drawtext=text='{y}':x={FRAME_W // 2 - 34}:y={y + 4}:fontsize=26:fontcolor=0xFF00C8:box=1:boxcolor=black@0.6")
    return ",".join(steps)


def legend(step: int, labelled: bool) -> str:
    """Printed legend of the lines."""
    text = (f"thin cyan line every {step} px, thicker cyan line every 100 px, "
            f"magenta lines at y {', '.join(str(r) for r in ZONE_ROWS)} (header, seam, interface)")
    if not labelled:
        text += "; no labels (no Pillow, no drawtext): count the thick lines from the top, the first is y 100"
    return text


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``head_guide.py``."""
    parser = argparse.ArgumentParser(description="Frame with labelled guide lines to read the head position")
    parser.add_argument("video", type=Path, nargs="?", help="reel-cut.mp4 (1080x1920); not needed with numbers only")
    parser.add_argument("--time", type=float, action="append", default=[], help="second of the frame (repeatable, default 1.0)")
    parser.add_argument("--out", type=Path, default=Path("head-guide"), help="file name prefix (default head-guide)")
    parser.add_argument("--step", type=int, default=20, help="spacing of the thin lines, px (default 20)")
    parser.add_argument("--top", type=int, help="top of the hair, px, read on the guide frame")
    parser.add_argument("--eyes", type=int, help="eye line, px")
    parser.add_argument("--chin", type=int, help="chin, px")
    parser.add_argument("--y0", type=int, help="check THIS split crop (source row at the seam) instead of computing one; exit 1 when it cuts the head")
    parser.add_argument("--scale", type=float, default=1.0, help=f"speaker scale of the crop checked with --y0 ({MIN_SCALE:g}-1.0, default 1.0)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def report_numbers(top: int, chin: int, eyes: int | None, y0: int | None = None, scale: float = 1.0) -> bool:
    """Print the split offset (or the check of a given crop) and the speaker caption slot.

    Returns:
        False when the crop cuts the head (verdict fail).
    """
    fit = split_offset(top, chin, eyes) if y0 is None else crop_fit(top, chin, y0, scale, eyes)
    eyes_text = f", eyes y {fit.eyes}" if fit.eyes is not None else ""
    scale_text = f", speaker scale {fit.scale:g}" if fit.scale != 1.0 else ""
    print(f"split: y0 = {fit.y0}{scale_text} (source rows {fit.y0}-{fit.y0 + SEAM} go to y 960-1920); "
          f"hair top y {fit.top}{eyes_text}, chin y {fit.chin} -> {fit.verdict.upper()}")
    for note in fit.notes:
        print(f"  - {note}")
    slot = speaker_caption(top, chin)
    if slot.mode == "none":
        print("speaker shot: no room for a caption above the head and the chin is too low for the chest: "
              "shorten this speaker shot or move the cut (rule 7.5)")
    else:
        print(f"speaker shot: caption mode {slot.mode}, block y {slot.box_top}-{slot.box_bottom}, up to {slot.lines} line(s)")
    return fit.verdict != "fail"


def check_times_inside(video: Path, times: list[float]) -> None:
    """Fail before any frame is drawn when a time lies at or past the end of the video stream.

    ffmpeg exits with success and writes no image for a seek past the end, so the check is done on the
    length of the video stream (the container may be longer when the audio runs past the picture).

    Raises:
        MontageError: A requested second is not inside the video.
    """
    length = video_duration(video, probe(video).duration)
    late = [t for t in times if t >= length]
    if late:
        shown = ", ".join(f"{t:.2f}" for t in late)
        raise MontageError(f"--time {shown} s is beyond the end of the video (the picture lasts {length:.2f} s): "
                           "give a second before the end")


def require_frame(path: Path, seconds: float) -> None:
    """Fail when ffmpeg reported success but wrote no usable image."""
    if not path.is_file() or path.stat().st_size == 0:
        raise MontageError(f"no frame was extracted at {seconds:.2f} s (is it beyond the end of the video?)")


def run(args: argparse.Namespace) -> int:
    """Draw guide frames and/or print the numbers. Returns the exit code."""
    if (args.top is None) != (args.chin is None):
        raise MontageError("--top and --chin go together")
    if args.video is None and args.top is None:
        raise MontageError("give a video to draw guide frames, or --top and --chin to compute the framing")
    if args.video is not None:
        video: Path = args.video.expanduser()
        if not video.is_file():
            raise MontageError(f"File not found: {video}")
        times = args.time or [1.0]
        for t in times:
            if not math.isfinite(t) or t < 0:
                raise MontageError(f"--time must be a non-negative number, got {t}")
        check_times_inside(video, times)
        rows = guide_rows(args.step)
        method = "pillow" if have_pillow() else ("drawtext" if have_drawtext() else "none")
        for t in times:
            out = Path(f"{args.out}-{t:07.2f}.png")
            if method == "pillow":
                raw = out.with_suffix(".raw.png")
                run_ffmpeg(["-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", f"scale={FRAME_W}:-2", str(raw)], "frame export")
                try:
                    require_frame(raw, t)
                    draw_pillow(raw, out, rows)
                finally:
                    raw.unlink(missing_ok=True)
            else:
                run_ffmpeg(["-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", ffmpeg_guide_filter(rows, method == "drawtext"), str(out)], "guide frame")
                require_frame(out, t)
            print(f"{t:.2f} s -> {out}")
        print(legend(args.step, method != "none"))
    if args.top is not None:
        if not MIN_SCALE <= args.scale <= 1.0:
            raise MontageError(f"--scale must be between {MIN_SCALE:g} and 1.0, got {args.scale:g}")
        if not report_numbers(args.top, args.chin, args.eyes, args.y0, args.scale):
            return 1
    elif args.y0 is not None:
        raise MontageError("--y0 checks a crop against the head marks: pass --top and --chin too")
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
