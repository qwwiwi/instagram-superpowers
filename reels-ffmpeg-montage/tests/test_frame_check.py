"""Measurable acceptance checks: empty bands, the top service zone, light scenes, caption lines, numbers and
invented file names against the speech."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import NEED_FFMPEG
from frame_check import (
    FRAME_H,
    FRAME_W,
    analyse_frame,
    frame_results,
    main,
    speech_numbers,
    text_results,
)


def canvas(luma: int = 30) -> bytearray:
    return bytearray([luma]) * (FRAME_W * FRAME_H)


def text_rows(img: bytearray, y0: int, y1: int, x0: int = 140, x1: int = 940, ink: int = 230) -> None:
    """Draw 'text': alternating 6 px strokes, so every row of the block has many sharp edges."""
    for y in range(y0, y1):
        for x in range(x0, x1):
            if (x // 6) % 2 == 0:
                img[y * FRAME_W + x] = ink


def card_outline(img: bytearray, y0: int, y1: int, x0: int = 120, x1: int = 960, ink: int = 240) -> None:
    """A card: a bright body with a 4 px dark border on both sides (no content inside)."""
    for y in range(y0, y1):
        for x in range(x0, x1):
            img[y * FRAME_W + x] = 20 if x - x0 < 4 or x1 - x <= 4 else ink


def test_an_empty_card_body_is_an_empty_band_in_the_split_scene() -> None:
    img = canvas()
    text_rows(img, 150, 240)                 # title
    card_outline(img, 260, 740)               # card with an empty body
    text_rows(img, 270, 340)                 # header only
    report = analyse_frame(bytes(img), 6.0, "split")
    assert report.bands and report.bands[0][1] - report.bands[0][0] >= 250


def test_a_filled_scene_has_no_empty_band_and_a_clean_top() -> None:
    img = canvas()
    for top in range(150, 750, 120):
        text_rows(img, top, top + 70)
    report = analyse_frame(bytes(img), 1.0, "split")
    assert report.bands == [] and report.top_rows == [] and not report.light


def test_a_sticker_in_the_top_service_zone_is_found() -> None:
    img = canvas()
    for top in range(150, 750, 120):
        text_rows(img, top, top + 70)
    text_rows(img, 60, 120, x0=700, x1=1000)  # a sticker at y 60-120
    report = analyse_frame(bytes(img), 7.5, "visual")
    assert report.top_rows and report.top_rows[0] >= 60


def test_speaker_frames_are_not_measured() -> None:
    report = analyse_frame(bytes(canvas(250)), 3.0, "speaker")
    assert report.bands == [] and not report.light


def test_light_scenes_over_a_quarter_of_the_reel_fail() -> None:
    dark, light = canvas(30), canvas(240)
    for img in (dark, light):
        for top in range(150, 1280, 120):
            text_rows(img, top, top + 70, ink=120)
    reports = [analyse_frame(bytes(light), t, "visual") for t in (0.0, 0.5)]
    reports += [analyse_frame(bytes(dark), t, "visual") for t in (1.0, 1.5, 2.0)]
    light_check = next(r for r in frame_results(reports) if r.name == "light scenes")
    assert not light_check.ok and "40 %" in light_check.detail
    reports += [analyse_frame(bytes(dark), t, "visual") for t in (2.5, 3.0, 3.5)]
    assert next(r for r in frame_results(reports) if r.name == "light scenes").ok


HTML = """
<div class="clip cap cap-split"><div class="line"><span class="w">ЭТО</span> <span class="w">ОЧЕНЬ</span>
<span class="w">ПРОСТО</span> <span class="w">ВСЁ</span></div><div class="line"><span class="w">ТРИ</span></div></div>
<div class="card"><div class="head">Три файла</div><div class="row"><b>index.html</b> 120 строк</div>
<div class="row">CLAUDE.md</div><span>2 400</span><span>3</span></div>
<script>tl.set("#w1", {opacity: 1}, 0.5);</script>
"""
SAID = "это очень просто всё три файла claude md и двадцать четыре сотни".split()


def test_caption_lines_over_three_words_fail() -> None:
    results, _ = text_results(HTML, SAID)
    check = next(r for r in results if r.name == "caption words per line")
    assert not check.ok and "ЭТО ОЧЕНЬ ПРОСТО ВСЁ" in check.detail


def test_numbers_and_file_names_not_said_are_found() -> None:
    results, words = text_results(HTML, SAID)
    numbers = next(r for r in results if r.name == "numbers come from the speech")
    assert not numbers.ok and "120" in numbers.detail and "2 400" in numbers.detail and " 3" not in numbers.detail
    filler = next(r for r in results if r.name == "no invented file names or commands")
    assert not filler.ok and "index.html" in filler.detail and "CLAUDE.md" not in filler.detail
    assert "строк" in words and "opacity" not in words


def test_speech_numbers_read_digits_and_words() -> None:
    assert {3, 25, 2, 1000} <= speech_numbers("три файла двадцать пять двух тысяч".split()) | speech_numbers(["1000"])
    assert 25 in speech_numbers(["двадцать", "пять"])


@NEED_FFMPEG
def test_cli_runs_on_exported_frames(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xF0F0F0:s=1080x1920",
                    "-frames:v", "1", str(frames / "t0000.00.jpg")], check=True)
    plans = tmp_path / "plans.json"
    plans.write_text(json.dumps([{"start": 0, "end": 1, "kind": "visual"}]), encoding="utf-8")
    assert main(["frames", str(frames), "--plans", str(plans)]) == 1  # light and empty
