#!/usr/bin/env python3
"""Measurable acceptance checks on the frames of a reel and on the text of its composition.

Usage:
    frame_check.py frames accept/frames --plans plans.json
    frame_check.py text build/index.html --words words-cut.json [--words-out screen-words.txt]

``frames`` reads the frames that ``frames_sheet.py`` exported (``tSSSS.ss.jpg``) and, for every frame
of a SPLIT or VISUAL plan (``plans.json``: ``[{"start": 0, "end": 3.2, "kind": "split"}, ...]``,
kinds ``split`` / ``visual`` / ``speaker``):

  * empty band: rows without content (fewer than ``EDGE_PIXELS`` sharp edges in the row) running
    ``EMPTY_BAND_SPLIT`` px or more inside the split scene (x 120-960, y 140-750) or
    ``EMPTY_BAND_VISUAL`` px or more inside the visual layout (x 60-1020, y 140-1280);
  * top service zone: any row of y 0-140 with content (a sticker, a badge, text);
  * light scenes: share of frames whose scene area has a median brightness above ``LIGHT_LUMA``;
    more than ``MAX_LIGHT_SHARE`` of all frames of the reel is a FAIL.

``text`` reads a HyperFrames composition (HTML) and the words of the cut:

  * every caption line (``.line`` inside an element with a ``cap`` class) holds at most 3 words;
  * every number on screen outside the captions is said in the speech (digits or Russian number words);
  * file names, paths and commands on screen (``index.html``, ``src/app``, ``npm run``) are said in the
    speech, otherwise they are invented filler;
  * the other on-screen words that are not in the speech are listed as a WARNING to read, and all
    on-screen words go to ``--words-out`` for the spelling pass (rule 7.9).

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
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from common import MontageError, load_words, read_json, require_tool, setup_logging

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

NUMBER_WORDS = {
    "ноль": 0, "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100, "двести": 200,
    "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800,
    "девятьсот": 900,
}
# Case forms of the number words most often said («трёх файлов», «двух дней»).
NUMBER_STEMS = {"одн": 1, "двух": 2, "двум": 2, "трёх": 3, "трех": 3, "трём": 3, "трем": 3, "четырёх": 4,
                "четырех": 4, "пяти": 5, "шести": 6, "семи": 7, "восьми": 8, "девяти": 9, "десяти": 10,
                "двадцати": 20, "тридцати": 30, "сорока": 40, "пятидесяти": 50, "ста": 100, "сотн": 100}
SCALE_WORDS = {"тысяч": 1000, "миллион": 1_000_000}
FILE_LIKE = re.compile(r"[\w-]+\.[a-z]{1,5}\b|\w+/\w+|^(npm|npx|git|mkdir|cd|pip|python3?)$", re.I)
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*|\d[\d\s]*(?:[.,]\d+)?")


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
    light: bool = False


def analyse_frame(gray: bytes, t: float, kind: str, width: int = FRAME_W) -> FrameReport:
    """All frame checks for one frame of a known plan kind."""
    report = FrameReport(t, kind)
    if kind == "speaker":
        return report
    box, limit = (SPLIT_SCENE, EMPTY_BAND_SPLIT) if kind == "split" else (VISUAL_SCENE, EMPTY_BAND_VISUAL)
    report.bands = empty_bands(gray, width, box, limit)
    report.top_rows = top_zone_rows(gray, width)
    scene = (0, 0, width, 960) if kind == "split" else (0, 0, width, FRAME_H)
    report.light = median_luma(gray, width, scene) > LIGHT_LUMA
    return report


def frame_results(reports: list[FrameReport]) -> list[Result]:
    """Fold frame reports into the three frame checks."""
    bands = [r for r in reports if r.bands]
    top = [r for r in reports if r.top_rows]
    light = [r for r in reports if r.light]
    share = len(light) / len(reports) if reports else 0.0
    return [
        Result("empty bands", not bands, "none" if not bands else "; ".join(
            f"{r.t:.2f} s {r.kind}: " + ", ".join(f"y {a}-{b} ({b - a} px)" for a, b in r.bands) for r in bands[:12])),
        Result("top zone y 0-140", not top, "nothing there" if not top else "; ".join(
            f"{r.t:.2f} s: rows {r.top_rows[0]}-{r.top_rows[-1]}" for r in top[:12])),
        Result("light scenes", share <= MAX_LIGHT_SHARE,
               f"{share * 100:.0f} % of the frames (max {MAX_LIGHT_SHARE * 100:.0f} %)"
               + (": " + ", ".join(f"{r.t:.2f}" for r in light[:40]) if light else "")),
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


def run_frames(folder: Path, plans: list[Plan]) -> list[Result]:
    """Frame checks over every frame of a folder."""
    require_tool("ffmpeg")
    frames = sorted(p for p in folder.iterdir() if re.fullmatch(r"t\d+\.\d+\.(jpg|png)", p.name))
    if not frames:
        raise CheckError(f"no frames in {folder}: run frames_sheet.py first")
    reports = []
    for path in frames:
        t = frame_time(path)
        reports.append(analyse_frame(read_gray(path), t, kind_at(plans, t)))
    return frame_results(reports)


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


def speech_numbers(words: list[str]) -> set[int]:
    """Numbers said in the speech: digits and Russian number words (``двадцать пять`` is 25)."""
    found: set[int] = set()
    total: int | None = None
    for raw in words:
        word = raw.lower().strip(".,!?«»\"'():;—–-")
        digits = re.sub(r"[\s ]", "", word)
        value: int | None = None
        if digits.isdigit():
            found.add(int(digits))
            total = None
            continue
        if word in NUMBER_WORDS:
            value = NUMBER_WORDS[word]
        else:
            value = next((v for stem, v in NUMBER_STEMS.items() if word.startswith(stem) and len(word) <= len(stem) + 3), None)
        scale = next((v for stem, v in SCALE_WORDS.items() if word.startswith(stem)), None)
        if value is not None:
            total = value if total is None or total % 10 or value >= total else total + value
            found.add(total)
            found.add(value)
        elif scale is not None:
            total = (total or 1) * scale
            found.add(total)
        else:
            total = None
    return found


def screen_numbers(texts: list[str]) -> list[tuple[str, int]]:
    """Numbers shown on screen: ``(as written, value)``; ``2 400`` is 2400, ``3/3`` gives 3 and 3."""
    out: list[tuple[str, int]] = []
    for text in texts:
        for match in re.finditer(r"\d{1,3}(?:[  ]\d{3})+|\d+", text):
            out.append((match.group(0), int(re.sub(r"\D", "", match.group(0)))))
    return out


def stem(word: str) -> str:
    """Crude stem for matching a screen word to a said word (first five letters, ё as е)."""
    return word.lower().replace("ё", "е")[:5]


def text_results(html: str, said: list[str]) -> tuple[list[Result], list[str]]:
    """Text checks of a composition against the words of the speech.

    Returns:
        ``(results, sorted unique on-screen words)``.
    """
    parser = CompositionText()
    parser.feed(html)
    results: list[Result] = []
    long_lines = [" ".join(line) for line in parser.lines if len(line) > MAX_CAPTION_WORDS]
    results.append(Result("caption words per line", not long_lines,
                          f"{len(parser.lines)} lines, all <= {MAX_CAPTION_WORDS} words" if not long_lines
                          else "over 3 words: " + " | ".join(long_lines[:10])))
    numbers = speech_numbers(said)
    unsaid = sorted({text for text, value in screen_numbers(parser.screen) if value not in numbers and value > 1})
    results.append(Result("numbers come from the speech", not unsaid,
                          "every number on screen is said" if not unsaid else "not said: " + ", ".join(unsaid[:20])))
    said_stems = {stem(w.strip(".,!?«»\"'():;")) for w in said}
    screen_tokens = [tok for text in parser.screen for tok in text.split()]
    filler = sorted({tok for tok in screen_tokens if FILE_LIKE.search(tok) and stem(re.split(r"[./]", tok)[0]) not in said_stems})
    results.append(Result("no invented file names or commands", not filler,
                          "none" if not filler else "not in the speech: " + ", ".join(filler[:20])))
    screen_words = sorted({w for text in parser.screen for w in WORD_RE.findall(text) if not w[0].isdigit()},
                          key=str.lower)
    caption_words = sorted({w for line in parser.lines for w in line}, key=str.lower)
    other = [w for w in screen_words if stem(w) not in said_stems and len(w) > 2]
    if other:
        results.append(Result("screen words not in the speech", True, ", ".join(other[:40]), warning=True))
    return results, sorted(set(screen_words) | set(caption_words), key=str.lower)


# --- command line --------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``frame_check.py``."""
    parser = argparse.ArgumentParser(description="Measurable acceptance checks on frames and on the composition text")
    sub = parser.add_subparsers(dest="mode", required=True)
    frames = sub.add_parser("frames", help="empty bands, top zone, light scenes on exported frames")
    frames.add_argument("folder", type=Path, help="accept/frames from frames_sheet.py")
    frames.add_argument("--plans", type=Path, required=True, help="plans.json: [{start, end, kind}] with kind split/visual/speaker")
    text = sub.add_parser("text", help="caption line length, numbers and file names against the speech")
    text.add_argument("html", type=Path, help="composition HTML (build/index.html)")
    text.add_argument("--words", type=Path, required=True, help="words-cut.json")
    text.add_argument("--words-out", type=Path, help="write every on-screen word here for the spelling pass")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run. Exit code 1 when a check fails."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        if args.mode == "frames":
            results = run_frames(args.folder, load_plans(args.plans))
        else:
            said = [w.text for w in load_words(args.words).words]
            results, words = text_results(args.html.read_text(encoding="utf-8"), said)
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
