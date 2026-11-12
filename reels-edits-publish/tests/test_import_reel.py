"""Offline tests of the import: path guards, the planned calls, the length check, the run order."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

import import_reel
from import_reel import check_source, length_problem, plan_calls, timeline_length


def test_check_source_guards(tmp_path: Path) -> None:
    movies = tmp_path / "Movies"
    (movies / "reel").mkdir(parents=True)
    good = movies / "reel" / "reel.mp4"
    good.write_bytes(b"x")
    assert check_source(str(good), movies) == good.resolve()
    outside = tmp_path / "reel.mp4"
    outside.write_bytes(b"x")
    with pytest.raises(ValueError, match="under"):
        check_source(str(outside), movies)
    with pytest.raises(ValueError, match="mp4"):
        check_source(str(movies / "reel" / "reel.mkv"), movies)
    with pytest.raises(ValueError, match="No such file"):
        check_source(str(movies / "reel" / "missing.mp4"), movies)


def test_plan_calls_order_and_payloads() -> None:
    calls = plan_calls(Path("/Users/someone/Movies/r/reel.mp4"), " Мой рилс ", "p-1")
    assert [tool for tool, _ in calls] == ["renameProject", "setProjectDynamicRange", "setCanvas", "importMedia"]
    assert calls[0][1] == {"id": "p-1", "name": "Мой рилс"}
    assert calls[1][1] == {"range": "sdr"}
    assert calls[2][1] == {"aspect": "9:16", "frameRate": 30}
    assert calls[3][1] == {"path": "/Users/someone/Movies/r/reel.mp4"}
    with pytest.raises(ValueError):
        plan_calls(Path("/x.mp4"), "  ")


def test_timeline_length_and_tolerance() -> None:
    clips = {"clips": [{"id": "a", "endTime": 12.5}, {"id": "b", "endTime": 41.21}]}
    assert timeline_length(clips) == 41.21
    assert timeline_length([]) == 0.0
    assert timeline_length({"clips": "bad"}) == 0.0
    assert length_problem(41.21, 41.2) is None
    assert length_problem(41.5, 41.2) is not None


class FakeClient:
    """Records calls; answers like the live server did."""

    def __init__(self, clip_end: float) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.clip_end = clip_end

    def health(self) -> Dict[str, Any]:
        return {"ok": True, "screen": "home", "appVersion": "x"}

    def call_ok(self, tool: str, arguments: Dict[str, Any]) -> Any:
        self.calls.append((tool, arguments))
        if tool == "newProject":
            return {"ok": True, "id": "p-9"}
        if tool == "importMedia":
            return {"ok": True, "id": "clip-1"}
        if tool == "clips":
            return {"ok": True, "clips": [{"id": "clip-1", "endTime": self.clip_end}]}
        return {"ok": True}


@pytest.mark.parametrize(("clip_end", "code"), [(20.0, 0), (25.0, 1)])
def test_main_run_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clip_end: float, code: int) -> None:
    movies = tmp_path / "Movies"
    movies.mkdir()
    reel = movies / "reel.mp4"
    reel.write_bytes(b"x")
    fake = FakeClient(clip_end)
    monkeypatch.setattr(import_reel, "movies_dir", lambda: movies.resolve())
    monkeypatch.setattr(import_reel, "EditsClient", lambda: fake)
    assert import_reel.main([str(reel), "--expect-duration", "20"]) == code
    tools = [tool for tool, _ in fake.calls]
    assert tools == ["newProject", "renameProject", "setProjectDynamicRange", "setCanvas", "importMedia", "clips"]
    assert fake.calls[1][1]["id"] == "p-9"
    assert not {"deleteProject", "closeProject", "openProject"} & set(tools)


def test_dry_run_touches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    movies = tmp_path / "Movies"
    movies.mkdir()
    reel = movies / "reel.mp4"
    reel.write_bytes(b"x")
    monkeypatch.setattr(import_reel, "movies_dir", lambda: movies.resolve())

    def no_client() -> None:
        raise AssertionError("dry run must not connect")

    monkeypatch.setattr(import_reel, "EditsClient", no_client)
    assert import_reel.main([str(reel), "--expect-duration", "20", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "newProject {}" and "importMedia" in out


def test_refuses_file_outside_movies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_reel, "movies_dir", lambda: (tmp_path / "Movies").resolve())
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x")
    assert import_reel.main([str(reel), "--expect-duration", "20", "--dry-run"]) == 2
