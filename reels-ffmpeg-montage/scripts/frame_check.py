#!/usr/bin/env python3
"""Measurable geometric checks on the frames of a reel, and the on-screen words of its composition.

Usage:
    frame_check.py frames accept/frames --plans plans.json [--duration 42.7]
    frame_check.py text build/index.html [--words-out screen-words.txt]

``frames`` reads the frames that ``frames_sheet.py`` exported (``tSSSS.ss.jpg``) and, for every frame
of a SPLIT or VISUAL plan (``plans.json``: ``[{"start": 0, "end": 3.2, "kind": "split"}, ...]``,
kinds ``split`` / ``visual`` / ``speaker``):

  * empty band: rows without content (fewer than ``EDGE_PIXELS`` sharp edges in the row) running
    ``EMPTY_BAND_SPLIT`` px or more inside the split scene (x 120-960, y 140-750) or
    ``EMPTY_BAND_VISUAL`` px or more inside the visual layout (x 60-1020, y 140-1280); FAIL when a band
    is >= the limit;
  * top service zone y 0-140: a row with content (text, a strip of stickers) or a compact object (a
    connected patch that stands out from the zone background, at least ``MIN_OBJECT`` x ``MIN_OBJECT`` px:
    an icon, a badge, a corner of a card);
  * light scenes: share of the REEL DURATION (the denominator is the whole reel, every plan kind) whose
    scene area has a median brightness above ``LIGHT_LUMA``; every frame stands for the time from it to the
    next sampled frame, so the denser sampling of the head and the end does not weigh more. FAIL when the share is above ``MAX_LIGHT_SHARE``.

``text`` reads a HyperFrames composition (HTML): every caption line (``.line`` inside an element with a
``cap`` class) holds at most 3 words, and all on-screen words go to ``--words-out`` for the spelling pass
(rule 7.9). Whether the numbers, names and files on screen are said in the speech is NOT checked by a
script: the reviewer compares them with the transcript (acceptance checklist).

The numbers are a pointer, not the judge: the reviewer still looks at every sheet. Exit code 1 when a
check fails, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import logging
import re
import statistics
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from common import MontageError, read_json, require_tool, setup_logging

LOG = logging.getLogger("reels.frame_check")

FRAME_W = 1080
FRAME_H = 1920
# A row has content when it holds at least this many sharp horizontal edges (brightness step > EDGE_STEP).
# A bare card body crossed only by its two side borders gives about 8.
EDGE_STEP = 24
EDGE_PIXELS = 16
TOP_ZONE = 140
SPLIT_SCENE = (120, 140, 960, 750)      # x0, y0, x1, y1
VISUAL_SCENE = (60, 140, 1020, 1280)
EMPTY_BAND_SPLIT = 250
EMPTY_BAND_VISUAL = 300
LIGHT_LUMA = 170
MAX_LIGHT_SHARE = 0.25
MAX_CAPTION_WORDS = 3
# Top zone objects: a pixel stands out when it differs from the zone median by more than OBJECT_CONTRAST;
# a connected patch of such pixels at least MIN_OBJECT px wide and tall is an object (searched on a 2 px grid).
OBJECT_CONTRAST = 40
MIN_OBJECT = 24
OBJECT_GRID = 2
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*")


class CheckError(MontageError):
    """The check cannot run on this input."""


@dataclass
class Result:
    """Outcome of one check."""

    name: str
    ok: bool
    detail: str
    warning: bool = False

    def line(self) -> str:
        """One printable line."""
        return f"{'WARN' if self.warning else ('PASS' if self.ok else 'FAIL')}  {self.name}: {self.detail}"


# --- frames -----------------------------------------------------------------------------------------------------


def row_edges(gray: bytes, width: int, y: int, x0: int, x1: int) -> int:
    """Sharp horizontal brightness steps in row ``y`` between ``x0`` and ``x1``."""
    base = y * width
    row = gray[base + x0: base + x1]
    return sum(1 for i in range(1, len(row) - 1) if abs(row[i + 1] - row[i - 1]) > EDGE_STEP)


def empty_bands(gray: bytes, width: int, box: tuple[int, int, int, int], min_height: int) -> list[tuple[int, int]]:
    """Runs of rows without content inside ``box`` that are at least ``min_height`` px tall.

    Args:
        gray: 8-bit grey frame, row by row.
        width: Frame width.
        box: ``(x0, y0, x1, y1)``.
        min_height: Shortest run reported.

    Returns:
        ``(first row, last row + 1)`` of every empty run.
    """
    x0, y0, x1, y1 = box
    out: list[tuple[int, int]] = []
    start: int | None = None
    for y in range(y0, y1):
        empty = row_edges(gray, width, y, x0, x1) < EDGE_PIXELS
        if empty and start is None:
            start = y
        elif not empty and start is not None:
            if y - start >= min_height:
                out.append((start, y))
            start = None
    if start is not None and y1 - start >= min_height:
        out.append((start, y1))
    return out


def top_zone_rows(gray: bytes, width: int, limit: int = TOP_ZONE) -> list[int]:
    """Rows of the top service zone that carry content."""
    return [y for y in range(0, limit) if row_edges(gray, width, y, 0, width) >= EDGE_PIXELS]


def top_zone_objects(gray: bytes, width: int, limit: int = TOP_ZONE) -> list[tuple[int, int, int, int]]:
    """Compact objects in the top service zone: connected patches that stand out from the zone background.

    Args:
        gray: 8-bit grey frame, row by row.
        width: Frame width.
        limit: Height of the zone.

    Returns:
        ``(x0, y0, x1, y1)`` of every patch at least ``MIN_OBJECT`` px wide and tall.
    """
    step = OBJECT_GRID
    cols, rows = width // step, limit // step
    values = [gray[(r * step) * width + c * step] for r in range(rows) for c in range(cols)]
    if not values:
        return []
    background = statistics.median(values)
    mask = [abs(v - background) > OBJECT_CONTRAST for v in values]
    seen = [False] * len(mask)
    found: list[tuple[int, int, int, int]] = []
    for index, hit in enumerate(mask):
        if not hit or seen[index]:
            continue
        seen[index] = True
        queue = deque([index])
        c0 = c1 = index % cols
        r0 = r1 = index // cols
        while queue:
            cell = queue.popleft()
            r, c = divmod(cell, cols)
            c0, c1, r0, r1 = min(c0, c), max(c1, c), min(r0, r), max(r1, r)
            for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if 0 <= nr < rows and 0 <= nc < cols:
                    other = nr * cols + nc
                    if mask[other] and not seen[other]:
                        seen[other] = True
                        queue.append(other)
        box = (c0 * step, r0 * step, (c1 + 1) * step, (r1 + 1) * step)
        if box[2] - box[0] >= MIN_OBJECT and box[3] - box[1] >= MIN_OBJECT:
            found.append(box)
    return found


def median_luma(gray: bytes, width: int, box: tuple[int, int, int, int], step: int = 8) -> float:
    """Median brightness of a box, sampled every ``step`` px."""
    x0, y0, x1, y1 = box
    values = [gray[y * width + x] for y in range(y0, y1, step) for x in range(x0, x1, step)]
    return float(statistics.median(values)) if values else 0.0


@dataclass
class Plan:
    """A plan of the reel: its time range and kind."""

    start: float
    end: float
    kind: str


def load_plans(path: Path) -> list[Plan]:
    """Read ``plans.json``.

    Raises:
        CheckError: Not a list of plans with start, end and a known kind.
    """
    data = read_json(path)
    if isinstance(data, dict):
        data = data.get("plans")
    if not isinstance(data, list):
        raise CheckError(f"{path}: need a list of plans (start, end, kind)")
    plans: list[Plan] = []
    for item in data:
        if not isinstance(item, dict) or str(item.get("kind", "")).lower() not in ("split", "visual", "speaker"):
            raise CheckError(f"{path}: every plan needs kind split, visual or speaker: {item!r}")
        try:
            plans.append(Plan(float(item["start"]), float(item["end"]), str(item["kind"]).lower()))
        except (KeyError, TypeError, ValueError):
            raise CheckError(f"{path}: plan without numeric start and end: {item!r}") from None
    return sorted(plans, key=lambda p: p.start)


def kind_at(plans: list[Plan], t: float) -> str:
    """Kind of the plan on screen at ``t`` (the last plan that started at or before it)."""
    current = "speaker"
    for plan in plans:
        if plan.start <= t + 1e-6:
            current = plan.kind
    return current


@dataclass
class FrameReport:
    """Findings on one frame."""

    t: float
    kind: str
    bands: list[tuple[int, int]] = field(default_factory=list)
    top_rows: list[int] = field(default_factory=list)
    top_objects: list[tuple[int, int, int, int]] = field(default_factory=list)
    light: bool = False


def analyse_frame(gray: bytes, t: float, kind: str, width: int = FRAME_W) -> FrameReport:
    """All frame checks for one frame of a known plan kind."""
    report = FrameReport(t, kind)
    if kind == "speaker":
        return report
    box, limit = (SPLIT_SCENE, EMPTY_BAND_SPLIT) if kind == "split" else (VISUAL_SCENE, EMPTY_BAND_VISUAL)
    report.bands = empty_bands(gray, width, box, limit)
    report.top_rows = top_zone_rows(gray, width)
    report.top_objects = top_zone_objects(gray, width)
    scene = (0, 0, width, 960) if kind == "split" else (0, 0, width, FRAME_H)
    report.light = median_luma(gray, width, scene) > LIGHT_LUMA
    return report


def frame_weights(times: list[float], duration: float) -> list[float]:
    """Seconds of the reel each sampled frame stands for: from its time to the next frame (the first frame
    from 0, the last one to ``duration``).

    Args:
        times: Frame times, ascending.
        duration: Reel length, seconds.
    """
    if not times:
        return []
    bounds = [0.0] + list(times[1:]) + [max(duration, times[-1])]
    return [max(0.0, b - a) for a, b in zip(bounds, bounds[1:])]


def frame_results(reports: list[FrameReport], duration: float | None = None) -> list[Result]:
    """Fold frame reports into the three frame checks.

    Args:
        reports: One report per sampled frame (any order).
        duration: Reel length, seconds (default: the time of the last frame).
    """
    reports = sorted(reports, key=lambda r: r.t)
    bands = [r for r in reports if r.bands]
    top = [r for r in reports if r.top_rows or r.top_objects]
    total = duration if duration is not None else (reports[-1].t if reports else 0.0)
    weights = frame_weights([r.t for r in reports], total)
    light_seconds = sum(w for r, w in zip(reports, weights) if r.light)
    share = light_seconds / total if total > 0 else 0.0
    light = [r for r in reports if r.light]

    def top_detail(r: FrameReport) -> str:
        parts = [f"rows {r.top_rows[0]}-{r.top_rows[-1]}"] if r.top_rows else []
        parts += [f"object x {x0}-{x1} y {y0}-{y1}" for x0, y0, x1, y1 in r.top_objects[:3]]
        return f"{r.t:.2f} s: " + ", ".join(parts)

    return [
        Result("empty bands", not bands, "none" if not bands else "; ".join(
            f"{r.t:.2f} s {r.kind}: " + ", ".join(f"y {a}-{b} ({b - a} px)" for a, b in r.bands) for r in bands[:12])),
        Result(f"top zone y 0-{TOP_ZONE}", not top, "nothing there" if not top else "; ".join(
            top_detail(r) for r in top[:12])),
        Result("light scenes", share <= MAX_LIGHT_SHARE,
               f"{light_seconds:.2f} s of {total:.2f} s = {share * 100:.1f} % of the reel duration "
               f"(max {MAX_LIGHT_SHARE * 100:.0f} %)"
               + (": frames " + ", ".join(f"{r.t:.2f}" for r in light[:40]) if light else "")),
    ]


def frame_time(path: Path) -> float:
    """Time of a frame from its ``tSSSS.ss.jpg`` name.

    Raises:
        CheckError: The name is not a frame name.
    """
    match = re.fullmatch(r"t(\d+\.\d+)\.(jpg|png)", path.name)
    if not match:
        raise CheckError(f"not a frame of frames_sheet.py: {path.name}")
    return float(match.group(1))


def read_gray(path: Path) -> bytes:
    """Decode an image into 1080 x 1920 8-bit grey with ffmpeg (no Pillow needed)."""
    done = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vf",
                           f"scale={FRAME_W}:{FRAME_H},format=gray", "-f", "rawvideo", "-"],
                          capture_output=True, check=False)
    if done.returncode != 0:
        raise CheckError(f"{path}: ffmpeg could not decode the frame: {done.stderr.decode(errors='replace')[-300:]}")
    data = done.stdout
    if len(data) != FRAME_W * FRAME_H:
        raise CheckError(f"{path}: decoded {len(data)} bytes, need {FRAME_W * FRAME_H}")
    return data


def run_frames(folder: Path, plans: list[Plan], duration: float | None = None) -> list[Result]:
    """Frame checks over every frame of a folder.

    Args:
        folder: Frames of ``frames_sheet.py``.
        plans: Plans of the reel.
        duration: Reel length (default: the end of the last plan or the last frame, whichever is later).
    """
    require_tool("ffmpeg")
    frames = sorted(p for p in folder.iterdir() if re.fullmatch(r"t\d+\.\d+\.(jpg|png)", p.name))
    if not frames:
        raise CheckError(f"no frames in {folder}: run frames_sheet.py first")
    reports = []
    for path in frames:
        t = frame_time(path)
        reports.append(analyse_frame(read_gray(path), t, kind_at(plans, t)))
    if duration is None:
        duration = max([p.end for p in plans] + [r.t for r in reports])
    return frame_results(reports, duration)


# --- text ----------------------------------------------------------------------------------------------------------


class CompositionText(HTMLParser):
    """Caption lines and the other visible text of a composition."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, bool, bool]] = []  # tag, inside a caption, is a caption line
        self.lines: list[list[str]] = []
        self.screen: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        in_cap = (self.stack[-1][1] if self.stack else False) or "cap" in classes
        is_line = in_cap and "line" in classes
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("br", "img", "input", "meta", "link", "source", "path", "circle", "rect"):
            return
        self.stack.append((tag, in_cap, is_line))
        if is_line:
            self.lines.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self.skip or not data.strip():
            return
        in_cap = self.stack[-1][1] if self.stack else False
        in_line = any(entry[2] for entry in self.stack)
        if in_cap and in_line and self.lines:
            self.lines[-1].extend(data.split())
        elif not in_cap:
            self.screen.append(data.strip())


