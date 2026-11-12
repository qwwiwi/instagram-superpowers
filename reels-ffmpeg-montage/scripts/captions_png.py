"""PNG captions backend: needs only the ``overlay`` filter of ffmpeg, no libass.

Every caption state (a line with one word highlighted, see ``captions.build_states``)
is drawn with Pillow to a transparent PNG of the same size: a band as wide as the
frame, positioned so that the text box ends at ``SAFE_BOTTOM_Y`` exactly like the ASS
style does. The pictures are listed in a concat file with one duration per picture
(and a blank picture for the gaps, all durations are whole frames) and that
sequence is laid over the video by a single ``overlay`` filter.

Why one concat input and not one ``overlay=enable='between(t,a,b)'`` per state: a
60-90 s reel has 150+ states, so that graph has 150+ inputs and 150+ chained
overlays that each touch every frame, and the command line grows with the word
count (long, slow, and near the OS argument limit on Windows). The concat list
lives in a file, the command line has a fixed length, and ffmpeg blends one picture
per frame.

Look: same style as the ASS backend. White text, the current word amber, black
outline ``OUTLINE`` px, bold. The ASS font size is the height of the whole text box
(ascent + descent); Pillow wants the em size, so the em size is chosen to give a box
of ``FONT_SIZE`` px, as libass does. The look is the same by construction; it was
not compared pixel by pixel with libass.

Pillow is an optional dependency, imported only here and only when this backend runs.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from captions import (
    FONT_SIZE,
    MAX_LINE_WIDTH,
    OUTLINE,
    SAFE_BOTTOM_Y,
    Line,
    State,
    build_states,
    group_lines,
)
from common import OUT_FPS, OUT_WIDTH, MontageError, Word, probe, require_tool

LOG = logging.getLogger("reels.captions.png")

PILLOW_HELP = (
    "The png captions backend needs Pillow. Install it with: python3 -m pip install pillow  "
    "(if pip answers `externally-managed-environment`, make a virtual environment first: "
    "python3 -m venv .venv && .venv/bin/python -m pip install pillow, and run the scripts with .venv/bin/python)."
)
FONT_HELP = (
    "No font with Cyrillic letters was found for the png backend. Pass a font file: --font /path/to/Font-Bold.ttf "
    "(any bold TrueType/OpenType font with Cyrillic, for example DejaVu Sans Bold, Liberation Sans Bold or Noto Sans Bold)."
)

HIGHLIGHT_RGB = (255, 215, 0)  # amber, the same colour as the ASS style (&H00D7FF& in BGR)
WHITE_RGB = (255, 255, 255)
BLACK = (0, 0, 0, 255)
# Free pixels around the text box inside a band, so the outline is never clipped.
BAND_MARGIN = OUTLINE + 2

# Bold fonts with Cyrillic, best first. Files are looked for in FONT_DIRS.
FONT_FILES = (
    "Montserrat-Bold.ttf", "Montserrat-ExtraBold.ttf", "Inter-Bold.ttf", "Inter-Bold.otf",
    "Arial Bold.ttf", "arialbd.ttf", "Tahoma Bold.ttf", "tahomabd.ttf", "Verdana Bold.ttf", "verdanab.ttf",
    "Trebuchet MS Bold.ttf", "trebucbd.ttf", "segoeuib.ttf", "calibrib.ttf",
    "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "NotoSans-Bold.ttf", "Roboto-Bold.ttf", "FreeSansBold.ttf",
    "Arial Unicode.ttf", "arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf",
)


def font_dirs() -> list[Path]:
    """Folders searched for fonts: macOS, Windows, Linux and the user's own."""
    home = Path.home()
    windir = Path(os.environ.get("WINDIR", "C:/Windows"))
    local = os.environ.get("LOCALAPPDATA")
    candidates = [
        home / "Library/Fonts", home / ".fonts", home / ".local/share/fonts",
        Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"),
        Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), windir / "Fonts",
    ]
    if local:
        candidates.append(Path(local) / "Microsoft/Windows/Fonts")
    return [d for d in candidates if d.is_dir()]


