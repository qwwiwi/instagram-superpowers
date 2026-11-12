"""ASS generation: line width, timing, highlight, font choice, burn command."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import captions
from captions import (
    FONT_SIZE,
    MAX_LINE_WIDTH,
    MAX_WORDS,
    SAFE_BOTTOM_Y,
    ass_time,
    build_ass,
    build_events,
    burn_command,
    escape_filter_value,
    estimate_width,
    group_lines,
    pick_font,
)
from common import MontageError, Word

from conftest import fake_words


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
    assert 1920 - margin_v == SAFE_BOTTOM_Y <= 1260
    assert 1080 - margin_l - margin_r == MAX_LINE_WIDTH
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


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


def test_filter_path_escaping() -> None:
    assert escape_filter_value("a b.ass") == "a b.ass"
    assert escape_filter_value("it's:here,[x].ass") == "it\\'s\\:here\\,\\[x\\].ass"
    assert escape_filter_value("C:\\f\\x.ass") == "C\\:\\\\f\\\\x.ass"


def test_burn_command_uses_the_ass_filter_and_keeps_audio() -> None:
    command = burn_command(Path("/t/in.mp4"), Path("/t/cap.ass"), Path("/t/out.mp4"))
    assert command[command.index("-vf") + 1] == "ass=cap.ass"
    assert command[command.index("-c:a") + 1] == "copy"
    assert "+faststart" in command and command[-1] == "/t/out.mp4"
    with_fonts = burn_command(Path("in.mp4"), Path("/t/cap.ass"), Path("out.mp4"), Path("/fonts dir"))
    assert with_fonts[with_fonts.index("-vf") + 1] == "ass=cap.ass:fontsdir=/fonts dir"


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
