#!/usr/bin/env python3
"""Burned-in captions from words-cut.json, two backends.

Usage:
    captions.py reel-cut.mp4 words-cut.json [--backend auto|ass|png] [--out reel-captioned.mp4]

Layout: 2-4 words per line, the current word highlighted, a large bold font.
The line sits in the lower-middle part of a 1080x1920 frame: its bottom edge is at
y=1250, above the zone the Instagram interface covers at the bottom, and it is kept
inside side margins that stay clear of the buttons on the right. No line is wider
than ``MAX_LINE_WIDTH`` pixels (a lone word that is wider is shrunk).

Backends (``--backend``):
  * ``ass`` - an ASS file burned in by ffmpeg's libass (``ass`` filter). The font is a
    family name; fontconfig finds the file. Needs an ffmpeg built with libass.
  * ``png`` - every caption state is drawn to a transparent PNG with Pillow and the
    pictures are laid over the video by the ``overlay`` filter that every ffmpeg has
    (see ``captions_png.py``). The font is a file (``--font`` takes a path). Needs the
    optional Pillow package.
  * ``auto`` (default) - ``ass`` when this ffmpeg has libass, otherwise ``png``.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from common import OUT_HEIGHT, OUT_WIDTH, MontageError, Word, load_words, require_tool, setup_logging

LOG = logging.getLogger("reels.captions")

FONT_SIZE = 78
OUTLINE = 7
# Bottom edge of the caption block, pixels from the top (the interface covers about 1520 and below).
SAFE_BOTTOM_Y = 1250
SIDE_MARGIN = 120
MAX_LINE_WIDTH = OUT_WIDTH - 2 * SIDE_MARGIN
MAX_WORDS = 4
# A pause between words longer than this ends a line. It is a little under the gap that
# plan_cut.py leaves at a trimmed pause (0.25 s), so a cut pause also breaks the line.
GAP_BREAK = 0.22
# A caption stays on screen this long after its last word, if no other caption follows.
HOLD_AFTER = 0.15
MIN_EVENT = 0.03
HIGHLIGHT = "&H00D7FF&"  # BGR order: amber
WHITE = "&HFFFFFF&"
PREFERRED_FONTS = ("Montserrat", "Inter", "Arial", "Helvetica Neue", "DejaVu Sans", "Liberation Sans", "Noto Sans")
FALLBACK_FONT = "Arial"
BACKENDS = ("auto", "ass", "png")
LIBASS_HELP = (
    "ffmpeg here has no libass (the `ass` filter is missing), so the ass backend cannot burn captions. "
    "Use --backend png (or leave --backend auto): it needs only the `overlay` filter and Pillow. "
    "To use ass anyway, install an ffmpeg build with libass (check: ffmpeg -hide_banner -filters | grep -E ' (ass|subtitles) '). "
    "The standard Homebrew `ffmpeg` formula has no libass."
)

_NARROW = set("il.,:;!|' ")
_WIDE = set("мшщюжыфМШЩЮЖЫФWM@")


def has_libass() -> bool:
    """True when the installed ffmpeg offers the ``ass`` filter."""
    require_tool("ffmpeg")
    done = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=False)
    return any(line.split()[1:2] == ["ass"] for line in done.stdout.splitlines() if line[:1] == " ")


def estimate_width(text: str, size: float = FONT_SIZE) -> float:
    """Conservative width in pixels of a bold sans line of ``text`` at font size ``size``.

    The estimate has no font file to measure, so it errs on the wide side.
    """
    total = 0.0
    for ch in text:
        if ch in _NARROW:
            total += 0.32
        elif ch in _WIDE:
            total += 0.9
        elif ch.isupper():
            total += 0.74
        else:
            total += 0.6
    return total * size


def installed_cyrillic_families() -> set[str] | None:
    """Lower-cased font families that cover Russian, or None when fontconfig is not available."""
    if not shutil.which("fc-list"):
        return None
    done = subprocess.run(["fc-list", ":lang=ru", "family"], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return None
    families: set[str] = set()
    for line in done.stdout.splitlines():
        for name in line.split(","):
            if name.strip():
                families.add(name.strip().lower())
    return families


def pick_font(requested: str | None = None, installed: set[str] | None = None) -> str:
    """Choose the font family for the style.

    Args:
        requested: Family given by the user; used as is.
        installed: Lower-cased families with Cyrillic, or None when unknown.

    Returns:
        A family name.
    """
    if requested:
        return requested
    if installed:
        for name in PREFERRED_FONTS:
            if name.lower() in installed:
                return name
        LOG.warning("None of %s is installed; writing %s and leaving the choice to fontconfig. Use --font to set a family.",
                    ", ".join(PREFERRED_FONTS), FALLBACK_FONT)
    else:
        LOG.warning("Cannot list installed fonts (no fontconfig); writing %s. Use --font to set a family.", FALLBACK_FONT)
    return FALLBACK_FONT


@dataclass
class Line:
    """A caption line: consecutive words shown together."""

    words: list[Word]

    @property
    def text(self) -> str:
        """Plain text of the line."""
        return " ".join(w.text for w in self.words)


def clean_text(text: str) -> str:
    """Make a word safe for an ASS text field."""
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def group_lines(words: list[Word], max_words: int = MAX_WORDS, max_width: float = MAX_LINE_WIDTH,
                size: float = FONT_SIZE, gap_break: float = GAP_BREAK) -> list[Line]:
    """Split words into caption lines.

    A line ends at ``max_words`` words, when the next word would make it wider than
    ``max_width``, at a pause longer than ``gap_break``, or after sentence-ending
    punctuation. A line of one word is joined to the previous line when that fits.

    Args:
        words: Words on the output clock.
        max_words: Most words per line.
        max_width: Widest line, pixels.
        size: Font size, pixels.
        gap_break: Pause that ends a line, seconds.

    Returns:
        Lines in time order.
    """
    cleaned = [Word(clean_text(w.text), w.start, w.end) for w in words if clean_text(w.text)]
    lines: list[list[Word]] = []
    for word in cleaned:
        if lines:
            current = lines[-1]
            candidate = " ".join(w.text for w in [*current, word])
            sentence_end = current[-1].text.endswith((".", "?", "!", "…"))
            gap = word.start - current[-1].end
            if len(current) < max_words and not sentence_end and gap <= gap_break and estimate_width(candidate, size) <= max_width:
                current.append(word)
                continue
        lines.append([word])
    merged: list[list[Word]] = []
    for line in lines:
        if (len(line) == 1 and merged and len(merged[-1]) < max_words
                and line[0].start - merged[-1][-1].end <= gap_break
                and not merged[-1][-1].text.endswith((".", "?", "!", "…"))
                and estimate_width(" ".join(w.text for w in [*merged[-1], line[0]]), size) <= max_width):
            merged[-1].append(line[0])
        else:
            merged.append(line)
    return [Line(words=line) for line in merged]


def ass_time(seconds: float) -> str:
    """Seconds as ``H:MM:SS.cc``."""
    total = max(0, round(seconds * 100))
    cs = total % 100
    sec = (total // 100) % 60
    minutes = (total // 6000) % 60
    hours = total // 360000
    return f"{hours}:{minutes:02d}:{sec:02d}.{cs:02d}"


@dataclass
class State:
    """One picture of the captions: a line with one word highlighted, shown from ``start`` to ``end``."""

    start: float
    end: float
    line: int
    pos: int


def build_states(lines: list[Line]) -> list[State]:
    """Caption states: one per word, the whole line shown with that word highlighted.

    States never overlap and start times never go backwards. A word stays
    highlighted until the next word of its line starts; the last word stays
    ``HOLD_AFTER`` seconds past its end, but not into the next line.

    Args:
        lines: Result of :func:`group_lines`.

    Returns:
        States in time order.
    """
    states: list[State] = []
    for index, line in enumerate(lines):
        next_start = lines[index + 1].words[0].start if index + 1 < len(lines) else float("inf")
        for pos, word in enumerate(line.words):
            start = word.start
            if pos + 1 < len(line.words):
                end = line.words[pos + 1].start
            else:
                end = min(word.end + HOLD_AFTER, next_start)
            if states:
                start = max(start, states[-1].start)
                if states[-1].end > start:
                    states[-1].end = start
            end = max(end, start + MIN_EVENT)
            states.append(State(start, end, index, pos))
    return [st for st in states if st.end - st.start >= MIN_EVENT / 2]


def shrink_size(text: str, size: float = FONT_SIZE, max_width: float = MAX_LINE_WIDTH) -> int | None:
    """Smaller font size for a lone line that is wider than ``max_width``, else None."""
    width = estimate_width(text, size)
    return int(size * max_width / width) if width > max_width else None


def build_events(lines: list[Line], size: float = FONT_SIZE, max_width: float = MAX_LINE_WIDTH) -> list[tuple[float, float, str]]:
    """Dialogue events of the ASS file: ``(start, end, ass text)``, one per caption state.

    Args:
        lines: Result of :func:`group_lines`.
        size: Font size, pixels.
        max_width: Widest line, pixels; a longer lone line is shrunk to fit.
    """
    events: list[tuple[float, float, str]] = []
    for st in build_states(lines):
        line = lines[st.line]
        small = shrink_size(line.text, size, max_width)
        shrink = f"{{\\fs{small}}}" if small else ""
        pieces = [
            f"{{\\1c{HIGHLIGHT}}}{w.text}{{\\1c{WHITE}}}" if i == st.pos else w.text
            for i, w in enumerate(line.words)
        ]
        events.append((st.start, st.end, shrink + " ".join(pieces)))
    return events


def build_ass(words: list[Word], font: str = FALLBACK_FONT, size: float = FONT_SIZE) -> str:
    """The whole ASS document for a list of words.

    Args:
        words: Words on the output clock (``words-cut.json``).
        font: Font family name.
        size: Font size, pixels.
    """
    margin_v = OUT_HEIGHT - SAFE_BOTTOM_Y
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {OUT_WIDTH}\n"
        f"PlayResY: {OUT_HEIGHT}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        f"Style: Default,{font},{int(size)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{OUTLINE},0,2,"
        f"{SIDE_MARGIN},{SIDE_MARGIN},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = build_events(group_lines(words, size=size), size)
    body = "".join(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{text}\n" for a, b, text in events)
    return header + body


def escape_filter_value(path: str) -> str:
    """Escape a path for use inside an ffmpeg filter argument (``\\``, ``'``, ``:``, ``,``, ``[``, ``]``)."""
    out = path.replace("\\", "\\\\")
    for ch in ("'", ":", ",", "[", "]", ";"):
        out = out.replace(ch, "\\" + ch)
    return out


def burn_command(video: Path, ass: Path, out: Path, fonts_dir: Path | None = None) -> list[str]:
    """ffmpeg arguments that burn an ASS file into a video.

    The ASS file is given by its bare name, so ffmpeg must run in its folder.
    """
    option = f"ass={escape_filter_value(ass.name)}"
    if fonts_dir:
        option += f":fontsdir={escape_filter_value(str(fonts_dir))}"
    return ["-y", "-nostats", "-i", str(video), "-vf", option, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "copy", "-movflags", "+faststart", str(out)]


def burn(video: Path, ass: Path, out: Path, fonts_dir: Path | None = None) -> None:
    """Burn captions in with ffmpeg/libass.

    Raises:
        MontageError: ffmpeg has no libass, or fails.
    """
    if not has_libass():
        raise MontageError(LIBASS_HELP)
    require_tool("ffmpeg")
    command = burn_command(video, ass.resolve(), out.resolve(), fonts_dir.resolve() if fonts_dir else None)
    done = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", *command], capture_output=True, text=True, check=False,
                          cwd=ass.resolve().parent)
    if done.returncode != 0:
        raise MontageError(f"caption burn-in failed (ffmpeg exit {done.returncode}): {done.stderr.strip()[-700:]}")


