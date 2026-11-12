#!/usr/bin/env python3
"""One human-like mouse action in Edits, only for a reel the human confirmed by name.

Every mouse action of the publishing step goes through this script. It refuses
to move the mouse unless ``--confirm-file`` is exactly the file name of
``--file`` (the human's word was «да, публикуй <name>» for THIS file), the file
exists, and its name does not look like a test file.

Coordinates are read from a screenshot (``shot.sh``): pixels of that image. On a
Retina display one screen point is 2 image pixels. Pass ``--scale`` = image width
in pixels / width in points of what was captured: the whole screen width for a
full-screen shot, the region width (``w`` of ``x,y,w,h``) for a region shot, plus
the region origin (in points) with ``--origin``. The cursor moves with easing (``cliclick -e``,
acceleration and braking, never a jump), then the script waits 0.3-0.6 s.

Examples::

    smooth_click.py --file ~/Movies/my-reel/reel.mp4 --confirm-file reel.mp4 \\
        --step "навести на кнопку" --x 2410 --y 96 --scale 2 --action move
    smooth_click.py --file ~/Movies/my-reel/reel.mp4 --confirm-file reel.mp4 \\
        --step "нажать кнопку" --x 2410 --y 96 --scale 2 --dry-run

Exit codes: 0 done (or dry run), 2 refused.
"""

from __future__ import annotations

import argparse
import logging
import platform
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

LOG = logging.getLogger("edits.smooth_click")

DEFAULT_EASING = 200
ALLOWED_SUFFIXES = (".mp4", ".mov")
# A name with one of these parts is a test or a draft, never a reel to publish.
TEST_NAME = re.compile(r"(test|тест|проб|sample|dummy|smoke|draft|чернов)", re.IGNORECASE)
ACTIONS = ("move", "click")


class Refused(ValueError):
    """The action is not allowed; the message says why."""


@dataclass(frozen=True)
class Point:
    """A screen point in cliclick coordinates."""

    x: int
    y: int


def check_confirmation(file_arg: str, confirm_file: Optional[str]) -> Path:
    """Check the human's confirmation against the file being published.

    Args:
        file_arg: Path of the reel (``~`` allowed).
        confirm_file: The file name the human said yes to.

    Returns:
        The resolved path of the reel.

    Raises:
        Refused: No confirmation, a different name, a missing file, a wrong
            extension, or a name that looks like a test file.
    """
    if not confirm_file:
        raise Refused("no --confirm-file: the human must name the file they want published")
    path = Path(file_arg).expanduser().resolve()
    if confirm_file != path.name:
        raise Refused(f"--confirm-file {confirm_file!r} is not the file being published ({path.name!r})")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise Refused(f"not a video file: {path.name}")
    if TEST_NAME.search(path.name):
        raise Refused(f"{path.name!r} looks like a test file or a draft; test files and drafts are never published")
    if not path.is_file():
        raise Refused(f"no such file: {path}")
    return path


def parse_origin(raw: Optional[str]) -> Tuple[float, float]:
    """Parse ``X,Y`` (points) of a region screenshot; ``0,0`` when not given.

    Raises:
        Refused: Not two non-negative numbers.
    """
    if not raw:
        return (0.0, 0.0)
    parts = raw.split(",")
    try:
        ox, oy = (float(part) for part in parts)
    except ValueError:
        raise Refused(f"--origin must be X,Y: {raw!r}") from None
    if ox < 0 or oy < 0:
        raise Refused("--origin must not be negative")
    return (ox, oy)


def to_screen_point(px: float, py: float, scale: float, origin: Tuple[float, float] = (0.0, 0.0)) -> Point:
    """Convert screenshot pixels to screen points for cliclick.

    Args:
        px: X in pixels of the screenshot.
        py: Y in pixels of the screenshot.
        scale: Image pixels per screen point (1 on a plain display, 2 on Retina).
        origin: Top-left corner of the screenshot region, in points.

    Returns:
        The rounded screen point.

    Raises:
        Refused: Negative pixels or a non-positive scale.
    """
    if scale <= 0:
        raise Refused("--scale must be positive")
    if px < 0 or py < 0:
        raise Refused("coordinates must not be negative")
    return Point(round(origin[0] + px / scale), round(origin[1] + py / scale))


def cliclick_command(action: str, point: Point, easing: int = DEFAULT_EASING) -> List[str]:
    """Build the cliclick command for one eased move or click.

    Args:
        action: ``move`` or ``click``.
        point: Screen point.
        easing: cliclick easing (higher is slower); must be positive, never a jump.

    Returns:
        The argument list.

    Raises:
        Refused: Unknown action or no easing.
    """
    if action not in ACTIONS:
        raise Refused(f"unknown action {action!r}")
    if easing <= 0:
        raise Refused("easing must be positive: the cursor never jumps")
    verb = "m" if action == "move" else "c"
    return ["cliclick", "-e", str(easing), f"{verb}:{point.x},{point.y}"]


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments, check the guards, act. Returns the exit code."""
    parser = argparse.ArgumentParser(description="One eased mouse move or click, only for a confirmed reel")
    parser.add_argument("--file", required=True, help="the reel being published")
    parser.add_argument("--confirm-file", help="file name the human confirmed; must equal the name of --file")
    parser.add_argument("--step", required=True, help="what this action does, for the log")
    parser.add_argument("--x", type=float, required=True, help="x in screenshot pixels")
    parser.add_argument("--y", type=float, required=True, help="y in screenshot pixels")
    parser.add_argument("--scale", type=float, required=True, help="screenshot pixels per screen point (2 on Retina)")
    parser.add_argument("--origin", help="X,Y of a region screenshot in points (default 0,0)")
    parser.add_argument("--action", choices=ACTIONS, default="click")
    parser.add_argument("--easing", type=int, default=DEFAULT_EASING, help="cliclick easing (default 200)")
    parser.add_argument("--dry-run", action="store_true", help="check and print the command, do not touch the mouse")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        path = check_confirmation(args.file, args.confirm_file)
        if not args.step.strip():
            raise Refused("--step is empty")
        point = to_screen_point(args.x, args.y, args.scale, parse_origin(args.origin))
        command = cliclick_command(args.action, point, args.easing)
    except Refused as exc:
        LOG.error("REFUSED: %s", exc)
        return 2
    LOG.info("%s: %s %s at %d,%d for %s", "dry run" if args.dry_run else "acting", args.step, args.action, point.x, point.y, path.name)
    print(" ".join(command))
    if args.dry_run:
        return 0
    if platform.system() != "Darwin" or not shutil.which("cliclick"):
        LOG.error("REFUSED: needs macOS with cliclick installed (brew install cliclick)")
        return 2
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        LOG.error("cliclick failed (exit %d): %s; check Accessibility for the terminal", done.returncode, done.stderr.strip()[:200])
        return 2
    time.sleep(random.uniform(0.3, 0.6))
    LOG.info("done; take a new screenshot and confirm the effect before the next step")
    return 0


if __name__ == "__main__":
    sys.exit(main())
