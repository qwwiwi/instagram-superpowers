"""End to end on a synthetic take: test pattern + tone bursts, a fake words.json, real ffmpeg."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import captions
import phrases
import plan_cut
import render_cut
import verify
from common import WordsFile, load_words, probe, save_words

from conftest import CLIP_DURATION, NEED_FFMPEG, fake_words

pytestmark = NEED_FFMPEG

HAVE_LIBASS = False
try:
    HAVE_LIBASS = captions.has_libass()
except Exception:  # ffmpeg missing: the module-level skip above already covers it
    HAVE_LIBASS = False


@pytest.fixture(scope="module")
def work(synthetic_clip: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Run transcribe-less pipeline steps once: words -> phrases -> plan -> render."""
    folder = tmp_path_factory.mktemp("e2e")
    words_json = folder / "words.json"
    save_words(words_json, WordsFile(words=fake_words(), duration=CLIP_DURATION, language="ru", source=str(synthetic_clip)))
    paths = {
        "words": words_json,
        "phrases": folder / "phrases.json",
        "table": folder / "phrases.md",
        "plan": folder / "cut-plan.json",
        "report": folder / "cut-plan.md",
        "video": folder / "reel-cut.mp4",
        "words_cut": folder / "words-cut.json",
        "map": folder / "cut-map.json",
        "clip": synthetic_clip,
        "dir": folder,
    }
    assert phrases.main([str(words_json), "--out", str(paths["phrases"]), "--table", str(paths["table"])]) == 0
    # First pass: nothing confirmed, nothing dropped.
    assert plan_cut.main([str(words_json), "--phrases", str(paths["phrases"]), "--out", str(paths["plan"]), "--report", str(paths["report"])]) == 0
    first = json.loads(paths["plan"].read_text(encoding="utf-8"))
    assert first["drop"] == [] and {c["phrase"] for c in first["candidates"]} == {1, 3}
    # Second pass: the "student" confirmed phrases 1 and 3.
    assert plan_cut.main([str(words_json), "--phrases", str(paths["phrases"]), "--drop", "1,3", "--speed", "1.0",
                          "--out", str(paths["plan"]), "--report", str(paths["report"])]) == 0
    code = render_cut.main([str(synthetic_clip), str(paths["plan"]), "--words", str(words_json), "--out", str(paths["video"]),
                            "--words-out", str(paths["words_cut"]), "--map-out", str(paths["map"])])
    assert code == 0
    return paths


def test_plan_applies_the_confirmed_drops_and_trims_pauses(work: dict[str, Path]) -> None:
    plan = json.loads(work["plan"].read_text(encoding="utf-8"))
    assert plan["drop"] == [1, 3]
    assert plan["params"]["energy_check"] is True
    kept = plan["stats"]["kept_seconds"]
    # 13 s take: P1 (head, 3.4 s), P3 with its pauses, shorter pauses and the long tail go.
    assert 5.0 < kept < 7.5
    assert len(plan["keep"]) == 3


def test_render_gives_1080x1920_30fps_h264_aac(work: dict[str, Path]) -> None:
    info = probe(work["video"])
    assert (info.width, info.height) == (1080, 1920)
    assert info.fps == pytest.approx(30.0, abs=0.01)
    assert info.video_codec == "h264" and info.audio_codec == "aac"
    cut_map = json.loads(work["map"].read_text(encoding="utf-8"))
    assert info.duration == pytest.approx(cut_map["expected_duration"], abs=0.15)
    assert len(cut_map["seams"]) == 2
    assert verify.moov_before_mdat(work["video"]) is True


def test_words_cut_match_the_new_video(work: dict[str, Path]) -> None:
    cut = load_words(work["words_cut"])
    spoken = [w.text for w in cut.words]
    assert spoken == "сегодня я расскажу про монтаж рилсов это очень просто и это всё".split()
    assert cut.words[0].start >= 0.0 and cut.words[-1].end <= probe(work["video"]).duration + 0.05
    starts = [w.start for w in cut.words]
    assert starts == sorted(starts)
    # the first kept word sits 0.2 s into the clip (head air)
    assert cut.words[0].start == pytest.approx(0.2, abs=0.05)


def test_verify_passes_and_exports_seam_frames(work: dict[str, Path]) -> None:
    seams = work["dir"] / "seams"
    code = verify.main([str(work["video"]), "--cut-map", str(work["map"]), "--seams-dir", str(seams)])
    assert code == 0
    assert (seams / "index.txt").read_text(encoding="utf-8").count("seam ") == 2
    for n in (1, 2):
        for suffix in ("-before", "-after", ""):
            assert (seams / f"seam-{n:03d}{suffix}.png").stat().st_size > 1000


def test_verify_fails_on_a_clip_that_is_wrong(work: dict[str, Path]) -> None:
    # The raw take is 1080x1920 too, but it is unnormalised, has long silences and is longer than a tight limit.
    code = verify.main([str(work["clip"]), "--max-duration", "5", "--seams-dir", str(work["dir"] / "x"), "--no-seams"])
    assert code == 1


def test_speed_up_shortens_the_video(work: dict[str, Path]) -> None:
    fast_plan = work["dir"] / "fast-plan.json"
    assert plan_cut.main([str(work["words"]), "--phrases", str(work["phrases"]), "--drop", "1,3", "--speed", "1.25",
                          "--out", str(fast_plan), "--report", str(work["dir"] / "fast.md")]) == 0
    out = work["dir"] / "fast.mp4"
    assert render_cut.main([str(work["clip"]), str(fast_plan), "--words", str(work["words"]), "--out", str(out),
                            "--words-out", str(work["dir"] / "fw.json"), "--map-out", str(work["dir"] / "fm.json")]) == 0
    normal = probe(work["video"]).duration
    assert probe(out).duration == pytest.approx(normal / 1.25, abs=0.2)


def test_ass_for_the_cut_words_is_written_without_libass(work: dict[str, Path]) -> None:
    ass = work["dir"] / "captions.ass"
    assert captions.main([str(work["video"]), str(work["words_cut"]), "--ass", str(ass), "--ass-only"]) == 0
    text = ass.read_text(encoding="utf-8")
    assert text.count("Dialogue:") >= len(load_words(work["words_cut"]).words)


@pytest.mark.skipif(not HAVE_LIBASS, reason="this ffmpeg has no libass (the `ass` filter is missing): captions cannot be burned in")
def test_captions_are_burned_into_the_video(work: dict[str, Path]) -> None:
    out = work["dir"] / "reel-captioned.mp4"
    code = captions.main([str(work["video"]), str(work["words_cut"]), "--backend", "ass", "--out", str(out),
                          "--ass", str(work["dir"] / "c.ass")])
    assert code == 0
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration == pytest.approx(probe(work["video"]).duration, abs=0.1)
    assert verify.main([str(out), "--cut-map", str(work["map"]), "--seams-dir", str(work["dir"] / "s2")]) == 0


def test_burn_without_libass_fails_cleanly(work: dict[str, Path]) -> None:
    if HAVE_LIBASS:
        pytest.skip("this ffmpeg has libass, the failure path cannot be reached")
    out = work["dir"] / "never.mp4"
    assert captions.main([str(work["video"]), str(work["words_cut"]), "--backend", "ass", "--out", str(out),
                          "--ass", str(work["dir"] / "n.ass")]) == 2
    assert not out.exists()