def choose_backend(requested: str, libass: bool) -> str:
    """Resolve ``--backend``: ``auto`` is ``ass`` when ffmpeg has libass, else ``png``.

    Args:
        requested: One of ``BACKENDS``.
        libass: Whether this ffmpeg has the ``ass`` filter.
    """
    if requested == "auto":
        return "ass" if libass else "png"
    return requested


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``captions.py``."""
    parser = argparse.ArgumentParser(description="Burned-in captions from words-cut.json: ASS/libass or PNG/overlay backend")
    parser.add_argument("video", type=Path, help="cut video from render_cut.py")
    parser.add_argument("words", type=Path, help="words-cut.json from render_cut.py")
    parser.add_argument("--backend", choices=BACKENDS, default="auto",
                        help="auto (default): ass when ffmpeg has libass, otherwise png; png needs Pillow")
    parser.add_argument("--out", type=Path, default=Path("reel-captioned.mp4"), help="output mp4 (default ./reel-captioned.mp4)")
    parser.add_argument("--ass", type=Path, default=Path("captions.ass"), help="ASS file to write (ass backend, default ./captions.ass)")
    parser.add_argument("--font", help="ass: font family name (default: first installed of the preferred list); "
                                       "png: path to a font file with Cyrillic (default: a bold system font)")
    parser.add_argument("--fonts-dir", type=Path, help="ass backend: folder with extra font files for libass")
    parser.add_argument("--png-dir", type=Path, help="png backend: keep the caption pictures in this folder")
    parser.add_argument("--size", type=int, default=FONT_SIZE, help=f"font size in pixels (default {FONT_SIZE})")
    parser.add_argument("--ass-only", action="store_true", help="write the ASS file and stop, do not burn")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        wf = load_words(args.words)
        if not wf.words:
            raise MontageError("words-cut.json holds no words; nothing to caption")
        if args.ass_only:
            backend = "ass"
        else:
            backend = choose_backend(args.backend, has_libass())
            if args.backend == "auto":
                print(f"captions: backend {backend} (auto: this ffmpeg {'has' if backend == 'ass' else 'has no'} libass)")
            if backend == "ass" and not has_libass():
                raise MontageError(LIBASS_HELP)
        if backend == "png":
            from captions_png import burn_png  # noqa: PLC0415 - Pillow is optional, loaded only for this backend

            count = burn_png(args.video, wf.words, args.out, args.font, args.size, args.png_dir)
            print(f"burned in ({count} caption pictures, png backend) -> {args.out}")
            return 0
        font = pick_font(args.font, installed_cyrillic_families())
        args.ass.parent.mkdir(parents=True, exist_ok=True)
        args.ass.write_text(build_ass(wf.words, font, args.size), encoding="utf-8")
        print(f"captions: font {font}, size {args.size} -> {args.ass}")
        if args.ass_only:
            return 0
        burn(args.video, args.ass, args.out, args.fonts_dir)
        print(f"burned in -> {args.out}")
    except MontageError as exc:
        LOG.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
