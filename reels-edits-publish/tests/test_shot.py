"""Offline tests of shot.sh argument guards (it exits before taking any screenshot)."""

from __future__ import annotations

import subprocess
from pathlib import Path

SHOT = Path(__file__).resolve().parents[1] / "scripts" / "shot.sh"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SHOT), *args], capture_output=True, text=True, check=False)


def test_needs_an_output_path() -> None:
    done = run()
    assert done.returncode != 0
    assert "usage" in done.stderr or "macOS only" in done.stderr


def test_refuses_unknown_arguments(tmp_path: Path) -> None:
    out = tmp_path / "s.png"
    done = run(str(out), "--bogus")
    assert done.returncode == 2
    assert not out.exists()
