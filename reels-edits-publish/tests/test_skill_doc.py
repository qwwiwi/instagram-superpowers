"""SKILL.md stays in step with the scripts: every script listed, every forbidden tool named."""

from __future__ import annotations

from pathlib import Path

from edits_client import FORBIDDEN_TOOLS

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_MD = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def test_front_matter() -> None:
    head = SKILL_MD.split("---")[1]
    assert "name: reels-edits-publish" in head
    assert "description:" in head


def test_every_script_is_documented() -> None:
    for script in sorted((SKILL_DIR / "scripts").iterdir()):
        if script.suffix in (".py", ".sh"):
            assert f"scripts/{script.name}" in SKILL_MD, script.name


def test_every_forbidden_tool_is_named() -> None:
    for tool in FORBIDDEN_TOOLS:
        assert f"`{tool}`" in SKILL_MD, tool


def test_confirm_rule_is_documented() -> None:
    assert "--confirm-file" in SKILL_MD
    assert "не проверено" in SKILL_MD.lower()
