"""Tests of the acceptance helpers: frames_sheet.py (frames, sheets, change measure) and head_guide.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import frames_sheet
import head_guide
from common import MontageError
from conftest import HAVE_FFMPEG

NEED_FFMPEG = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
NEED_PILLOW = pytest.mark.skipif(not frames_sheet.have_pillow(), reason="Pillow is not installed")


# --- times and sheets --------------------------------------------------------------------------------


def test_times_are_dense_in_the_hook_and_the_ending_and_include_the_last_frame() -> None:
    times = frames_sheet.frame_times(20.0, 30.0)
    assert times[:5] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert 5.5 in times and 5.25 not in times and 13.75 not in times
    assert [t for t in times if t >= 14.0][:3] == [14.0, 14.25, 14.5]
    assert times[-1] == pytest.approx(599 / 30, abs=1e-3)
    assert times == sorted(set(times))


def test_short_clip_has_no_time_past_its_end() -> None:
    times = frames_sheet.frame_times(3.0, 30.0)
    assert times[-1] <= 3.0 - 1 / 30 + 1e-3
    assert times[:4] == [0.0, 0.25, 0.5, 0.75]


@pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan")])
def test_times_refuse_bad_numbers(bad: float) -> None:
    with pytest.raises(MontageError):
        frames_sheet.frame_times(bad, 30.0)
    with pytest.raises(MontageError):
        frames_sheet.frame_times(10.0, 30.0, step=bad)


def test_sheets_split_hook_body_end_in_order() -> None:
    times = frames_sheet.frame_times(20.0, 30.0)
    sheets = frames_sheet.group_sheets(times, 20.0, 5.0, 6.0, 12)
    names = [s.name for s in sheets]
    assert names[0] == "sheet-hook-01.png" and names[-1].startswith("sheet-end-")
    assert all(len(s.times) <= 12 for s in sheets)
    assert [t for s in sheets for t in s.times] == times
    index = frames_sheet.index_text(sheets)
    assert index.splitlines()[0].startswith("sheet-hook-01.png: 0.00 0.25")


def test_frame_names_sort_by_time() -> None:
    names = [frames_sheet.frame_name(t) for t in (0.25, 2.0, 12.5, 61.75)]
    assert names == sorted(names) == ["t0000.25.jpg", "t0002.00.jpg", "t0012.50.jpg", "t0061.75.jpg"]


# --- change measure --------------------------------------------------------------------------------------


def test_region_parsing_and_limits() -> None:
    region = frames_sheet.parse_region("split=120,140,840,610")
    assert (region.name, region.x, region.y, region.w, region.h) == ("split", 120, 140, 840, 610)
    for bad in ("split", "split=1,2,3", "=1,2,3,4", "x=0,0,2000,10", "x=0,0,0,10"):
        with pytest.raises(MontageError):
            frames_sheet.parse_region(bad)


def test_change_share_counts_only_inside_the_region() -> None:
    w, h = 108, 192
    a = bytes(w * h)
    b = bytearray(a)
    for row in range(10, 20):  # a 100 x 100 px block at x 0-100, y 100-200 changes
        for col in range(0, 10):
            b[row * w + col] = 200
    inside = frames_sheet.Region("in", 0, 100, 200, 200)
    outside = frames_sheet.Region("out", 500, 1000, 200, 200)
    assert frames_sheet.change_share(a, bytes(b), inside) == pytest.approx(100 / 400)
    assert frames_sheet.change_share(a, bytes(b), outside) == 0.0


def test_static_runs_are_measured_from_the_first_to_the_last_still_frame() -> None:
    pairs = [(0.0, 0.5, 0.05), (0.5, 1.0, 0.0), (1.0, 1.5, 0.001), (1.5, 2.0, 0.0), (2.0, 2.5, 0.02), (2.5, 3.0, 0.0)]
    runs = frames_sheet.static_runs(pairs, 0.004)
    assert runs[0] == (0.5, 2.0)
    assert (2.5, 3.0) in runs


# --- sheet drawing -----------------------------------------------------------------------------------------


def test_ffmpeg_sheet_without_drawtext_has_no_text_but_draws_guides(tmp_path: Path) -> None:
    frames = [tmp_path / f"f{i}.jpg" for i in range(3)]
    args = frames_sheet.ffmpeg_sheet_args(frames, [0.0, 0.25, 0.5], tmp_path / "s.png", 4, 3, True, False)
    graph = args[args.index("-filter_complex") + 1]
    assert "drawtext" not in graph and "drawbox" in graph and "xstack=inputs=3" in graph
    with_text = frames_sheet.ffmpeg_sheet_args(frames, [0.0, 0.25, 0.5], tmp_path / "s.png", 4, 3, False, True)
    graph = with_text[with_text.index("-filter_complex") + 1]
    assert "drawtext=text='0.25 s'" in graph and "drawbox" not in graph


def make_moving_clip(path: Path, seconds: float = 8.0) -> None:
    """Dark 1080x1920 clip with a white 200 px square that moves until 4 s and then stands still."""
    graph = path.with_suffix(".graph.txt")
    graph.write_text("[0][1]overlay=x='if(lt(t,4),120+100*t,520)':y=300[v]\n", encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=0x111418:s=1080x1920:r=30:d={seconds}",
         "-f", "lavfi", "-i", f"color=c=white:s=200x200:r=30:d={seconds}",
         "-filter_complex_script", str(graph), "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


@NEED_FFMPEG
def test_cli_exports_frames_sheets_and_finds_the_static_stretch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clip = tmp_path / "clip.mp4"
    make_moving_clip(clip)
    out = tmp_path / "accept"
    code = frames_sheet.main([str(clip), "--out", str(out), "--head", "2", "--tail", "2",
                              "--region", "split=120,140,840,610", "--region", "low=0,1200,1080,300"])
    assert code == 0
    printed = capsys.readouterr().out
    frames = sorted(p.name for p in (out / "frames").glob("*.jpg"))
    assert frames[0] == "t0000.00.jpg" and "t0000.25.jpg" in frames and "t0003.25.jpg" not in frames
    assert frames[-1] == "t0007.97.jpg"
    assert (out / "sheet-hook-01.png").is_file() and (out / "index.txt").is_file()
    assert "split: stretches without a change >= 0.4 %: 4.00-7.50" in printed
    assert "low: stretches without a change >= 0.4 %: 0.00-7.50" in printed
    rows = (out / "changes.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "t_from\tt_to\tsplit\tlow"
    moving = rows[1].split("\t")
    assert float(moving[2]) > 0.4 and float(moving[3]) == 0.0


@NEED_FFMPEG
def test_audio_longer_than_the_picture_does_not_ask_for_missing_frames(tmp_path: Path) -> None:
    # A real take: the AAC track ends two frames after the last picture, so the container is longer.
    silent = tmp_path / "silent.mp4"
    make_moving_clip(silent, 3.0)
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(silent),
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3.2",
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", str(clip)],
        check=True,
    )
    assert frames_sheet.probe(clip).duration > 3.1
    out = tmp_path / "accept"
    assert frames_sheet.main([str(clip), "--out", str(out), "--head", "1", "--tail", "1"]) == 0
    frames = sorted(p.name for p in (out / "frames").glob("*.jpg"))
    assert frames[-1] == "t0002.97.jpg"
    assert frames_sheet.video_duration(clip, 0.0) == pytest.approx(3.0, abs=1e-3)


@NEED_FFMPEG
def test_sheets_without_pillow_and_drawtext_fall_back_to_the_printed_index(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    clip = tmp_path / "clip.mp4"
    make_moving_clip(clip, 3.0)
    monkeypatch.setattr(frames_sheet, "have_pillow", lambda: False)
    monkeypatch.setattr(frames_sheet, "have_drawtext", lambda: False)
    out = tmp_path / "accept"
    assert frames_sheet.main([str(clip), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "labels: none" in printed and "sheet-hook-01.png: 0.00 0.25" in printed
    assert (out / "sheet-hook-01.png").is_file()


@NEED_FFMPEG
@NEED_PILLOW
def test_pillow_sheet_has_the_label_band_and_the_zone_lines(tmp_path: Path) -> None:
    from PIL import Image

    clip = tmp_path / "clip.mp4"
    make_moving_clip(clip, 2.0)
    out = tmp_path / "accept"
    assert frames_sheet.main([str(clip), "--out", str(out)]) == 0
    with Image.open(out / "sheet-hook-01.png") as sheet:
        rgb = sheet.convert("RGB")
        gap, band = frames_sheet.GAP, frames_sheet.LABEL_BAND
        seam_y = gap + band + 960 // 3
        assert rgb.getpixel((gap + 20, seam_y)) == frames_sheet.GUIDE_COLOR
        label_pixels = [rgb.getpixel((x, gap + 18)) for x in range(gap, gap + 120)]
        assert any(p[0] > 200 and p[1] > 200 for p in label_pixels)


def test_wrong_frame_size_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = tmp_path / "wide.mp4"
    clip.write_bytes(b"x")

    class Info:
        width, height, duration, fps = 1920, 1080, 5.0, 30.0

    monkeypatch.setattr(frames_sheet, "probe", lambda _p: Info())
    assert frames_sheet.main([str(clip), "--out", str(tmp_path / "a")]) == 2


# --- head guide ---------------------------------------------------------------------------------------------


def test_split_offset_puts_the_hair_60_px_under_the_seam() -> None:
    fit = head_guide.split_offset(top=610, chin=1010, eyes=830)
    assert (fit.y0, fit.top, fit.chin, fit.eyes, fit.verdict) == (550, 1020, 1420, 1240, "ok")


def test_split_offset_tightens_for_a_long_face_and_warns_for_a_close_up() -> None:
    fit = head_guide.split_offset(top=500, chin=990)  # 490 px face: 60 px gap would put the chin at 1510
    assert fit.y0 == 480 and fit.top == 980 and fit.chin == 1470 and fit.verdict == "ok"
    close = head_guide.split_offset(top=500, chin=1080)
    assert close.chin == 1560 and close.verdict == "warn"
    too_close = head_guide.split_offset(top=400, chin=1300)
    assert too_close.verdict == "fail"


def test_split_offset_is_clamped_to_the_frame() -> None:
    fit = head_guide.split_offset(top=30, chin=420)
    assert fit.y0 == 0 and fit.top == 990
    with pytest.raises(MontageError):
        head_guide.split_offset(top=900, chin=800)


def test_speaker_caption_goes_to_the_chest_or_above_the_head() -> None:
    chest = head_guide.speaker_caption(top=570, chin=1000)
    assert (chest.mode, chest.box_top, chest.box_bottom, chest.lines) == ("chest", 1180, 1340, 2)
    above = head_guide.speaker_caption(top=540, chin=1300)
    assert (above.mode, above.box_bottom, above.box_top, above.lines) == ("above_head", 516, 356, 2)
    one = head_guide.speaker_caption(top=260, chin=1300)
    assert one.lines == 1 and one.box_bottom == 236
    assert head_guide.speaker_caption(top=150, chin=1300).mode == "none"


def test_guide_rows_mark_every_100_px() -> None:
    rows = head_guide.guide_rows(20)
    assert rows[0] == (20, False) and (100, True) in rows and (1900, True) in rows
    with pytest.raises(MontageError):
        head_guide.guide_rows(30)


def test_guide_filter_draws_lines_and_labels_only_with_drawtext() -> None:
    rows = head_guide.guide_rows(20)
    plain = head_guide.ffmpeg_guide_filter(rows, drawtext=False)
    assert "drawtext" not in plain and "y=959" in plain and plain.count("drawbox") == len(rows) + 1  # 960 and 1520 are drawn as zone lines, 115 is extra
    assert "drawtext=text='1500'" in head_guide.ffmpeg_guide_filter(rows, drawtext=True)


def test_numbers_only_mode_prints_offset_and_caption(capsys: pytest.CaptureFixture[str]) -> None:
    assert head_guide.main(["--top", "610", "--eyes", "830", "--chin", "1010"]) == 0
    out = capsys.readouterr().out
    assert "y0 = 550" in out and "caption mode chest" in out
    assert head_guide.main(["--top", "610"]) == 2


@NEED_FFMPEG
def test_guide_frame_is_full_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = tmp_path / "clip.mp4"
    make_moving_clip(clip, 2.0)
    monkeypatch.chdir(tmp_path)
    assert head_guide.main([str(clip), "--time", "1", "--out", "g"]) == 0
    frame = tmp_path / "g-0001.00.png"
    size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                           str(frame)], capture_output=True, text=True, check=True).stdout.strip()
    assert size == "1080,1920"