def import_pillow() -> Any:
    """Import ``PIL.ImageFont`` / ``PIL.ImageDraw`` / ``PIL.Image`` or fail with the install command.

    Raises:
        MontageError: Pillow is not installed.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415 - optional dependency, imported on use
    except ImportError as exc:
        raise MontageError(PILLOW_HELP) from exc
    return Image, ImageDraw, ImageFont


def has_cyrillic(font: Any) -> bool:
    """True when the font draws real letters for ``Ж`` and ``я`` (not its "missing glyph" box)."""
    def glyph(ch: str) -> tuple[Any, bytes]:
        mask = font.getmask(ch)
        return mask.size, bytes(mask)

    missing = glyph("\ue000")
    return glyph("Ж") != missing and glyph("я") != missing


def open_font(path: Path, size: int, index: int = 0) -> Any:
    """Open a font file with Pillow.

    Raises:
        MontageError: The file is not a usable font.
    """
    _, _, image_font = import_pillow()
    try:
        return image_font.truetype(str(path), size, index=index)
    except OSError as exc:
        raise MontageError(f"Cannot open the font {path}: {exc}") from exc


def index_fonts(dirs: list[Path]) -> dict[str, Path]:
    """Font files of the folders by lower-case file name; the earlier folder wins."""
    found: dict[str, Path] = {}
    for folder in dirs:
        try:
            for file in folder.rglob("*"):
                if file.suffix.lower() in (".ttf", ".otf") and file.name.lower() not in found:
                    found[file.name.lower()] = file
        except OSError as exc:
            LOG.debug("cannot list %s: %s", folder, exc)
    return found


def usable(path: Path) -> bool:
    """True when the file opens as a font and draws Cyrillic."""
    try:
        return has_cyrillic(open_font(path, 40))
    except MontageError:
        return False


def find_font_file(requested: str | None = None) -> tuple[Path, int]:
    """Choose the font file for the png backend.

    Args:
        requested: ``--font``: a path to a font file, or a file name / family name to look for in the system folders.

    Returns:
        ``(file, collection index)``.

    Raises:
        MontageError: The requested font is not found or has no Cyrillic, or no system font with Cyrillic exists.
    """
    import_pillow()
    if requested:
        path = Path(requested).expanduser()
        if not path.is_file():
            needle = requested.lower().replace(" ", "")
            matches = sorted(
                (f for name, f in index_fonts(font_dirs()).items() if needle in name.replace(" ", "").replace("-", "")),
                key=lambda f: ("bold" not in f.name.lower(), len(f.name)),
            )
            if not matches:
                raise MontageError(f"Font {requested!r} not found. For the png backend give --font as the path to a font file (.ttf/.otf).")
            path = matches[0]
        if not usable(path):
            raise MontageError(f"The font {path} cannot be opened or has no Cyrillic letters. Pick another file with --font.")
        return path, 0
    available = index_fonts(font_dirs())
    for name in FONT_FILES:
        path = available.get(name.lower())
        if path and usable(path):
            LOG.debug("font %s", path)
            return path, 0
    fc = shutil.which("fc-match")
    if fc:
        done = subprocess.run([fc, "-f", "%{file}", "sans-serif:bold:lang=ru"], capture_output=True, text=True, check=False)
        path = Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None
        if path and path.suffix.lower() in (".ttf", ".otf") and path.is_file() and usable(path):
            return path, 0
    raise MontageError(FONT_HELP)


@dataclass
class Style:
    """Font metrics shared by every picture."""

    path: Path
    index: int
    em: int  # em size in px that gives a text box of FONT_SIZE
    box_height: int  # ascent + descent at that size
    band_height: int
    band_top: int  # y of the band's top edge in the frame


def make_style(path: Path, index: int = 0, size: int = FONT_SIZE) -> Style:
    """Em size so that the text box (ascent + descent) is ``size`` px, and the band geometry that follows."""
    probe = open_font(path, 100, index)
    ascent, descent = probe.getmetrics()
    em = max(8, round(size * 100 / (ascent + descent)))
    box = sum(open_font(path, em, index).getmetrics())
    band = box + 2 * BAND_MARGIN
    return Style(path, index, em, box, band, SAFE_BOTTOM_Y + BAND_MARGIN - band)


def render_state(line: Line, pos: int, style: Style, max_width: float = MAX_LINE_WIDTH) -> Any:
    """Draw one caption state: the line, word ``pos`` amber, the rest white, all with a black outline.

    Args:
        line: The caption line.
        pos: Index of the highlighted word.
        style: Font and band geometry.
        max_width: Widest text, pixels; a wider line is drawn smaller.

    Returns:
        A transparent RGBA image, ``OUT_WIDTH`` wide, ``style.band_height`` high.
    """
    image_mod, draw_mod, _ = import_pillow()
    em = style.em
    font = open_font(style.path, em, style.index)
    text = line.text
    width = font.getlength(text)
    if width > max_width:
        em = max(8, int(em * max_width / width))
        font = open_font(style.path, em, style.index)
        width = font.getlength(text)
    _, descent = font.getmetrics()
    image = image_mod.new("RGBA", (OUT_WIDTH, style.band_height), (0, 0, 0, 0))
    draw = draw_mod.Draw(image)
    left = (OUT_WIDTH - width) / 2
    baseline = style.band_height - BAND_MARGIN - descent
    xs = [left + font.getlength(" ".join(w.text for w in line.words[:i]) + (" " if i else "")) for i in range(len(line.words))]
    # Outlines first, then the fills, so a later word's outline never covers an earlier word's letters.
    for word, x in zip(line.words, xs):
        draw.text((x, baseline), word.text, font=font, fill=BLACK, anchor="ls", stroke_width=OUTLINE, stroke_fill=BLACK)
    for i, (word, x) in enumerate(zip(line.words, xs)):
        colour = HIGHLIGHT_RGB if i == pos else WHITE_RGB
        draw.text((x, baseline), word.text, font=font, fill=(*colour, 255), anchor="ls")
    return image


def frame_spans(states: list[State], fps: float = OUT_FPS) -> list[tuple[int, int, int]]:
    """Put the states on the frame grid.

    Returns:
        ``(index into states, first frame, end frame)``; spans are in order, never overlap, and have at least one frame.
    """
    out: list[tuple[int, int, int]] = []
    cursor = 0
    for index, st in enumerate(states):
        first = max(round(st.start * fps), cursor)
        last = round(st.end * fps)
        if last <= first:
            continue
        out.append((index, first, last))
        cursor = last
    return out


def concat_list(spans: list[tuple[int, int, int]], names: dict[int, str], blank: str, fps: float = OUT_FPS,
                total_frames: int = 0) -> str:
    """Text of the ffmpeg concat file: a picture per span, the blank picture in the gaps and after the last caption.

    Args:
        spans: Result of :func:`frame_spans`.
        names: Picture file name per state index.
        blank: Name of the transparent picture.
        fps: Frame rate of the grid.
        total_frames: Length of the video in frames; the sequence is padded with the blank picture up to it, because a
            sequence that simply ends would hold its last caption on screen.

    The last file is listed once more without a duration: the concat demuxer ignores the duration of the final entry.
    """
    lines = ["ffconcat version 1.0"]
    cursor = 0
    for index, first, end in spans:
        if first > cursor:
            lines += [f"file '{blank}'", f"duration {(first - cursor) / fps:.6f}"]
        lines += [f"file '{names[index]}'", f"duration {(end - first) / fps:.6f}"]
        cursor = end
    if total_frames > cursor:
        lines += [f"file '{blank}'", f"duration {(total_frames - cursor) / fps:.6f}"]
    lines.append(f"file '{blank}'")
    return "\n".join(lines) + "\n"


def overlay_command(video: Path, concat: Path, out: Path, top: int, fps: float = OUT_FPS) -> list[str]:
    """ffmpeg arguments that lay the picture sequence over the video with the ``overlay`` filter."""
    graph = (
        f"[1:v]fps={fps:g},format=rgba[cap];"
        f"[0:v][cap]overlay=0:{top}:eof_action=pass:format=auto,format=yuv420p[v]"
    )
    return ["-y", "-nostats", "-i", str(video), "-f", "concat", "-safe", "0", "-i", str(concat),
            "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", f"{fps:g}",
            "-c:a", "copy", "-movflags", "+faststart", str(out)]


def render_pictures(words: list[Word], style: Style, folder: Path, total_frames: int = 0,
                    fps: float = OUT_FPS) -> tuple[Path, int]:
    """Draw every picture and the concat list into ``folder``.

    Args:
        words: Words on the output clock.
        style: Font and band geometry.
        folder: Where the pictures and ``captions.ffconcat`` go.
        total_frames: Length of the video in frames (see :func:`concat_list`).
        fps: Frame rate of the grid.

    Returns:
        ``(concat file, number of caption pictures)``.

    Raises:
        MontageError: There is nothing to caption.
    """
    lines = group_lines(words, size=FONT_SIZE)
    states = build_states(lines)
    spans = frame_spans(states, fps)
    if not spans:
        raise MontageError("No caption state is at least one frame long; nothing to draw")
    folder.mkdir(parents=True, exist_ok=True)
    image_mod, _, _ = import_pillow()
    blank = "blank.png"
    image_mod.new("RGBA", (OUT_WIDTH, style.band_height), (0, 0, 0, 0)).save(folder / blank)
    names: dict[int, str] = {}
    for index, _, _ in spans:
        st = states[index]
        names[index] = f"s{index:05d}.png"
        render_state(lines[st.line], st.pos, style).save(folder / names[index], compress_level=1)
    concat = folder / "captions.ffconcat"
    concat.write_text(concat_list(spans, names, blank, fps, total_frames), encoding="utf-8")
    return concat, len(spans)


def burn_png(video: Path, words: list[Word], out: Path, font: str | None = None, size: int = FONT_SIZE,
             keep_dir: Path | None = None) -> int:
    """Burn captions into a video with the PNG + overlay backend.

    Args:
        video: Cut video.
        words: Words on the output clock.
        out: Output mp4.
        font: ``--font`` value (a font file, or a name to search for).
        size: Text box height in pixels (the ASS ``Fontsize``).
        keep_dir: Keep the pictures in this folder; by default a temporary folder is used and removed.

    Returns:
        Number of caption pictures drawn.

    Raises:
        MontageError: Pillow, a Cyrillic font, ffmpeg or the overlay filter is missing, or ffmpeg fails.
    """
    import_pillow()
    path, index = find_font_file(font)
    style = make_style(path, index, size)
    require_tool("ffmpeg")
    total_frames = math.ceil(probe(video).duration * OUT_FPS) + 1
    work = keep_dir or Path(tempfile.mkdtemp(prefix="reels-captions-"))
    try:
        concat, count = render_pictures(words, style, work, total_frames)
        command = overlay_command(video.resolve(), Path(concat.name), out.resolve(), style.band_top)
        done = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", *command], capture_output=True, text=True,
                              check=False, cwd=work)
        if done.returncode != 0:
            raise MontageError(f"caption overlay failed (ffmpeg exit {done.returncode}): {done.stderr.strip()[-700:]}")
    finally:
        if keep_dir is None:
            shutil.rmtree(work, ignore_errors=True)
    LOG.info("font %s, %d caption pictures", path.name, count)
    return count
