#!/usr/bin/env python3
"""Save frames of the open Edits project as JPEG files, for the visual check.

Uses the ``captureFrame`` tool (the exact frame the app would render). One call
per time, so every file name maps to a known second. With ``--zones`` a copy of
each frame gets the Instagram interface zones drawn on it (ffmpeg needed):
red bands y 0-115 and y 1520-1920, the button column x 950-1080 at y 1080-1720
(fractions of 1080x1920, so any frame width works).

Examples::

    capture_frames.py --times 0,1.5,5,10 --out ~/Movies/my-reel/frames --zones
    capture_frames.py --count 6 --out ~/Movies/my-reel/frames
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from edits_client import EditsClient, EditsError

LOG = logging.getLogger("edits.capture_frames")

FRAME_W = 1080
FRAME_H = 1920
# Zones covered by the Instagram interface, in pixels of a 1080x1920 frame.
TOP_BAND = (0, 115)
BOTTOM_BAND = (1520, 1920)
BUTTON_COLUMN = (950, 1080, 1080, 1720)  # x0, x1, y0, y1


def parse_times(raw: str) -> List[float]:
    """Parse ``0,1.5,10`` into seconds.

    Raises:
        ValueError: A part is not a number, is negative, or nothing is given.
    """
    times = [float(part) for part in raw.split(",") if part.strip()]
    if not times:
        raise ValueError("no times given")
    if any(t < 0 for t in times):
        raise ValueError("times must not be negative")
    return times


def evenly_spaced(duration: float, count: int) -> List[float]:
    """Return ``count`` times spread over the timeline, away from the very last frame.

    Args:
        duration: Timeline length in seconds.
        count: How many frames.

    Returns:
        Times in seconds, rounded to 2 decimals.
    """
    if count <= 1:
        return [0.0]
    last = max(0.0, duration - 0.1)
    return [round(last * i / (count - 1), 2) for i in range(count)]


def zones_filter() -> str:
    """ffmpeg filter that paints the interface zones as fractions of the frame size."""
    top = TOP_BAND[1] / FRAME_H
    bottom = BOTTOM_BAND[0] / FRAME_H
    x0, x1, y0, y1 = BUTTON_COLUMN
    return ",".join([
        f"drawbox=x=0:y=0:w=iw:h=ih*{top:.4f}:color=red@0.35:t=fill",
        f"drawbox=x=0:y=ih*{bottom:.4f}:w=iw:h=ih*{1 - bottom:.4f}:color=red@0.35:t=fill",
        f"drawbox=x=iw*{x0 / FRAME_W:.4f}:y=ih*{y0 / FRAME_H:.4f}:w=iw*{(x1 - x0) / FRAME_W:.4f}"
        f":h=ih*{(y1 - y0) / FRAME_H:.4f}:color=orange@0.35:t=fill",
    ])


def draw_zones(frame: Path) -> Path:
    """Write ``<frame>_zones.jpg`` with the interface zones painted on it.

    Raises:
        EditsError: ffmpeg is missing or fails.
    """
    if not shutil.which("ffmpeg"):
        raise EditsError("ffmpeg not found; cannot draw the zones")
    target = frame.with_name(f"{frame.stem}_zones.jpg")
    done = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(frame), "-vf", zones_filter(), str(target)],
        capture_output=True, text=True, check=False,
    )
    if done.returncode != 0:
        raise EditsError(f"ffmpeg failed: {done.stderr.strip()[:300]}")
    return target


def capture_jpeg(client: EditsClient, second: float, max_width: int = 540) -> Optional[bytes]:
    """Capture one frame with ``captureFrame``.

    Returns:
        JPEG bytes, or None when the tool returned an error or no image.
    """
    outcome = client.call("captureFrame", {"times": f"{second:g}", "maxWidth": max_width})
    if outcome.is_error or not outcome.images:
        LOG.error("No image for t=%s: %s", second, str(outcome.data)[:200])
        return None
    return outcome.images[0]


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments, capture, save. Returns the exit code."""
    parser = argparse.ArgumentParser(description="Save frames of the open Edits project as JPEG")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--times", help="comma-separated timeline seconds, e.g. 0,2.5,10")
    group.add_argument("--count", type=int, help="N frames evenly spaced over the timeline")
    parser.add_argument("--out", type=Path, required=True, help="folder for the JPEG files (created)")
    parser.add_argument("--prefix", default="frame", help="file name prefix (default frame)")
    parser.add_argument("--max-width", type=int, default=540, help="max frame width in px (default 540)")
    parser.add_argument("--zones", action="store_true", help="also write a copy with the interface zones drawn")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    client = EditsClient()
    try:
        if args.times:
            times = parse_times(args.times)
        else:
            state = client.call_ok("clips", {"fields": ["endTime"]})
            rows = state.get("clips", []) if isinstance(state, dict) else state
            times = evenly_spaced(max((float(r["endTime"]) for r in rows), default=0.0), args.count)
        out_dir = args.out.expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for second in times:
            image = capture_jpeg(client, second, args.max_width)
            if image is None:
                continue
            path = out_dir / f"{args.prefix}_{second:g}s.jpg"
            path.write_bytes(image)
            print(path)
            if args.zones:
                print(draw_zones(path))
            saved += 1
        if saved != len(times):
            LOG.error("Saved %d of %d frames", saved, len(times))
            return 1
    except (EditsError, ValueError, KeyError) as exc:
        LOG.error("%s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
