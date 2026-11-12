"""Offline tests of the click guard: the confirm-file rule, coordinate math, the cliclick command. No GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest

import smooth_click
from smooth_click import Point, Refused, check_confirmation, cliclick_command, parse_origin, to_screen_point


@pytest.fixture()
def reel(tmp_path: Path) -> Path:
    path = tmp_path / "reel-final.mp4"
    path.write_bytes(b"x")
    return path


def test_confirmation_must_name_this_file(reel: Path) -> None:
    assert check_confirmation(str(reel), "reel-final.mp4") == reel.resolve()
    with pytest.raises(Refused, match="no --confirm-file"):
        check_confirmation(str(reel), None)
    with pytest.raises(Refused, match="not the file"):
        check_confirmation(str(reel), "other.mp4")
    with pytest.raises(Refused, match="not the file"):
        check_confirmation(str(reel), str(reel))  # a path is not the name the human said
    with pytest.raises(Refused, match="not the file"):
        check_confirmation(str(reel), "REEL-FINAL.mp4")


def test_confirmation_refuses_missing_wrong_type_and_test_files(tmp_path: Path) -> None:
    with pytest.raises(Refused, match="no such file"):
        check_confirmation(str(tmp_path / "gone.mp4"), "gone.mp4")
    notes = tmp_path / "notes.txt"
    notes.write_text("x")
    with pytest.raises(Refused, match="not a video"):
        check_confirmation(str(notes), "notes.txt")
    for name in ("test.mp4", "reel_TEST.mp4", "тест-рилс.mp4", "пробный.mp4", "sample.mov"):
        path = tmp_path / name
        path.write_bytes(b"x")
        with pytest.raises(Refused, match="test file"):
            check_confirmation(str(path), name)


def test_coordinate_math() -> None:
    assert to_screen_point(100, 50, 1.0) == Point(100, 50)
    assert to_screen_point(2410, 97, 2.0) == Point(1205, 48)  # Retina: 2 px per point, rounded
    assert to_screen_point(40, 20, 2.0, (300.0, 200.0)) == Point(320, 210)  # region screenshot
    with pytest.raises(Refused):
        to_screen_point(10, 10, 0)
    with pytest.raises(Refused):
        to_screen_point(-1, 10, 1)


def test_parse_origin() -> None:
    assert parse_origin(None) == (0.0, 0.0)
    assert parse_origin("300,200") == (300.0, 200.0)
    for bad in ("300", "1,2,3", "a,b", "-1,2"):
        with pytest.raises(Refused):
            parse_origin(bad)


def test_cliclick_command_always_eased() -> None:
    assert cliclick_command("move", Point(5, 6)) == ["cliclick", "-e", "200", "m:5,6"]
    assert cliclick_command("click", Point(5, 6), 300) == ["cliclick", "-e", "300", "c:5,6"]
    with pytest.raises(Refused):
        cliclick_command("click", Point(5, 6), 0)
    with pytest.raises(Refused):
        cliclick_command("drag", Point(5, 6))


def test_main_never_runs_cliclick_without_confirmation(reel: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: List[Any] = []
    monkeypatch.setattr(smooth_click.subprocess, "run", lambda *a, **k: ran.append(a))
    base = ["--file", str(reel), "--step", "нажать", "--x", "10", "--y", "10", "--scale", "1"]
    assert smooth_click.main(base) == 2
    assert smooth_click.main(base + ["--confirm-file", "other.mp4"]) == 2
    assert smooth_click.main(base + ["--confirm-file", "reel-final.mp4", "--dry-run"]) == 0
    assert ran == []


def test_main_runs_one_eased_click_when_confirmed(reel: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: List[List[str]] = []

    class Done:
        returncode = 0
        stderr = ""

    def fake_run(command: List[str], **_kwargs: Any) -> Done:
        ran.append(command)
        return Done()

    monkeypatch.setattr(smooth_click.subprocess, "run", fake_run)
    monkeypatch.setattr(smooth_click.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(smooth_click.shutil, "which", lambda _name: "/usr/local/bin/cliclick")
    monkeypatch.setattr(smooth_click.time, "sleep", lambda _s: None)
    args = ["--file", str(reel), "--confirm-file", "reel-final.mp4", "--step", "нажать",
            "--x", "200", "--y", "100", "--scale", "2"]
    assert smooth_click.main(args) == 0
    assert ran == [["cliclick", "-e", "200", "c:100,50"]]


@pytest.mark.parametrize("name", ["reel-draft.mp4", "DRAFT_reel.mov", "черновик.mp4", "рилс-Черновой.mp4"])
def test_main_never_clicks_a_draft_even_when_confirmed(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / name
    path.write_bytes(b"x")
    with pytest.raises(Refused, match="test file"):
        check_confirmation(str(path), name)
    ran: List[Any] = []
    monkeypatch.setattr(smooth_click.subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(smooth_click.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(smooth_click.shutil, "which", lambda _name: "/usr/local/bin/cliclick")
    monkeypatch.setattr(smooth_click.time, "sleep", lambda _s: None)
    args = ["--file", str(path), "--confirm-file", name, "--step", "нажать", "--x", "10", "--y", "10", "--scale", "1"]
    assert smooth_click.main(args) == 2
    assert ran == []


def test_coordinate_math_for_a_region_screenshot() -> None:
    # Retina region shot: 400 pt wide region at (300, 200) gives an 800 px wide image.
    # The scale is image width / REGION width (800 / 400 = 2), not / screen width.
    scale = 800 / 400
    assert to_screen_point(400, 200, scale, (300.0, 200.0)) == Point(500, 300)