def text_results(html: str) -> tuple[list[Result], list[str]]:
    """Text checks of a composition.

    Returns:
        ``(results, sorted unique on-screen and caption words)``.
    """
    parser = CompositionText()
    parser.feed(html)
    long_lines = [" ".join(line) for line in parser.lines if len(line) > MAX_CAPTION_WORDS]
    results = [Result("caption words per line", not long_lines,
                      f"{len(parser.lines)} lines, all <= {MAX_CAPTION_WORDS} words" if not long_lines
                      else "over 3 words: " + " | ".join(long_lines[:10]))]
    screen_words = {w for text in parser.screen for w in WORD_RE.findall(text)}
    caption_words = {w.strip(".,!?«»\"'():;") for line in parser.lines for w in line}
    return results, sorted((screen_words | caption_words) - {""}, key=str.lower)


# --- command line --------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``frame_check.py``."""
    parser = argparse.ArgumentParser(description="Measurable acceptance checks on frames and on the composition text")
    sub = parser.add_subparsers(dest="mode", required=True)
    frames = sub.add_parser("frames", help="empty bands, top zone, light scenes on exported frames")
    frames.add_argument("folder", type=Path, help="accept/frames from frames_sheet.py")
    frames.add_argument("--plans", type=Path, required=True, help="plans.json: [{start, end, kind}] with kind split/visual/speaker")
    frames.add_argument("--duration", type=float, help="reel length, s (default: end of the last plan or the last frame)")
    text = sub.add_parser("text", help="caption line length and the on-screen word list for the spelling pass")
    text.add_argument("html", type=Path, help="composition HTML (build/index.html)")
    text.add_argument("--words-out", type=Path, help="write every on-screen word here for the spelling pass")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run. Exit code 1 when a check fails."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        if args.mode == "frames":
            results = run_frames(args.folder, load_plans(args.plans), args.duration)
        else:
            results, words = text_results(args.html.read_text(encoding="utf-8"))
            if args.words_out:
                args.words_out.write_text("\n".join(words) + "\n", encoding="utf-8")
                print(f"{len(words)} on-screen words -> {args.words_out}: read each one for spelling (rule 7.9)")
    except (MontageError, OSError) as exc:
        LOG.error("%s", exc)
        return 2
    for result in results:
        print(result.line())
    failed = [r for r in results if not r.ok]
    print(f"\n{'FAIL' if failed else 'PASS'}: {len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
