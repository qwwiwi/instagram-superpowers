#!/usr/bin/env python3
"""Frames and contact sheets of a finished reel for the acceptance by frames.

Usage:
    frames_sheet.py reel.mp4 --out accept [--region split=120,140,840,610 --region visual=60,130,960,1150]

Exports a frame every 0.5 s (every 0.25 s in the first 5 s and the last 6 s, plus the very last
frame) into ``<out>/frames/tSSSS.ss.jpg`` and builds contact sheets of 12 frames (4 x 3, one third
of the size) with the time above every frame and the interface zone lines drawn in:
``sheet-hook-NN.png``, ``sheet-body-NN.png``, ``sheet-end-NN.png``; ``index.txt`` lists the times
of every sheet.

Labels: Pillow when it is installed, otherwise the ffmpeg ``drawtext`` filter when the local build
has it, otherwise the sheets carry no labels and ``index.txt`` (also printed) is the only time key.

With ``--region`` the script also measures, for every pair of frames 0.5 s apart, the share of
pixels that changed inside the region (frame scaled down 10x, brightness step above 24 of 255) and
writes ``changes.tsv``; it prints the longest stretches without a visible change (below
``--min-change`` percent, 0.4 by default) for every region.
"""

from __future__ import annotations

import argparse
import logging
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from common import MontageError, probe, require_tool, run_ffmpeg, setup_logging

LOG = logging.getLogger("reels.frames_sheet")

FRAME_W = 1080
FRAME_H = 1920
LABEL_BAND = 40
GAP = 8
# Interface zones of the regulation (rule 1.7), in frame pixels.
GUIDE_ROWS = (115, 960, 1520)
BUTTONS_BOX = (950, 1080, 1080, 1720)  # x0, y0, x1, y1
GUIDE_COLOR = (255, 0, 200)
# Change measure: frames are scaled down by this factor and compared in gray.
SMALL = 10
PIXEL_STEP = 24


@dataclass
class Region:
    """A named rectangle of the frame, in frame pixels."""

    name: str
    x: int
    y: int
    w: int
    h: int


@dataclass
class Sheet:
    """One contact sheet: its file name and the times of its frames."""

    name: str
    times: list[float]


# --- times -------------------------------------------------------------------------------------------


