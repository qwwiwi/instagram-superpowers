#!/usr/bin/env python3
"""Import a finished reel into Edits as a new project and check the timeline length.

Calls, in this order: ``health`` -> ``newProject`` -> ``renameProject`` ->
``setProjectDynamicRange`` (sdr) -> ``setAspectRatio`` (9:16) -> ``setCanvas``
(30 fps) -> ``importMedia``
-> ``clips``. A new project starts in HDR at 60 fps, so the SDR and 30 fps calls
come before the import. Nothing is cut, deleted or published; no other project is
opened, closed or touched.

Example::

    import_reel.py ~/Movies/my-reel/reel.mp4 --expect-duration 41.23 --project-name "<название проекта>"
    import_reel.py ~/Movies/my-reel/reel.mp4 --expect-duration 41.23 --dry-run

Exit codes: 0 imported and the length matches, 1 length mismatch, 2 refused or
a connection problem.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from edits_client import EditsClient, EditsError

LOG = logging.getLogger("edits.import_reel")

ALLOWED_SUFFIXES = (".mp4", ".mov")
DURATION_TOLERANCE = 0.15
FRAME_RATE = 30
ID_PLACEHOLDER = "<project id from newProject>"


def movies_dir() -> Path:
    """The only folder imports were verified from (``~/Movies``)."""
    return (Path.home() / "Movies").resolve()


def check_source(raw: str, movies: Optional[Path] = None) -> Path:
    """Validate the file to import.

    Args:
        raw: Path from the command line (``~`` allowed).
        movies: The allowed root (default ``~/Movies``); for tests.

    Returns:
        The absolute, resolved path (``importMedia`` needs an absolute path without ``~``).

    Raises:
        ValueError: Missing file, wrong extension, or outside the allowed folder.
    """
    root = (movies or movies_dir()).resolve()
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"Expected an .mp4 or .mov file: {path}")
    if root not in path.parents:
        raise ValueError(f"Put the file under {root} first (imports from other folders were not tested): {path}")
    if not path.is_file():
        raise ValueError(f"No such file: {path}")
    return path


def plan_calls(path: Path, project_name: str, project_id: str = ID_PLACEHOLDER) -> List[Tuple[str, Dict[str, Any]]]:
    """The tool calls after ``newProject``, in order.

    Args:
        path: Absolute path of the reel.
        project_name: Name of the new project.
        project_id: Id returned by ``newProject``.

    Returns:
        ``(tool, arguments)`` pairs.

    Raises:
        ValueError: Empty project name.
    """
    name = project_name.strip()
    if not name:
        raise ValueError("project name is empty")
    return [
        ("renameProject", {"id": project_id, "name": name}),
        ("setProjectDynamicRange", {"range": "sdr"}),
        ("setAspectRatio", {"ratio": "9:16"}),
        ("setCanvas", {"frameRate": FRAME_RATE}),
        ("importMedia", {"path": str(path)}),
    ]


def timeline_length(clips_payload: Any) -> float:
    """Largest ``endTime`` of the ``clips`` answer (0 when there are no clips).

    Args:
        clips_payload: ``{"clips": [...]}`` or a bare list of clip rows.

    Returns:
        Length of the main lane in seconds.
    """
    rows = clips_payload.get("clips", []) if isinstance(clips_payload, dict) else clips_payload
    if not isinstance(rows, list):
        return 0.0
    ends = [float(row["endTime"]) for row in rows if isinstance(row, dict) and "endTime" in row]
    return max(ends, default=0.0)


def length_problem(actual: float, expected: float, tolerance: float = DURATION_TOLERANCE) -> Optional[str]:
    """Compare the timeline length with the frozen length of the file.

    Returns:
        A problem line, or None when they match within the tolerance.
    """
    if abs(actual - expected) > tolerance:
        return f"timeline is {actual:.2f} s, the file is {expected:.2f} s (more than {tolerance} s apart)"
    return None


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments, import, check. Returns the exit code."""
    parser = argparse.ArgumentParser(description="Import a finished reel into Edits as a new project")
    parser.add_argument("file", help="the finished reel under ~/Movies")
    parser.add_argument("--expect-duration", type=float, required=True, help="frozen length from check_reel.py, seconds")
    parser.add_argument("--project-name", help="name of the new project (default: the file name)")
    parser.add_argument("--dry-run", action="store_true", help="print the calls, change nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        path = check_source(args.file)
        calls = plan_calls(path, args.project_name or path.stem)
    except ValueError as exc:
        LOG.error("REFUSED: %s", exc)
        return 2
    if args.dry_run:
        print("newProject {}")
        for tool, arguments in calls:
            print(f"{tool} {json.dumps(arguments, ensure_ascii=False)}")
        print("clips {}")
        return 0
    client = EditsClient()
    try:
        health = client.health()
        LOG.info("Edits %s, screen %s", health.get("appVersion"), health.get("screen"))
        created = client.call_ok("newProject", {})
        project_id = str(created.get("id", "")) if isinstance(created, dict) else ""
        if not project_id:
            LOG.error("newProject returned no id: %s", created)
            return 2
        for tool, arguments in plan_calls(path, args.project_name or path.stem, project_id):
            answer = client.call_ok(tool, arguments)
            LOG.info("%s: ok", tool)
            if tool == "importMedia" and isinstance(answer, dict):
                print(f"clip id: {answer.get('id')}")
        actual = timeline_length(client.call_ok("clips", {}))
    except EditsError as exc:
        LOG.error("%s", exc)
        return 2
    print(f"project id: {project_id}")
    print(f"timeline: {actual:.2f} s, file: {args.expect_duration:.2f} s")
    problem = length_problem(actual, args.expect_duration)
    if problem:
        print(f"PROBLEM: {problem}")
        print("VERDICT: FAIL")
        return 1
    print("VERDICT: PASS (now capture frames and look at them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
