"""ASS generation: line width, timing, highlight, font choice, burn command."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import captions
from captions import (
    CAPTION_CENTER_Y,
    FONT_SIZE,
    MAX_LINE_WIDTH,
    MAX_WORDS,
    OUTLINE,
    ass_time,
    box_bottom_y,
    build_ass,
    build_events,
    build_states,
    burn_command,
    check_center_y,
    escape_filter_value,
    escape_filtergraph,
    escape_option_value,
    estimate_width,
    group_lines,
    pick_font,
)
from common import OUT_HEIGHT, MontageError, Word

from conftest import HAVE_FFMPEG, fake_words


def speech(n: int = 40, step: float = 0.38, text: str = "слово") -> list[Word]:
    return [Word(f"{text}{i}", round(i * step, 3), round(i * step + 0.3, 3)) for i in range(n)]


def parse_dialogues(ass: str) -> list[tuple[float, float, str]]:
    out = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            fields = line.split(",", 9)
            out.append((to_seconds(fields[1]), to_seconds(fields[2]), fields[9]))
    return out


def to_seconds(stamp: str) -> float:
    h, m, rest = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def plain(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", text)


def test_two_to_four_words_per_line_and_width_limit() -> None:
    words = speech(60) + [Word("высокоэффективный", 30.0, 30.6), Word("ну", 30.7, 30.9), Word("и", 31.0, 31.2)]
    lines = group_lines(words)
    for line in lines:
        assert 1 <= len(line.words) <= MAX_WORDS
        assert estimate_width(line.text) <= MAX_LINE_WIDTH + 1e-6 or len(line.words) == 1
    inner = [len(line.words) for line in lines]
    assert max(inner) == MAX_WORDS or max(inner) >= 2
    assert sum(1 for n in inner if n == 1) <= 2  # lone words are the exception


def test_wide_words_make_shorter_lines() -> None:
    words = [Word("электроэнцефалограмма", 0.0, 0.5), Word("психофизиология", 0.6, 1.1), Word("нейрофизиология", 1.2, 1.7)]
    for line in group_lines(words):
        assert estimate_width(line.text) <= MAX_LINE_WIDTH or len(line.words) == 1


def test_lone_overwide_word_is_shrunk_to_fit() -> None:
    word = Word("а" * 30, 0.0, 1.0)
    ass = build_ass([word], font="Arial")
    text = parse_dialogues(ass)[0][2]
    size = int(re.search(r"\\fs(\d+)", text).group(1))
    assert size < FONT_SIZE
    assert estimate_width("а" * 30, size) <= MAX_LINE_WIDTH + 1e-6


def test_pause_and_sentence_end_start_a_new_line() -> None:
    words = [Word("раз", 0.0, 0.3), Word("два.", 0.35, 0.7), Word("три", 0.75, 1.0), Word("четыре", 2.5, 2.9), Word("пять", 2.95, 3.3)]
    lines = [line.text for line in group_lines(words)]
    assert lines == ["раз два.", "три", "четыре пять"]


def test_events_are_monotonic_and_do_not_overlap() -> None:
    words = fake_words() + [Word("накладка", 11.0, 11.4), Word("слов", 11.3, 11.6)]  # overlapping times from a sloppy recogniser
    events = build_events(group_lines(words))
    assert events
    for (a1, b1, _), (a2, b2, _) in zip(events, events[1:]):
        assert a1 < b1
        assert a2 >= a1
        assert b1 <= a2 + 1e-9  # no overlap
    assert all(b > a for a, b, _ in events)


def test_ass_times_are_monotonic_in_the_file() -> None:
    ass = build_ass(speech(80), font="Arial")
    dialogues = parse_dialogues(ass)
    starts = [a for a, _, _ in dialogues]
    assert starts == sorted(starts)
    assert all(b > a for a, b, _ in dialogues)


def test_every_word_is_highlighted_once() -> None:
    words = speech(12)
    dialogues = parse_dialogues(build_ass(words, font="Arial"))
    lit = [re.search(r"\\1c&H00D7FF&\}([^{]+)\{", d[2]).group(1) for d in dialogues]
    assert lit == [w.text for w in words]
    for _, _, text in dialogues:
        assert len(plain(text).split()) <= MAX_WORDS


def test_event_text_is_the_line_text() -> None:
    dialogues = parse_dialogues(build_ass([Word("привет", 0.0, 0.4), Word("мир", 0.5, 0.9)], font="Arial"))
    assert [plain(t) for _, _, t in dialogues] == ["привет мир", "привет мир"]


def test_style_places_captions_in_the_safe_zone() -> None:
    ass = build_ass(speech(6), font="Montserrat")
    style = next(line for line in ass.splitlines() if line.startswith("Style:"))
    fields = style[len("Style: "):].split(",")
    assert fields[1] == "Montserrat"
    assert int(fields[2]) == FONT_SIZE
    assert fields[-5] == "2"  # alignment: bottom centre
    margin_l, margin_r, margin_v = (int(x) for x in fields[-4:-1])
    bottom = 1920 - margin_v  # the bottom edge of the text box
    centre = bottom - FONT_SIZE / 2
    assert 0.74 <= centre / 1920 <= 0.78  # lower third, below the face
    assert (bottom + int(fields[-7]) + int(fields[-6])) / 1920 <= 0.82  # outline and shadow still above the Instagram zone
    assert 1080 - margin_l - margin_r == MAX_LINE_WIDTH
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


def test_ass_position_follows_y_and_both_backends_share_the_default() -> None:
    def margin_v(ass: str) -> int:
        style = next(line for line in ass.splitlines() if line.startswith("Style:"))
        return int(style[len("Style: "):].split(",")[-2])

    low = margin_v(build_ass(speech(4), font="Arial", center_y=0.8))
    high = margin_v(build_ass(speech(4), font="Arial", center_y=0.6))
    assert low < high
    assert margin_v(build_ass(speech(4), font="Arial")) == OUT_HEIGHT - box_bottom_y(CAPTION_CENTER_Y, FONT_SIZE)
    assert 0.74 <= CAPTION_CENTER_Y <= 0.78


def test_y_option_is_checked_and_reaches_the_ass_file(tmp_path: Path) -> None:
    from common import WordsFile, save_words

    words_file = tmp_path / "w.json"
    save_words(words_file, WordsFile(words=speech(6), duration=5.0))
    ass = tmp_path / "c.ass"
    assert captions.main([str(tmp_path / "v.mp4"), str(words_file), "--ass", str(ass), "--ass-only", "--font", "Arial", "--y", "0.8"]) == 0
    style = next(line for line in ass.read_text(encoding="utf-8").splitlines() if line.startswith("Style:"))
    assert int(style.split(",")[-2]) == OUT_HEIGHT - box_bottom_y(0.8)
    for bad in ("0.05", "1.2"):
        assert captions.main([str(tmp_path / "v.mp4"), str(words_file), "--ass", str(ass), "--ass-only", "--y", bad]) == 2
    with pytest.raises(MontageError, match="--y"):
        check_center_y(0.1)


def test_outline_is_thick_and_there_is_a_shadow() -> None:
    style = next(line for line in build_ass(speech(3), font="Arial").splitlines() if line.startswith("Style:"))
    fields = style[len("Style: "):].split(",")
    assert int(fields[-7]) == OUTLINE >= 8 and int(fields[-6]) >= 3  # Outline, Shadow


def test_lone_short_word_before_a_trimmed_pause_joins_the_next_line() -> None:
    # The real case: «Я» (0.20-0.65), a trimmed pause, then the next phrase 0.30 s later. It used to be a one-word line.
    words = [Word("Я", 0.2, 0.65), Word("три", 0.947, 1.147), Word("месяца", 1.147, 1.607), Word("откладывал", 1.607, 2.247)]
    lines = group_lines(words)
    assert [line.text for line in lines] == ["Я три месяца", "откладывал"]


def test_real_take_after_the_first_cut_has_no_lone_one_letter_caption() -> None:
    from common import build_map, remap_words
    from conftest import real_take

    # The cut of the real run: the 0.2 s pause after the first word is removed (source 0.78-0.98 -> seam at 0.767).
    cut = remap_words(real_take().words, build_map([(0.0, 0.767), (1.0, 4.8), (5.133, 35.967)]))
    assert cut[0].text == "Я" and cut[1].start - cut[0].end > captions.GAP_BREAK  # the pause that used to split the line
    lines = group_lines(cut)
    assert lines[0].text == "Я три месяца"
    # no state shows a caption of one short word before the first words of the sentence have started
    assert all(len(clean_text_letters(l.text)) > 1 for l in lines[:12])


def clean_text_letters(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def test_lone_word_stays_alone_when_the_pause_is_long_or_it_ends_a_sentence() -> None:
    assert [l.text for l in group_lines([Word("Да", 0.0, 0.3), Word("потом", 1.5, 1.9)])] == ["Да", "потом"]
    assert [l.text for l in group_lines([Word("Да.", 0.0, 0.3), Word("потом", 0.5, 0.9)])] == ["Да.", "потом"]
    assert [l.text for l in group_lines([Word("Конструкция", 0.0, 0.6), Word("потом", 0.95, 1.2)])] == ["Конструкция", "потом"]


def test_exactly_one_word_is_highlighted_in_every_state_of_a_multiword_line() -> None:
    words = [Word("Я", 0.2, 0.65), Word("три", 0.947, 1.147), Word("месяца", 1.147, 1.607), Word("откладывал", 1.607, 2.247),
             Word("код", 2.4, 2.8)]
    lines = group_lines(words)
    for st in build_states(lines):
        assert len(lines[st.line].words) > 1  # a one-word line would be «all highlighted»
        assert 0 <= st.pos < len(lines[st.line].words)
    events = build_events(lines)
    assert all(text.count("\\1c&H00D7FF&") == 1 for _, _, text in events)


def test_ass_text_cannot_inject_override_tags() -> None:
    dialogues = parse_dialogues(build_ass([Word("{\\an8}хитрость", 0.0, 0.5)], font="Arial"))
    assert "{\\an8}" not in dialogues[0][2]


@pytest.mark.parametrize("seconds,expected", [(0, "0:00:00.00"), (61.256, "0:01:01.26"), (3725.5, "1:02:05.50")])
def test_ass_time_format(seconds: float, expected: str) -> None:
    assert ass_time(seconds) == expected


# --- font and ffmpeg -------------------------------------------------------------------------------------------------


def test_font_choice_prefers_installed_families_and_falls_back() -> None:
    assert pick_font("Roboto", {"arial"}) == "Roboto"
    assert pick_font(None, {"arial", "dejavu sans"}) == "Arial"
    assert pick_font(None, {"dejavu sans", "noto sans"}) == "DejaVu Sans"
    assert pick_font(None, {"comic neue"}) == captions.FALLBACK_FONT
    assert pick_font(None, None) == captions.FALLBACK_FONT


def test_filter_path_escaping_has_two_levels() -> None:
    # Level 1, the option parser of one filter: backslash, quote and the colon between options.
    assert escape_option_value("it's:here\\x.ass") == "it\\'s\\:here\\\\x.ass"
    assert escape_option_value("a,b[1].ass") == "a,b[1].ass"
    # Level 2, the filtergraph parser: backslash, quote, brackets, equals sign, comma, semicolon.
    assert escape_filtergraph("a,b[1];c=d.ass") == "a\\,b\\[1\\]\\;c\\=d.ass"
    assert escape_filtergraph("it\\'s") == "it\\\\\\'s"
    assert escape_filtergraph(" x ") == "\\ x\\ "
    # Both, in this order: the backslashes of the first level are doubled by the second.
    assert escape_filter_value("a b.ass") == "a b.ass"
    assert escape_filter_value("it's.ass") == "it\\\\\\'s.ass"
    assert escape_filter_value("cap:ru.ass") == "cap\\\\:ru.ass"
    assert escape_filter_value("C:\\f\\x.ass") == "C\\\\:\\\\\\\\f\\\\\\\\x.ass"
    assert escape_filter_value("a,b[1];c=d.ass") == "a\\,b\\[1\\]\\;c\\=d.ass"


AWKWARD_NAMES = ["it's.png", "cap:ru.png", "a,b;c[1]=d.png", "two  spaces.png", "quote\"d.png", "(paren)&amp.png", "ёжик.png"]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
@pytest.mark.parametrize("name", AWKWARD_NAMES + (["back\\slash.png"] if sys.platform != "win32" else []))
def test_escaped_file_names_survive_the_real_ffmpeg_parser(tmp_path: Path, name: str) -> None:
    # The `ass` filter may be missing, so the same escaping is exercised with the `movie` filter, which takes
    # a file name through the same two parsers (filtergraph, then options).
    folder = tmp_path / "it's [a]; b,c=d"
    folder.mkdir()
    source = folder / name
    made = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=16x16:d=1", "-frames:v", "1", str(source)],
                          capture_output=True, text=True, check=False)
    assert made.returncode == 0, made.stderr
    out = tmp_path / "out.png"
    graph = f"movie=filename={escape_filter_value(str(source))}[v]"
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", str(out)],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert out.stat().st_size > 0


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_one_level_escaping_really_fails_for_these_names(tmp_path: Path) -> None:
    # Guards the test above: with the old single-level escaping an apostrophe in the name breaks ffmpeg.
    source = tmp_path / "it's.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=16x16:d=1", "-frames:v", "1", str(source)], check=True)
    single = str(source).replace("\\", "\\\\")
    for ch in ("'", ":", ",", "[", "]", ";"):
        single = single.replace(ch, "\\" + ch)
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", f"movie=filename={single}[v]", "-map", "[v]", "-frames:v", "1",
                           str(tmp_path / "o.png")], capture_output=True, text=True, check=False)
    assert done.returncode != 0


def test_burn_command_uses_the_ass_filter_and_keeps_audio() -> None:
    command = burn_command(Path("/t/in.mp4"), Path("/t/cap.ass"), Path("/t/out.mp4"))
    assert command[command.index("-vf") + 1] == "ass=cap.ass"
    assert command[command.index("-c:a") + 1] == "copy"
    assert "+faststart" in command and command[-1] == "/t/out.mp4"
    with_fonts = burn_command(Path("in.mp4"), Path("/t/cap.ass"), Path("out.mp4"), Path("/fonts dir"))
    assert with_fonts[with_fonts.index("-vf") + 1] == "ass=cap.ass:fontsdir=/fonts dir"


def test_relative_video_path_still_resolves_when_ffmpeg_runs_in_the_ass_folder(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "subtitles").mkdir()
    (tmp_path / "take.mp4").write_bytes(b"x")
    (tmp_path / "subtitles" / "captions.ass").write_text("[Script Info]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(captions, "has_libass", lambda: True)
    monkeypatch.setattr(captions, "require_tool", lambda name: name)
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        seen["command"], seen["cwd"] = command, kwargs.get("cwd")

        class Done:
            returncode = 0
            stderr = ""

        return Done()

    monkeypatch.setattr(captions.subprocess, "run", fake_run)
    captions.burn(Path("take.mp4"), Path("subtitles/captions.ass"), Path("out/cap.mp4"), Path("fonts"))
    command = seen["command"]
    assert isinstance(command, list)
    video = Path(command[command.index("-i") + 1])
    # ffmpeg starts in the folder of the ASS file; the video must be found from there
    assert video.is_absolute() and video.is_file()
    assert Path(str(seen["cwd"])).resolve() == (tmp_path / "subtitles").resolve()
    assert Path(command[-1]).is_absolute()
    assert Path(command[command.index("-vf") + 1].split("fontsdir=")[1]).is_absolute()


def test_libass_check_reads_the_filter_list(monkeypatch: pytest.MonkeyPatch) -> None:
    class Done:
        returncode = 0

        def __init__(self, out: str) -> None:
            self.stdout = out

    monkeypatch.setattr(captions, "require_tool", lambda name: name)
    with_ass = " T.. ass               V->V       Render ASS subtitles onto input video using the libass library.\n ... null V->V x\n"
    monkeypatch.setattr(captions.subprocess, "run", lambda *a, **k: Done(with_ass))
    assert captions.has_libass() is True
    without = " ... null               V->V       Pass the source unchanged.\n TS overlay VV->V Overlay.\n"
    monkeypatch.setattr(captions.subprocess, "run", lambda *a, **k: Done(without))
    assert captions.has_libass() is False


def test_burn_without_libass_says_how_to_fix_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(captions, "has_libass", lambda: False)
    with pytest.raises(MontageError, match="libass") as info:
        captions.burn(tmp_path / "a.mp4", tmp_path / "a.ass", tmp_path / "b.mp4")
    assert "Homebrew" in str(info.value) and "grep" in str(info.value)


def test_main_ass_only_writes_a_file_without_ffmpeg_libass(tmp_path: Path) -> None:
    from common import WordsFile, save_words

    words_file = tmp_path / "words-cut.json"
    save_words(words_file, WordsFile(words=speech(10), duration=5.0))
    ass = tmp_path / "c.ass"
    code = captions.main([str(tmp_path / "none.mp4"), str(words_file), "--ass", str(ass), "--ass-only", "--font", "Arial"])
    assert code == 0
    assert "[Events]" in ass.read_text(encoding="utf-8")