def _finite_positive(value: float, what: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise MontageError(f"{what} must be a positive number, got {value}")
    return value


def _grid(start: float, stop: float, step: float) -> list[float]:
    """Times start, start + step, ... below stop (start is snapped up to the step grid)."""
    first = math.ceil(start / step - 1e-9) * step
    out: list[float] = []
    i = 0
    while first + i * step < stop - 1e-9:
        out.append(round(first + i * step, 3))
        i += 1
    return out


def frame_times(duration: float, fps: float, step: float = 0.5, fine: float = 0.25,
                head: float = 5.0, tail: float = 6.0) -> list[float]:
    """Times to export: ``step`` everywhere, ``fine`` in the head and the tail, and the last frame.

    Args:
        duration: Length of the video, seconds.
        fps: Frame rate.
        step: Normal spacing.
        fine: Spacing in the first ``head`` and the last ``tail`` seconds.
        head: Length of the hook window.
        tail: Length of the ending window.

    Returns:
        Sorted unique times, none after the last frame.
    """
    for value, what in ((duration, "duration"), (fps, "fps"), (step, "--step"), (fine, "--fine")):
        _finite_positive(value, what)
    last = round(max(0.0, (round(duration * fps) - 1) / fps), 3)
    times = set(_grid(0.0, duration, step))
    times.update(_grid(0.0, min(head, duration), fine))
    times.update(_grid(max(0.0, duration - tail), duration, fine))
    times.add(last)
    return sorted(t for t in times if t <= last + 1e-9)


def frame_name(seconds: float) -> str:
    """File name of a frame: ``t0012.50.jpg``."""
    return f"t{seconds:07.2f}.jpg"


def group_sheets(times: list[float], duration: float, head: float, tail: float, per_sheet: int) -> list[Sheet]:
    """Split times into hook, body and end sheets of at most ``per_sheet`` frames."""
    tail_start = duration - tail
    groups: dict[str, list[float]] = {"hook": [], "body": [], "end": []}
    for t in times:
        if t < head:
            groups["hook"].append(t)
        elif t >= tail_start - 1e-6:
            groups["end"].append(t)
        else:
            groups["body"].append(t)
    sheets: list[Sheet] = []
    for kind, values in groups.items():
        for number, start in enumerate(range(0, len(values), per_sheet), start=1):
            sheets.append(Sheet(f"sheet-{kind}-{number:02d}.png", values[start:start + per_sheet]))
    return sheets


# --- extraction ----------------------------------------------------------------------------------------


def _select(indexes: list[int]) -> str:
    return "select='" + "+".join(f"eq(n,{i})" for i in indexes) + "'"


def frame_indexes(times: list[float], fps: float, last_index: int) -> list[int]:
    """Frame numbers of the times (clamped to the last frame)."""
    return [min(int(round(t * fps)), last_index) for t in times]


def export_frames(video: Path, times: list[float], fps: float, last_index: int, out_dir: Path) -> list[Path]:
    """Write one full size JPEG per time in a single ffmpeg pass."""
    out_dir.mkdir(parents=True, exist_ok=True)
    indexes = frame_indexes(times, fps, last_index)
    unique = sorted(set(indexes))
    with tempfile.TemporaryDirectory(prefix="frames-", dir=out_dir) as tmp:
        pattern = Path(tmp) / "f-%05d.jpg"
        run_ffmpeg(["-y", "-i", str(video), "-vf", _select(unique), "-fps_mode", "passthrough",
                    "-q:v", "2", str(pattern)], "frame export")
        produced = sorted(Path(tmp).glob("f-*.jpg"))
        if len(produced) != len(unique):
            raise MontageError(f"expected {len(unique)} frames, ffmpeg wrote {len(produced)}")
        by_index = dict(zip(unique, produced))
        paths: list[Path] = []
        for t, index in zip(times, indexes):
            target = out_dir / frame_name(t)
            shutil.copyfile(by_index[index], target)
            paths.append(target)
    return paths


def gray_frames(video: Path, indexes: list[int]) -> list[bytes]:
    """Small gray frames (1/10 size) at the given frame numbers, in the order given."""
    unique = sorted(set(indexes))
    require_tool("ffmpeg")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(video), "-vf",
           f"{_select(unique)},scale={FRAME_W // SMALL}:{FRAME_H // SMALL},format=gray",
           "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    done = subprocess.run(cmd, capture_output=True, check=False)
    if done.returncode != 0:
        raise MontageError(f"gray frame export failed: {done.stderr.decode(errors='replace')[-500:]}")
    size = (FRAME_W // SMALL) * (FRAME_H // SMALL)
    data = done.stdout
    if len(data) != size * len(unique):
        raise MontageError(f"expected {len(unique)} gray frames, got {len(data) / size:.1f}")
    by_index = {index: data[i * size:(i + 1) * size] for i, index in enumerate(unique)}
    return [by_index[i] for i in indexes]


# --- change measure ------------------------------------------------------------------------------------


def parse_region(text: str) -> Region:
    """Parse ``name=x,y,w,h`` (frame pixels)."""
    name, _, rest = text.partition("=")
    try:
        x, y, w, h = (int(v) for v in rest.split(","))
    except ValueError:
        raise MontageError(f"--region must look like name=x,y,w,h, got {text!r}") from None
    if not name or w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > FRAME_W or y + h > FRAME_H:
        raise MontageError(f"--region {text!r} must be named and lie inside {FRAME_W}x{FRAME_H}")
    return Region(name, x, y, w, h)


def change_share(a: bytes, b: bytes, region: Region) -> float:
    """Share (0..1) of small pixels inside the region whose brightness moved by more than PIXEL_STEP."""
    width = FRAME_W // SMALL
    x0, y0 = region.x // SMALL, region.y // SMALL
    x1, y1 = max(x0 + 1, (region.x + region.w) // SMALL), max(y0 + 1, (region.y + region.h) // SMALL)
    changed = 0
    for row in range(y0, y1):
        base = row * width
        for col in range(x0 + base, x1 + base):
            if abs(a[col] - b[col]) > PIXEL_STEP:
                changed += 1
    return changed / ((x1 - x0) * (y1 - y0))


def static_runs(pairs: list[tuple[float, float, float]], min_change: float) -> list[tuple[float, float]]:
    """Stretches without a visible change, longest first.

    Args:
        pairs: ``(t_from, t_to, share)`` for consecutive frames.
        min_change: Smallest share that counts as a change (0..1).

    Returns:
        ``(start, end)`` of every run of consecutive pairs below the threshold.
    """
    runs: list[tuple[float, float]] = []
    start: float | None = None
    end = 0.0
    for t_from, t_to, share in pairs:
        if share < min_change:
            if start is None:
                start = t_from
            end = t_to
        elif start is not None:
            runs.append((start, end))
            start = None
    if start is not None:
        runs.append((start, end))
    return sorted(runs, key=lambda r: r[1] - r[0], reverse=True)


# --- sheets --------------------------------------------------------------------------------------------


def have_pillow() -> bool:
    """True when Pillow can be imported."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def have_drawtext() -> bool:
    """True when the local ffmpeg has the drawtext filter."""
    if not shutil.which("ffmpeg"):
        return False
    done = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=False)
    return any(line.split()[1:2] == ["drawtext"] for line in done.stdout.splitlines() if line.strip())


def guide_boxes(scale: int) -> list[tuple[int, int, int, int]]:
    """Guide rectangles (x, y, w, h) on a thumbnail of 1/scale size."""
    w = FRAME_W // scale
    boxes = [(0, row // scale, w, 2) for row in GUIDE_ROWS]
    x0, y0, x1, y1 = (v // scale for v in BUTTONS_BOX)
    boxes += [(x0, y0, 2, y1 - y0), (x0, y0, x1 - x0, 2), (x0, y1, x1 - x0, 2)]
    return boxes


def label(seconds: float) -> str:
    """Time label of a frame."""
    return f"{seconds:.2f} s"


def sheet_pillow(frames: list[Path], times: list[float], out: Path, cols: int, scale: int, guides: bool) -> None:
    """Draw one contact sheet with Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    tw, th = FRAME_W // scale, FRAME_H // scale
    rows = math.ceil(len(frames) / cols)
    sheet = Image.new("RGB", (cols * tw + (cols + 1) * GAP, rows * (th + LABEL_BAND) + (rows + 1) * GAP), (24, 24, 24))
    try:
        font = ImageFont.load_default(size=28)
    except TypeError:  # Pillow < 10.1 has no sized default font
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(sheet)
    for i, (path, t) in enumerate(zip(frames, times)):
        col, row = i % cols, i // cols
        x = GAP + col * (tw + GAP)
        y = GAP + row * (th + LABEL_BAND + GAP)
        with Image.open(path) as img:
            thumb = img.convert("RGB").resize((tw, th))
        if guides:
            tdraw = ImageDraw.Draw(thumb)
            for bx, by, bw, bh in guide_boxes(scale):
                tdraw.rectangle([bx, by, bx + bw - 1, by + bh - 1], fill=GUIDE_COLOR)
        sheet.paste(thumb, (x, y + LABEL_BAND))
        draw.text((x + 6, y + 4), label(t), fill=(255, 255, 255), font=font)
    sheet.save(out)


def ffmpeg_sheet_args(frames: list[Path], times: list[float], out: Path, cols: int, scale: int,
                      guides: bool, drawtext: bool) -> list[str]:
    """ffmpeg arguments that build one contact sheet with xstack (labels only with drawtext)."""
    tw, th = FRAME_W // scale, FRAME_H // scale
    args: list[str] = ["-y"]
    for path in frames:
        args += ["-i", str(path)]
    chains: list[str] = []
    for i, t in enumerate(times):
        steps = [f"scale={tw}:{th}"]
        if guides:
            steps += [f"drawbox=x={bx}:y={by}:w={bw}:h={bh}:color=0xFF00C8:t=fill" for bx, by, bw, bh in guide_boxes(scale)]
        steps.append(f"pad={tw + GAP}:{th + LABEL_BAND + GAP}:0:{LABEL_BAND}:color=0x181818")
        if drawtext:
            steps.append(f"drawtext=text='{t:.2f} s':x=6:y=6:fontsize=28:fontcolor=white")
        chains.append(f"[{i}:v]" + ",".join(steps) + f"[c{i}]")
    count = len(frames)
    if count == 1:
        graph = ";".join(chains) + ";[c0]null[out]"
    else:
        layout = "|".join(f"{(i % cols) * (tw + GAP)}_{(i // cols) * (th + LABEL_BAND + GAP)}" for i in range(count))
        inputs = "".join(f"[c{i}]" for i in range(count))
        graph = ";".join(chains) + f";{inputs}xstack=inputs={count}:layout={layout}:fill=0x181818[out]"
    return args + ["-filter_complex", graph, "-map", "[out]", "-frames:v", "1", str(out)]


def build_sheets(sheets: list[Sheet], frames_dir: Path, out_dir: Path, cols: int, scale: int, guides: bool) -> str:
    """Write every sheet; returns the labelling method used: pillow, drawtext or none."""
    method = "pillow" if have_pillow() else ("drawtext" if have_drawtext() else "none")
    for sheet in sheets:
        frames = [frames_dir / frame_name(t) for t in sheet.times]
        target = out_dir / sheet.name
        if method == "pillow":
            sheet_pillow(frames, sheet.times, target, cols, scale, guides)
        else:
            run_ffmpeg(ffmpeg_sheet_args(frames, sheet.times, target, cols, scale, guides, method == "drawtext"),
                       "contact sheet")
    return method


def index_text(sheets: list[Sheet]) -> str:
    """Text of index.txt: one line per sheet with the times of its frames, left to right, top to bottom."""
    return "".join(f"{s.name}: " + " ".join(f"{t:.2f}" for t in s.times) + "\n" for s in sheets)


# --- CLI ------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``frames_sheet.py``."""
    parser = argparse.ArgumentParser(description="Frames and contact sheets of a reel for the acceptance by frames")
    parser.add_argument("video", type=Path)
    parser.add_argument("--out", type=Path, default=Path("accept"), help="output folder (default ./accept)")
    parser.add_argument("--step", type=float, default=0.5, help="spacing of frames, seconds (default 0.5)")
    parser.add_argument("--fine", type=float, default=0.25, help="spacing in the hook and the ending (default 0.25)")
    parser.add_argument("--head", type=float, default=5.0, help="hook window, seconds (default 5)")
    parser.add_argument("--tail", type=float, default=6.0, help="ending window, seconds (default 6)")
    parser.add_argument("--cols", type=int, default=4, help="frames per row of a sheet (default 4)")
    parser.add_argument("--rows", type=int, default=3, help="rows per sheet (default 3)")
    parser.add_argument("--scale", type=int, default=3, help="thumbnail is 1/scale of the frame (default 3)")
    parser.add_argument("--no-guides", action="store_true", help="do not draw the interface zone lines")
    parser.add_argument("--region", action="append", default=[], metavar="NAME=X,Y,W,H",
                        help="measure changes inside this rectangle (repeatable)")
    parser.add_argument("--min-change", type=float, default=0.4,
                        help="smallest visible change, percent of the region (default 0.4)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    """Export frames, build sheets, measure changes. Returns the exit code."""
    video: Path = args.video.expanduser()
    if not video.is_file():
        raise MontageError(f"File not found: {video}")
    for value, what in ((args.head, "--head"), (args.tail, "--tail"), (args.min_change, "--min-change")):
        _finite_positive(value, what)
    if args.cols < 1 or args.rows < 1 or args.scale < 1:
        raise MontageError("--cols, --rows and --scale must be at least 1")
    regions = [parse_region(text) for text in args.region]
    info = probe(video)
    if (info.width, info.height) != (FRAME_W, FRAME_H):
        raise MontageError(f"{video} is {info.width}x{info.height}; the regulation is written for {FRAME_W}x{FRAME_H}")
    fps = info.fps or 30.0
    last_index = max(0, round(info.duration * fps) - 1)
    times = frame_times(info.duration, fps, args.step, args.fine, args.head, args.tail)
    out: Path = args.out
    frames_dir = out / "frames"
    export_frames(video, times, fps, last_index, frames_dir)
    sheets = group_sheets(times, info.duration, args.head, args.tail, args.cols * args.rows)
    method = build_sheets(sheets, frames_dir, out, args.cols, args.scale, not args.no_guides)
    index = index_text(sheets)
    (out / "index.txt").write_text(index, encoding="utf-8")
    print(f"{len(times)} frames -> {frames_dir}; {len(sheets)} sheets -> {out} (labels: {method})")
    if method == "none":
        print("No Pillow and no drawtext: the sheets have no time labels, read the times here "
              "(left to right, top to bottom):")
    print(index, end="")
    if regions:
        grid = [t for t in times if abs(t / args.step - round(t / args.step)) < 1e-6]
        indexes = frame_indexes(grid, fps, last_index)
        gray = gray_frames(video, indexes)
        rows = ["t_from\tt_to\t" + "\t".join(r.name for r in regions)]
        per_region: dict[str, list[tuple[float, float, float]]] = {r.name: [] for r in regions}
        for k in range(1, len(grid)):
            shares = [change_share(gray[k - 1], gray[k], r) for r in regions]
            rows.append(f"{grid[k - 1]:.2f}\t{grid[k]:.2f}\t" + "\t".join(f"{s * 100:.2f}" for s in shares))
            for r, s in zip(regions, shares):
                per_region[r.name].append((grid[k - 1], grid[k], s))
        (out / "changes.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"changes (percent of the region, frames {args.step:g} s apart) -> {out / 'changes.tsv'}")
        for r in regions:
            runs = [run_ for run_ in static_runs(per_region[r.name], args.min_change / 100) if run_[1] - run_[0] >= 1.0]
            text = ", ".join(f"{a:.2f}-{b:.2f} ({b - a:.1f} s)" for a, b in runs[:8]) or "none of 1.0 s or longer"
            print(f"  {r.name}: stretches without a change >= {args.min_change:g} %: {text}")
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
