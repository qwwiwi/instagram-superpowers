"""Guards on the numbers and commands written in the stage 2 reference documents.

They are cheap text checks that keep the documents consistent with the scripts that verify the result:
a copied command must produce a file that ``verify.py`` accepts, and the palette must meet rule 6.5.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

REFERENCES = Path(__file__).resolve().parent.parent / "references"
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def _luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of two ``#RRGGBB`` colours."""
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _shell_commands(text: str) -> list[list[str]]:
    """Commands of the fenced ``bash`` blocks, continuation lines joined."""
    commands: list[list[str]] = []
    for block in re.findall(r"```bash\n(.*?)```", text, flags=re.S):
        joined = block.replace("\\\n", " ")
        for line in joined.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "<<" in line:
                continue
            try:
                commands.append(shlex.split(line))
            except ValueError:
                continue
    return commands


def test_every_ffmpeg_command_that_writes_the_final_reel_sets_faststart() -> None:
    # verify.py rejects an mp4 whose moov atom comes after mdat; a copied skeleton must pass it.
    text = (REFERENCES / "layout-patterns.md").read_text(encoding="utf-8")
    final = [c for c in _shell_commands(text) if c and c[0] == "ffmpeg" and c[-1] == "reel.mp4"]
    assert final, "no ffmpeg command writes reel.mp4 in layout-patterns.md"
    for command in final:
        assert "-movflags" in command and command[command.index("-movflags") + 1] == "+faststart", " ".join(command)


def test_palette_text_colours_reach_4_5_on_their_backgrounds() -> None:
    text = (REFERENCES / "montage-reglament.md").read_text(encoding="utf-8")
    section = text.split("**6.1. Палитра по умолчанию**", 1)[1].split("**6.2.", 1)[0]
    rows = {cells[0]: cells[1] for line in section.splitlines()
            if line.startswith("|") and len(cells := [c.strip() for c in line.strip("|").split("|")]) >= 2}

    def first_hex(role: str) -> str:
        return HEX.findall(next(v for k, v in rows.items() if k.startswith(role)))[0]

    backgrounds = {
        "тёмном": [first_hex("Фон тёмный"), first_hex("Карточка тёмная")],
        "акцентном": [first_hex("Фон акцентный")],
        "светлом": [first_hex("Фон светлый"), first_hex("Карточка светлая")],
    }
    checked = 0
    for label, value in rows.items():
        if not label.startswith("Текст на"):
            continue
        fills = [bg for key, bgs in backgrounds.items() if key in label for bg in bgs]
        assert fills, f"unknown background in the palette row {label!r}"
        for color in HEX.findall(re.sub(r"\([^)]*\)", "", value)):  # notes in brackets are not colours
            for bg in fills:
                assert contrast(color, bg) >= 4.5, f"{color} on {bg} in {label!r}: {contrast(color, bg):.2f}"
                checked += 1
    assert checked >= 6
