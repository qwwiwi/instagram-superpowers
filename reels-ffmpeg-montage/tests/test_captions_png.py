"""PNG captions backend: the pure parts always run, the drawing and the overlay need Pillow (skipped cleanly without it)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import captions
import captions_png
from captions import FONT_SIZE, MAX_LINE_WIDTH, OUTLINE, SAFE_BOTTOM_Y, SIDE_MARGIN, State, choose_backend, group_lines
from common import MontageError, Word, WordsFile, probe, save_words

from conftest import CLIP_DURATION, HAVE_FFMPEG, fake_words

try:
    import PIL  # noqa: F401

    HAVE_PILLOW = True
except ImportError:
    HAVE_PILLOW = False

NEED_PILLOW = pytest.mark.skipif(not HAVE_PILLOW, reason="Pillow is not installed (only the png captions backend needs it)")


# --- no Pillow needed -------------------------------------------------------------------------------------------


def test_auto_backend_follows_libass() -> None:
    assert choose_backend("auto", libass=True) == "ass"
    assert choose_backend("auto", libass=False) == "png"
    assert choose_backend("png", libass=True) == "png"
    assert choose_backend("ass", libass=False) == "ass"


def test_frames_are_whole_and_never_overlap() -> None:
    states = [State(0.50, 0.90, 0, 0), State(0.90, 1.31, 0, 1), State(1.312, 1.314, 1, 0), State(2.0, 2.4, 1, 1)]
    spans = captions_png.frame_spans(states, 30)
    # the 2 ms state is shorter than a frame and goes; the others are on the 1/30 s grid
    assert [i for i, _, _ in spans] == [0, 1, 3]
    assert spans[0][1:] == (15, 27)
    assert all(a[2] <= b[1] for a, b in zip(spans, spans[1:]))


def test_concat_list_has_blank_gaps_and_repeats_the_last_file() -> None:
    spans = [(0, 15, 27), (1, 27, 39), (2, 60, 72)]
    text = captions_png.concat_list(spans, {0: "a.png", 1: "b.png", 2: "c.png"}, "blank.png", 30, total_frames=90)
    lines = text.splitlines()
    assert lines[0] == "ffconcat version 1.0"
    assert lines[1:3] == ["file 'blank.png'", "duration 0.500000"]
    # a gap of 21 frames sits between b and c
    assert "file 'blank.png'\nduration 0.700000" in text
    # after the last caption the blank picture runs to the end of the video, so the caption cannot stay on screen
    assert lines[-3:] == ["file 'blank.png'", "duration 0.600000", "file 'blank.png'"]
    # total length = 90 frames
    total = sum(float(x.split()[1]) for x in lines if x.startswith("duration"))
    assert total == pytest.approx(90 / 30, abs=1e-5)


def test_command_is_short_and_uses_only_the_overlay_filter() -> None:
    command = captions_png.overlay_command(Path("in.mp4"), Path("captions.ffconcat"), Path("out.mp4"), 1150)
    graph = command[command.index("-filter_complex") + 1]
    assert "overlay=0:1150" in graph and "eof_action=pass" in graph
    assert "ass" not in graph.replace("pass", "") and "subtitles" not in graph and "drawtext" not in graph
    assert len(" ".join(command)) < 600  # fixed length, however many words there are


def test_missing_pillow_prints_the_install_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(MontageError) as info:
        captions_png.import_pillow()
    assert "python3 -m pip install pillow" in str(info.value)
    words_file = tmp_path / "w.json"
    save_words(words_file, WordsFile(words=fake_words(), duration=CLIP_DURATION))
    code = captions.main([str(tmp_path / "v.mp4"), str(words_file), "--backend", "png", "--out", str(tmp_path / "o.mp4")])
    assert code == 2


def test_libass_message_does_not_claim_homebrew_has_it() -> None:
    assert "standard Homebrew" in captions.LIBASS_HELP and "has no libass" in captions.LIBASS_HELP
    assert "--backend png" in captions.LIBASS_HELP


# --- fonts and drawing ---------------------------------------------------------------------------------------------


@NEED_PILLOW
def test_no_cyrillic_font_gives_a_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(captions_png, "font_dirs", lambda: [])
    monkeypatch.setattr(captions_png.shutil, "which", lambda name: None)
    with pytest.raises(MontageError, match="--font"):
        captions_png.find_font_file()


@NEED_PILLOW
def test_unknown_font_file_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(captions_png, "font_dirs", lambda: [])
    with pytest.raises(MontageError, match="not found"):
        captions_png.find_font_file(str(tmp_path / "nope.ttf"))
    broken = tmp_path / "broken.ttf"
    broken.write_bytes(b"not a font")
    with pytest.raises(MontageError, match="Cyrillic|cannot be opened"):
        captions_png.find_font_file(str(broken))


def system_style() -> captions_png.Style:
    try:
        path, index = captions_png.find_font_file()
    except MontageError:
        pytest.skip("no system font with Cyrillic on this machine")
    return captions_png.make_style(path, index)


@NEED_PILLOW
def test_text_box_has_the_ass_font_size() -> None:
    style = system_style()
    assert abs(style.box_height - FONT_SIZE) <= 3
    assert style.band_height == style.box_height + 2 * captions_png.BAND_MARGIN
    # the box ends at the safe line, the outline hangs below it inside the band
    assert style.band_top + style.band_height == SAFE_BOTTOM_Y + captions_png.BAND_MARGIN


@NEED_PILLOW
def test_state_picture_highlights_one_word_and_stays_in_the_safe_area() -> None:
    style = system_style()
    line = group_lines([Word("Привет", 0, 0.4), Word("мир", 0.45, 0.8), Word("сегодня", 0.85, 1.3)])[0]
    amber, white = [], []
    for pos in range(3):
        image = captions_png.render_state(line, pos, style)
        assert image.size == (1080, style.band_height) and image.mode == "RGBA"
        pixels = image.load()
        assert image.getchannel("A").getbbox() is not None
        count_amber = count_white = 0
        for y in range(image.height):
            for x in range(image.width):
                r, g, b, a = pixels[x, y]
                if a == 255 and (r, g, b) == (255, 215, 0):
                    count_amber += 1
                elif a == 255 and (r, g, b) == (255, 255, 255):
                    count_white += 1
        amber.append(count_amber)
        white.append(count_white)
        left, _, right, bottom = image.getchannel("A").getbbox()
        assert left >= SIDE_MARGIN - OUTLINE - 2 and right <= 1080 - SIDE_MARGIN + OUTLINE + 2
        assert style.band_top + bottom <= SAFE_BOTTOM_Y + captions_png.BAND_MARGIN
    assert all(n > 200 for n in amber) and all(n > 200 for n in white)
    # the three words have different widths, so the amber share differs between states
    assert len(set(amber)) > 1


@NEED_PILLOW
def test_overwide_line_is_drawn_smaller_not_clipped() -> None:
    style = system_style()
    line = group_lines([Word("Достопримечательность" * 2, 0, 1)])[0]
    image = captions_png.render_state(line, 0, style)
    left, _, right, _ = image.getchannel("A").getbbox()
    assert right - left <= MAX_LINE_WIDTH + 2 * OUTLINE + 4


# --- with ffmpeg: the whole overlay path ------------------------------------------------------------------------------------


def frame_at(video: Path, seconds: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(video),
                    "-frames:v", "1", str(out)], check=True)
    return out


@NEED_PILLOW
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_overlay_burns_captions_only_where_words_are(synthetic_clip: Path, tmp_path: Path) -> None:
    from PIL import Image, ImageChops

    system_style()
    words = [Word("Первое", 1.0, 1.4), Word("слово", 1.45, 1.9), Word("второе", 3.0, 3.4), Word("слово", 3.45, 3.9)]
    words_file = tmp_path / "w.json"
    save_words(words_file, WordsFile(words=words, duration=5.0, language="ru"))
    out = tmp_path / "cap.mp4"
    code = captions.main([str(synthetic_clip), str(words_file), "--backend", "png", "--out", str(out), "--png-dir", str(tmp_path / "pics")])
    assert code == 0
    assert (tmp_path / "pics" / "captions.ffconcat").is_file()
    info, original = probe(out), probe(synthetic_clip)
    assert (info.width, info.height) == (1080, 1920) and info.has_audio and info.audio_codec == "aac"
    assert info.duration == pytest.approx(original.duration, abs=0.12)
    style = system_style()
    box = (0, style.band_top, 1080, style.band_top + style.band_height)

    def band_difference(seconds: float) -> int:
        a = Image.open(frame_at(out, seconds, tmp_path / f"c{seconds}.png")).convert("RGB").crop(box)
        b = Image.open(frame_at(synthetic_clip, seconds, tmp_path / f"o{seconds}.png")).convert("RGB").crop(box)
        diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 60 else 0)
        return sum(1 for v in diff.tobytes() if v)

    assert band_difference(1.2) > 1500   # first word on screen
    assert band_difference(3.6) > 1500   # second line on screen
    assert band_difference(0.5) < 50     # before the first word
    assert band_difference(2.5) < 50     # between the two lines
    assert band_difference(4.6) < 50     # after the last word
    # outside the band the frame is untouched
    top = (0, 0, 1080, style.band_top - 5)
    a = Image.open(tmp_path / "c1.2.png").convert("RGB").crop(top)
    b = Image.open(tmp_path / "o1.2.png").convert("RGB").crop(top)
    assert sum(1 for v in ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 60 else 0).tobytes() if v) < 50
