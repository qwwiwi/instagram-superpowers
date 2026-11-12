"""Measurable acceptance checks: empty bands, the top service zone, light scenes by reel duration, caption
lines and the on-screen word list."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import NEED_FFMPEG
from frame_check import (
    FRAME_H,
    FRAME_W,
    analyse_frame,
    FrameReport,
    frame_results,
    main,
    text_results,
)
from frames_sheet import frame_times


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


def test_a_plain_icon_in_the_top_service_zone_is_found() -> None:
    # A plain bright 64 x 64 square at y 40-104: only four edges per row, the row test alone misses it.
    img = canvas()
    for top in range(150, 750, 120):
        text_rows(img, top, top + 70)
    for y in range(40, 104):
        for x in range(500, 564):
            img[y * FRAME_W + x] = 235
    report = analyse_frame(bytes(img), 2.0, "split")
    assert report.top_objects and report.top_objects[0][1] <= 40 and report.top_objects[0][3] >= 104
    top = next(r for r in frame_results([report], 3.0) if r.name.startswith("top zone"))
    assert not top.ok


def test_a_small_dot_in_the_top_zone_is_not_an_object() -> None:
    img = canvas()
    for y in range(60, 70):
        for x in range(500, 510):
            img[y * FRAME_W + x] = 235
    assert analyse_frame(bytes(img), 2.0, "split").top_objects == []


def test_speaker_frames_are_not_measured() -> None:
    report = analyse_frame(bytes(canvas(250)), 3.0, "speaker")
    assert report.bands == [] and not report.light


def _light_check(duration: float, light_from: float, light_to: float):
    times = frame_times(duration, 30.0)
    reports = [FrameReport(t, "visual", light=light_from <= t < light_to) for t in times]
    return next(r for r in frame_results(reports, duration) if r.name == "light scenes")


def test_light_share_is_time_over_the_whole_reel() -> None:
    # 40 s reel sampled like frames_sheet.py (0.25 s in the head and the end, 0.5 s in the body). Light 5-16 s is
    # 27.5 % of the reel: FAIL, although only about 21 % of the frames are light. Light 0-10 s is 25 %: PASS,
    # although about 29 % of the frames are light.
    over = _light_check(40.0, 5.0, 16.0)
    assert not over.ok and "27.5 %" in over.detail
    at_limit = _light_check(40.0, 0.0, 10.0)
    assert at_limit.ok and "25.0 %" in at_limit.detail


def test_speaker_time_counts_in_the_denominator() -> None:
    reports = [FrameReport(0.0, "visual", light=True), FrameReport(1.0, "speaker"), FrameReport(2.0, "speaker"),
               FrameReport(3.0, "speaker")]
    light = next(r for r in frame_results(reports, 4.0) if r.name == "light scenes")
    assert light.ok and "25.0 %" in light.detail  # 1 s of 4 s; exactly the limit passes (share <= 25 %)


HTML = """
<div class="clip cap cap-split"><div class="line"><span class="w">ЭТО</span> <span class="w">ОЧЕНЬ</span>
<span class="w">ПРОСТО</span> <span class="w">ВСЁ</span></div><div class="line"><span class="w">ТРИ</span></div></div>
<div class="card"><div class="head">Три файла</div><div class="row"><b>index.html</b> 120 строк</div>
<div class="row">CLAUDE.md</div><span>2 400</span><span>3</span></div>
<script>tl.set("#w1", {opacity: 1}, 0.5);</script>
"""


def test_caption_lines_over_three_words_fail() -> None:
    results, _ = text_results(HTML)
    check = next(r for r in results if r.name == "caption words per line")
    assert not check.ok and "ЭТО ОЧЕНЬ ПРОСТО ВСЁ" in check.detail


def test_text_lists_the_screen_words_and_compares_nothing_with_the_speech() -> None:
    results, words = text_results(HTML)
    assert [r.name for r in results] == ["caption words per line"]  # numbers and names: reviewer checklist
    assert "строк" in words and "index" in words and "opacity" not in words and "ТРИ" in words


@NEED_FFMPEG
def test_cli_runs_on_exported_frames(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xF0F0F0:s=1080x1920",
                    "-frames:v", "1", str(frames / "t0000.00.jpg")], check=True)
    plans = tmp_path / "plans.json"
    plans.write_text(json.dumps([{"start": 0, "end": 1, "kind": "visual"}]), encoding="utf-8")
    assert main(["frames", str(frames), "--plans", str(plans)]) == 1  # light and empty
