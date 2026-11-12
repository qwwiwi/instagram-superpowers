#!/usr/bin/env python3
"""A frame with labelled horizontal guide lines, to read where the speaker's head is.

Usage:
    head_guide.py reel-cut.mp4 --time 2.0 [--time 9.5] [--out head-guide]
    head_guide.py --top 610 --eyes 830 --chin 1010          # numbers only, no video needed

The frame is drawn at full size (1080 x 1920) with a thin line every 20 px, a thicker labelled line
every 100 px and the zone lines of the regulation (y 115, 960, 1520). Read on it the top of the
hair, the eye line and the chin, then pass them as ``--top``, ``--eyes``, ``--chin``: the script
prints the split offset ``y0`` (rule 1.3 of ``montage-reglament.md``) with where the head lands in
the split, and where the caption of a full-screen speaker shot goes (rule 7.5).

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

from common import MontageError, run_ffmpeg, setup_logging
from frames_sheet import have_drawtext, have_pillow

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
# Speaker captions (rule 7.5).
CHEST_TOP = 1180
CHEST_BOTTOM = 1340
CHIN_PAD = 14
HAIR_PAD = 24
LINE_H = 80
TOP_SAFE = 115


@dataclass
class SplitFit:
    """Where the head lands in the speaker half of a split."""

    y0: int
    top: int
    chin: int
    eyes: int | None
    verdict: str
    notes: list[str]


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


def split_offset(top: int, chin: int, eyes: int | None = None, src_height: int = FRAME_H) -> SplitFit:
    """Split offset ``y0`` by rule 1.3 and where the head lands.

    Args:
        top: Top of the hair in the full frame (1080 x 1920), px.
        chin: Chin, px.
        eyes: Eye line, px (optional).
        src_height: Height of the full frame.

    Returns:
        The offset and the landing rows with a verdict: ok, warn (close-up) or fail.
    """
    if not 0 <= top < chin <= src_height:
        raise MontageError(f"need 0 <= top < chin <= {src_height}, got top={top} chin={chin}")
    y0 = top - HAIR_GAP
    if chin - y0 + SEAM > CHIN_TARGET:
        y0 = top - HAIR_GAP_TIGHT
    y0 = min(max(y0, 0), src_height - SEAM)
    land_top, land_chin = top - y0 + SEAM, chin - y0 + SEAM
    land_eyes = eyes - y0 + SEAM if eyes is not None else None
    notes: list[str] = []
    verdict = "ok"
    if land_chin > CHIN_HARD:
        verdict = "fail"
        notes.append(f"chin at y {land_chin} > {CHIN_HARD}: the take is too close, the face does not fit the half")
    elif land_chin > CHIN_LIMIT:
        verdict = "warn"
        notes.append(f"chin at y {land_chin} > {CHIN_LIMIT}: allowed, tell the student to sit further from the camera")
    if not 975 <= land_top <= 1060:
        verdict = "fail" if verdict == "fail" else "warn"
        notes.append(f"hair top at y {land_top}, outside 975-1060 (offset clamped to the frame)")
    if land_eyes is not None and not 1100 <= land_eyes <= 1400:
        verdict = "fail" if verdict == "fail" else "warn"
        notes.append(f"eyes at y {land_eyes}, outside 1100-1400")
    return SplitFit(y0, land_top, land_chin, land_eyes, verdict, notes)


def speaker_caption(top: int, chin: int) -> CaptionSlot:
    """Caption block of a full-screen speaker shot by rule 7.5."""
    if not 0 <= top < chin <= FRAME_H:
        raise MontageError(f"need 0 <= top < chin <= {FRAME_H}, got top={top} chin={chin}")
    if chin + CHIN_PAD <= CHEST_TOP:
        return CaptionSlot("chest", CHEST_TOP, CHEST_BOTTOM, 2)
    bottom = top - HAIR_PAD
    room = bottom - TOP_SAFE
    lines = 2 if room >= 2 * LINE_H else (1 if room >= LINE_H else 0)
    if lines == 0:
        return CaptionSlot("none", 0, 0, 0)
    return CaptionSlot("above_head", bottom - lines * LINE_H, bottom, lines)


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
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def report_numbers(top: int, chin: int, eyes: int | None) -> None:
    """Print the split offset and the speaker caption slot."""
    fit = split_offset(top, chin, eyes)
    eyes_text = f", eyes y {fit.eyes}" if fit.eyes is not None else ""
    print(f"split: y0 = {fit.y0} (source rows {fit.y0}-{fit.y0 + SEAM} go to y 960-1920); "
          f"hair top y {fit.top}{eyes_text}, chin y {fit.chin} -> {fit.verdict}")
    for note in fit.notes:
        print(f"  - {note}")
    slot = speaker_caption(top, chin)
    if slot.mode == "none":
        print("speaker shot: no room for a caption above the head and the chin is too low for the chest: "
              "shorten this speaker shot or move the cut (rule 7.5)")
    else:
        print(f"speaker shot: caption mode {slot.mode}, block y {slot.box_top}-{slot.box_bottom}, up to {slot.lines} line(s)")


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
        rows = guide_rows(args.step)
        method = "pillow" if have_pillow() else ("drawtext" if have_drawtext() else "none")
        for t in times:
            out = Path(f"{args.out}-{t:07.2f}.png")
            if method == "pillow":
                raw = out.with_suffix(".raw.png")
                run_ffmpeg(["-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", f"scale={FRAME_W}:-2", str(raw)], "frame export")
                try:
                    draw_pillow(raw, out, rows)
                finally:
                    raw.unlink(missing_ok=True)
            else:
                run_ffmpeg(["-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", ffmpeg_guide_filter(rows, method == "drawtext"), str(out)], "guide frame")
            print(f"{t:.2f} s -> {out}")
        print(legend(args.step, method != "none"))
    if args.top is not None:
        report_numbers(args.top, args.chin, args.eyes)
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
