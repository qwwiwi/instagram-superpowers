"""PNG captions backend: the pure parts always run, the drawing and the overlay need Pillow (skipped cleanly without it)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import captions
import captions_png
from captions import CAPTION_CENTER_Y, FONT_SIZE, MAX_LINE_WIDTH, OUTLINE, SIDE_MARGIN, State, choose_backend, group_lines
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
    assert lines[1:4] == ["file 'blank.png'", "option framerate 30", "duration 0.500000"]
    # a gap of 21 frames sits between b and c
    assert "file 'blank.png'\noption framerate 30\nduration 0.700000" in text
    # after the last caption the blank picture runs to the end of the video, so the caption cannot stay on screen
    assert lines[-5:] == ["file 'blank.png'", "option framerate 30", "duration 0.600000", "file 'blank.png'", "option framerate 30"]
    # every picture carries the frame rate: the still-image demuxer would assume 25 fps otherwise
    assert text.count("file ") == text.count("option framerate 30")
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
    assert style.band_height == style.box_height + 2 * style.margin
    # the centre of the text box is the caption centre, the one constant the ASS backend uses as well
    assert abs(style.band_top + style.band_height / 2 - CAPTION_CENTER_Y * 1920) <= 1


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
        assert (style.band_top + bottom) / 1920 <= 0.82
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


# --- position, size, look (a picture is measured, not eyeballed) -----------------------------------------------------------


def lines_for_measuring() -> list[captions.Line]:
    """Lines with and without descenders and tall letters."""
    texts = ["Есть обычное", "Ужу руку дружу", "ШЩ код", "Я три месяца"]
    return [group_lines([Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(t.split())])[0] for t in texts]


def ink_rows(image: object, threshold: int) -> tuple[int, int]:
    """First and last row of the picture that hold a pixel with alpha above ``threshold``."""
    alpha = image.getchannel("A").point(lambda v: 255 if v > threshold else 0)  # type: ignore[attr-defined]
    left, top, right, bottom = alpha.getbbox()
    return top, bottom


@NEED_PILLOW
def test_caption_sits_in_the_lower_third_below_the_face_and_above_the_interface() -> None:
    style = system_style()
    for line in lines_for_measuring():
        image = captions_png.render_state(line, 0, style)
        solid_top, solid_bottom = ink_rows(image, 128)  # letters and outline
        faint_top, faint_bottom = ink_rows(image, 8)  # with the soft shadow
        centre = (style.band_top + (solid_top + solid_bottom) / 2) / 1920
        assert 0.72 <= centre <= 0.80, (line.text, centre)
        assert (style.band_top + faint_bottom) / 1920 <= 0.82, (line.text, "shadow reaches below 82 %")
        assert (style.band_top + faint_top) / 1920 >= 0.70, (line.text, "too high: that is the face")
    # the block centre itself, the number the constant promises
    assert 0.74 <= (style.band_top + style.band_height / 2) / 1920 <= 0.78


@NEED_PILLOW
def test_y_moves_the_picture_and_the_default_comes_from_one_constant() -> None:
    path, index = captions_png.find_font_file()
    default = captions_png.make_style(path, index)
    lower = captions_png.make_style(path, index, center_y=0.84)
    assert lower.band_top > default.band_top
    assert abs(lower.band_top + lower.band_height / 2 - 0.84 * 1920) <= 1
    assert abs(default.band_top + default.band_height / 2 - CAPTION_CENTER_Y * 1920) <= 1


@NEED_PILLOW
def test_everything_scales_with_the_frame_width() -> None:
    path, index = captions_png.find_font_file()
    big = captions_png.make_style(path, index)
    small = captions_png.make_style(path, index, frame_width=540, frame_height=960)
    line = lines_for_measuring()[0]
    image_big = captions_png.render_state(line, 0, big)
    image_small = captions_png.render_state(line, 0, small)
    assert image_big.size[0] == 1080 and image_small.size[0] == 540
    big_box, small_box = image_big.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox(), \
        image_small.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()
    assert (small_box[2] - small_box[0]) / (big_box[2] - big_box[0]) == pytest.approx(0.5, abs=0.04)
    assert (small_box[3] - small_box[1]) / (big_box[3] - big_box[1]) == pytest.approx(0.5, abs=0.06)
    # the same fraction of the frame height, and nothing sticks out of the narrow frame
    assert (small.band_top + small.band_height / 2) / 960 == pytest.approx((big.band_top + big.band_height / 2) / 1920, abs=0.002)
    assert small_box[0] >= 0 and small_box[2] <= 540
    assert small.outline == round(OUTLINE / 2) or small.outline == OUTLINE // 2 + 1
    assert small.max_width == pytest.approx(MAX_LINE_WIDTH / 2)


@NEED_PILLOW
def test_outline_is_thick_and_a_soft_shadow_sits_under_the_text() -> None:
    style = system_style()
    line = group_lines([Word("П", 0, 0.5)])[0]
    image = captions_png.render_state(line, 0, style)
    pixels = image.load()
    mid = style.band_height // 2
    row = [pixels[x, mid] for x in range(image.width)]
    first_opaque = next(x for x, p in enumerate(row) if p[3] >= 250)
    first_fill = next(x for x, p in enumerate(row) if p[3] >= 250 and p[:3] == captions_png.HIGHLIGHT_RGB)
    assert OUTLINE - 1 <= first_fill - first_opaque <= OUTLINE + 2  # black run before the letter = the outline
    assert sum(1 for p in row[first_opaque:first_fill] if p[3] >= 250 and max(p[:3]) <= 40) >= OUTLINE - 2
    # soft shadow: partly transparent dark pixels below the outline, darkest near the letters and fading out
    below = [pixels[x, y] for y in range(style.band_height) for x in range(image.width)
             if 0 < pixels[x, y][3] < 200 and pixels[x, y][:3] == (0, 0, 0)]
    assert len(below) > 300
    assert max(p[3] for p in below) <= 200


@NEED_PILLOW
def test_cyrillic_ya_is_drawn_the_right_way_round() -> None:
    from PIL import Image, ImageDraw

    style = system_style()
    line = group_lines([Word("Я", 0, 0.5)])[0]
    image = captions_png.render_state(line, 0, style)
    r, _, _, a = image.split()
    # the letter itself: opaque and light (the outline and the shadow are black)
    amber = Image.eval(r, lambda v: 255 if v > 128 else 0)
    amber = Image.composite(amber, Image.new("L", image.size, 0), a.point(lambda v: 255 if v > 128 else 0))
    shown = amber.crop(amber.getbbox())

    def glyph(text: str, mirror: bool) -> object:
        font = captions_png.open_font(style.path, style.em, style.index)
        canvas = Image.new("L", (style.em * 2, style.em * 2), 0)
        ImageDraw.Draw(canvas).text((style.em // 2, style.em // 2), text, font=font, fill=255)
        crop = canvas.crop(canvas.getbbox())
        return crop.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if mirror else crop

    def overlap(a: object, b: object) -> float:
        """Intersection over union of two glyph masks, aligned at their top-left corners."""
        size = (max(a.size[0], b.size[0]), max(a.size[1], b.size[1]))  # type: ignore[attr-defined]
        pair = []
        for m in (a, b):
            canvas = Image.new("L", size, 0)
            canvas.paste(m, (0, 0))  # type: ignore[arg-type]
            pair.append(canvas.tobytes())
        both = sum(1 for x, y in zip(*pair) if x > 128 and y > 128)
        either = sum(1 for x, y in zip(*pair) if x > 128 or y > 128)
        return both / either

    reference = glyph("Я", mirror=False)
    assert abs(reference.size[0] - shown.size[0]) <= 3 and abs(reference.size[1] - shown.size[1]) <= 3
    assert overlap(shown, reference) > 0.85  # it is the font's «Я», not its mirror image
    assert overlap(shown, glyph("Я", mirror=True)) < 0.85  # the mirrored shape would look different
    # and «Я» really is the mirror image of the Latin R in this font, as the letter should be
    assert overlap(glyph("Я", mirror=False), glyph("R", mirror=True)) > 0.75


# --- the whole overlay path on real frames --------------------------------------------------------------------------------------


def plain_clip(path: Path, width: int, height: int, seconds: float) -> Path:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=0x303030:s={width}x{height}:r=30:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-preset", "veryfast", str(path)], check=True)
    return path


def decode_band(video: Path, top: int, height: int, width: int) -> list[bytes]:
    """RGB bytes of the caption band of every frame."""
    done = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(video), "-vf", f"crop={width}:{height}:0:{top}",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    size = width * height * 3
    return [done.stdout[i:i + size] for i in range(0, len(done.stdout) - size + 1, size)]


def amber_clusters(frame: bytes, width: int, height: int, gap: int) -> list[tuple[int, int]]:
    """Column ranges of amber pixels, runs closer than ``gap`` columns joined."""
    columns = [False] * width
    for y in range(height):
        row = frame[y * width * 3:(y + 1) * width * 3]
        for x in range(width):
            r, g, b = row[3 * x], row[3 * x + 1], row[3 * x + 2]
            if r > 200 and 150 < g < 245 and b < 110:
                columns[x] = True
    ranges: list[list[int]] = []
    for x, on in enumerate(columns):
        if not on:
            continue
        if ranges and x - ranges[-1][1] <= gap:
            ranges[-1][1] = x
        else:
            ranges.append([x, x])
    return [(a, b) for a, b in ranges]


@NEED_PILLOW
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_every_one_frame_highlight_state_reaches_the_output_exactly_once(tmp_path: Path) -> None:
    system_style()
    width, height = 540, 960
    clip = plain_clip(tmp_path / "plain.mp4", width, height, 1.4)
    names = ["один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь"]
    first = 10  # frame of the first word
    words = [Word(n, (first + i) / 30, (first + i + 1) / 30) for i, n in enumerate(names)]
    words_file = tmp_path / "w.json"
    save_words(words_file, WordsFile(words=words, duration=1.4, language="ru"))
    out = tmp_path / "cap.mp4"
    assert captions.main([str(clip), str(words_file), "--backend", "png", "--out", str(out)]) == 0
    path, index = captions_png.find_font_file()
    style = captions_png.make_style(path, index, frame_width=width, frame_height=height)
    frames = decode_band(out, style.band_top, style.band_height, width)
    assert len(frames) >= 40
    gap = max(8, round(0.2 * style.em))  # wider than the gap between letters, narrower than a space plus outlines
    seen: list[tuple[int, int, int]] = []  # (frame, clusters, centre of the amber word)
    for i, frame in enumerate(frames):
        clusters = amber_clusters(frame, width, style.band_height, gap)
        assert len(clusters) <= 1, f"frame {i}: more than one highlighted word {clusters}"
        if clusters:
            seen.append((i, len(clusters), (clusters[0][0] + clusters[0][1]) // 2))
    assert seen[0][0] == first  # the first state appears on exactly its frame
    runs: list[list[int]] = []
    for frame_no, _, centre in seen:
        if runs and abs(runs[-1][0] - centre) <= 3 and runs[-1][2] == frame_no - 1:
            runs[-1][2] = frame_no
        else:
            runs.append([centre, frame_no, frame_no])
    # eight states, in order, each on its own frame; the last one is held for the hold time
    assert len(runs) == 8, runs
    assert [r[2] - r[1] + 1 for r in runs[:7]] == [1] * 7, runs
    assert [r[1] for r in runs] == list(range(first, first + 8))
    # the highlight walks to the right inside a line and jumps back to the left when the next line starts
    states = captions.build_states(group_lines(words))
    for (a, centre_a), (b, centre_b) in zip(zip(states, [r[0] for r in runs]), zip(states[1:], [r[0] for r in runs[1:]])):
        assert (centre_b > centre_a) if a.line == b.line else (centre_b < centre_a), (a, b, runs)


@NEED_PILLOW
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_the_old_25_fps_concat_really_loses_states(tmp_path: Path) -> None:
    # Guards the test above: the same pictures without `option framerate` give a skipped and a doubled picture.
    colours = ["red", "green", "blue", "yellow", "white", "magenta"]
    for c in colours:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={c}:s=32x32:d=1", "-frames:v", "1", str(tmp_path / f"{c}.png")], check=True)

    def sequence(rate_line: str) -> list[tuple[int, ...]]:
        text = "ffconcat version 1.0\n" + "".join(f"file '{c}.png'\n{rate_line}duration 0.033333\n" for c in colours) + f"file '{colours[-1]}.png'\n"
        (tmp_path / "l.ffconcat").write_text(text, encoding="utf-8")
        out = tmp_path / "o.rgb"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=32x32:r=30:d=0.3", "-f", "concat", "-safe", "0",
                        "-i", str(tmp_path / "l.ffconcat"), "-filter_complex",
                        "[1:v]fps=30,format=rgba[c];[0:v][c]overlay=eof_action=pass:format=auto,format=rgb24[o]", "-map", "[o]",
                        "-f", "rawvideo", "-r", "30", str(out)], check=True)
        data = out.read_bytes()
        frame = 32 * 32 * 3
        return [tuple(data[i * frame + 16 * 96 + 48: i * frame + 16 * 96 + 51]) for i in range(len(data) // frame)][:6]

    assert len(set(sequence(""))) < 6  # 25 fps input: a picture is lost
    assert len(set(sequence("option framerate 30\n"))) == 6


@NEED_PILLOW
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_png_backend_with_awkward_file_names_and_a_relative_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    system_style()
    folder = tmp_path / "it's [x]; a,b=c"
    folder.mkdir()
    clip = plain_clip(folder / "my reel: take 'one' (1).mp4", 270, 480, 1.0)
    words_file = folder / "слова, ещё.json"
    save_words(words_file, WordsFile(words=[Word("Привет", 0.2, 0.5), Word("мир", 0.55, 0.9)], duration=1.0, language="ru"))
    monkeypatch.chdir(folder)  # the video and the words are given as relative paths
    pictures = Path("pics: 'x'")
    code = captions.main([clip.name, words_file.name, "--backend", "png", "--out", "out dir/cap's:1.mp4", "--png-dir", str(pictures)])
    assert code == 0
    out = folder / "out dir" / "cap's:1.mp4"
    info = probe(out)
    assert (info.width, info.height) == (270, 480)  # the caption was laid over a small frame without cropping it
    assert (folder / pictures / "captions.ffconcat").is_file()
