"""Offline tests of the file check: ffprobe parsing and the verdict rules."""

from __future__ import annotations

from pathlib import Path

from check_reel import facts_from_ffprobe, main, parse_rate, parse_volumedetect, verdict

GOOD = {"width": 1080, "height": 1920, "fps": 30.0, "has_audio": True, "duration": 41.2, "mean_volume": -20.0}


def test_parse_rate_and_volume() -> None:
    assert parse_rate("30/1") == 30.0
    assert abs(parse_rate("30000/1001") - 29.97) < 0.01
    assert parse_rate("") == 0.0
    log = "[Parsed_volumedetect_0] mean_volume: -33.5 dB\n[Parsed_volumedetect_0] max_volume: -inf dB"
    assert parse_volumedetect(log) == {"mean_volume": -33.5, "max_volume": float("-inf")}


def test_facts_from_ffprobe() -> None:
    info = {
        "streams": [
            {"codec_type": "video", "width": 1080, "height": 1920, "codec_name": "h264", "avg_frame_rate": "30/1"},
            {"codec_type": "audio", "sample_rate": "48000"},
        ],
        "format": {"duration": "41.200000"},
    }
    facts = facts_from_ffprobe(info)
    assert facts == {"duration": 41.2, "width": 1080, "height": 1920, "video_codec": "h264", "fps": 30.0, "has_audio": True}
    assert facts_from_ffprobe({"streams": [], "format": {}})["has_audio"] is False


def test_verdict_rules() -> None:
    assert verdict(GOOD) == []
    assert any("1080x1920" in p for p in verdict({**GOOD, "width": 1440, "height": 2560}))
    assert any("frame rate" in p for p in verdict({**GOOD, "fps": 60.0}))
    assert verdict({**GOOD, "fps": 60.0}, expect_fps=None) == []
    assert any("no audio" in p for p in verdict({**GOOD, "has_audio": False}))
    assert any("silent" in p for p in verdict({**GOOD, "mean_volume": float("-inf")}))
    assert any("decode failed" in p for p in verdict({**GOOD, "audio_error": "exit 1"}))
    assert any("longer than" in p for p in verdict(GOOD, max_duration=30))
    assert any("zero" in p for p in verdict({**GOOD, "duration": 0}))


def test_missing_file_exits_2(tmp_path: Path) -> None:
    assert main([str(tmp_path / "none.mp4")]) == 2
