#!/usr/bin/env python3
"""Cut plan: which stretches of the take are removed.

Usage:
    plan_cut.py words.json [--phrases phrases.json] [--drop 3,4] [--speed 1.3] [--out cut-plan.json]

What the script decides by itself (mechanics only):
  * pauses longer than ``--min-pause`` are trimmed to ``--target-gap``;
  * a long silence at the start / the end is trimmed to ``--head-air`` / ``--tail-air``.

What the script never decides: which words or phrases go. Repeats and false
starts are only FLAGGED in ``candidates``. The agent shows them to the student and,
after the student confirms, runs the script again with ``--drop`` listing the
confirmed phrase numbers. Nothing is dropped without that flag.

With ``--source`` (default: the video named in words.json) a cut edge is placed
only where the sound is really quiet; a pause with no quiet sound is left alone, a
dropped phrase with no quiet edge is refused (``--no-energy`` turns the check off).
"""

from __future__ import annotations

import argparse
import difflib
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from audio import Span, analyse_quiet
from common import (
    JSON_VERSION,
    MIN_CUT,
    MIN_KEEP,
    MontageError,
    Word,
    load_words,
    sanitize_words,
    setup_logging,
    tokens,
    write_json,
)
from phrases import (
    DEFAULT_MIN_PAUSE as PHRASE_MIN_PAUSE,
    DEFAULT_MIN_PHRASE,
    build_phrases,
    load_phrase_items,
)

LOG = logging.getLogger("reels.plan_cut")

DEFAULT_MIN_PAUSE = 0.4
DEFAULT_TARGET_GAP = 0.25
DEFAULT_HEAD_AIR = 0.2
DEFAULT_TAIL_AIR = 0.5
MIN_SPEED = 0.5
MAX_SPEED = 2.0
# How many phrases ahead a repeat is searched.
LOOKAHEAD = 4
REPEAT_RATIO = 0.75
PREFIX_RATIO = 0.85
# A kept phrase may lose at most this much speech to the cuts.
COLLATERAL_TOLERANCE = 0.035
# A kept piece shorter than this between two removals is joined into them.
EPS = 1e-9


class PlanError(MontageError):
    """The plan cannot be built or is not valid."""


@dataclass
class Removal:
    """A stretch of the source that is cut out."""

    start: float
    end: float
    kind: str
    phrases: list[int] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {"start": round(self.start, 3), "end": round(self.end, 3), "kind": self.kind,
                "phrases": self.phrases, "note": self.note}


def parse_drop_list(text: str) -> list[int]:
    """Parse ``3,5-7`` into ``[3, 5, 6, 7]``.

    Raises:
        PlanError: The text is not a list of numbers and ranges.
    """
    numbers: list[int] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        try:
            if "-" in part:
                low, high = part.split("-", 1)
                if int(low) > int(high):
                    raise ValueError
                numbers.extend(range(int(low), int(high) + 1))
            else:
                numbers.append(int(part))
        except ValueError:
            raise PlanError(f"Cannot read the drop list {text!r}: use phrase numbers like 3,5-7") from None
    return sorted(set(numbers))


# --- quiet helpers -----------------------------------------------------------------------------------------


def quiet_piece(region: Span, quiet: list[Span] | None) -> Span | None:
    """The longest quiet stretch inside a region (the region itself without energy data).

    Args:
        region: Time range to look in.
        quiet: Quiet spans of the audio, or None when the energy check is off.

    Returns:
        The stretch, or None when the region holds no quiet sound.
    """
    if quiet is None:
        return region
    best: Span | None = None
    for span in quiet:
        low, high = max(span.start, region.start), min(span.end, region.end)
        if high - low > EPS and (best is None or high - low > best.length):
            best = Span(low, high)
    return best


def trim_region(region: Span, air_left: float, air_right: float, quiet: list[Span] | None) -> Span | None:
    """Cut a silent region down, keeping some air on each side.

    Args:
        region: The silence between two stretches of speech (or a file edge).
        air_left: Silence kept next to the earlier speech.
        air_right: Silence kept next to the later speech.
        quiet: Quiet spans, or None.

    Returns:
        The range to remove, or None when nothing worth cutting is left.
    """
    piece = quiet_piece(region, quiet)
    if piece is None:
        return None
    # A file edge has no air to keep: the quiet piece may then reach the edge itself.
    start = piece.start + air_left if air_left else piece.start
    end = piece.end - air_right if air_right else piece.end
    if end - start < MIN_CUT:
        return None
    return Span(start, end)


# --- plan parts -------------------------------------------------------------------------------------------


def plan_pause_removals(
    words: list[Word],
    duration: float,
    min_pause: float = DEFAULT_MIN_PAUSE,
    target_gap: float = DEFAULT_TARGET_GAP,
    head_air: float = DEFAULT_HEAD_AIR,
    tail_air: float = DEFAULT_TAIL_AIR,
    quiet: list[Span] | None = None,
) -> tuple[list[Removal], list[str]]:
    """Trim long pauses between words and long silence at both ends.

    Args:
        words: Clean words.
        duration: Length of the source, seconds.
        min_pause: A gap at least this long is trimmed.
        target_gap: Gap left after trimming (half of it on each side).
        head_air: Silence kept before the first word.
        tail_air: Silence kept after the last word.
        quiet: Quiet spans of the audio, or None to trust the word times.

    Returns:
        ``(removals, notes)``; notes name the pauses that were left alone.
    """
    removals: list[Removal] = []
    notes: list[str] = []
    air = target_gap / 2
    if not words:
        return removals, notes
    lead = trim_region(Span(0.0, words[0].start), 0.0, head_air, quiet)
    if lead:
        removals.append(Removal(lead.start, lead.end, "head", note="silence before the first word"))
    for left, right in zip(words, words[1:]):
        gap = right.start - left.end
        if gap < min_pause:
            continue
        cut = trim_region(Span(left.end, right.start), air, air, quiet)
        if cut:
            removals.append(Removal(cut.start, cut.end, "pause", note=f"{gap:.2f} s pause after «{left.text}»"))
        else:
            notes.append(f"pause {gap:.2f} s at {left.end:.2f} s left alone: the sound there is not quiet")
    tail = trim_region(Span(words[-1].end, duration), tail_air, 0.0, quiet)
    if tail:
        removals.append(Removal(tail.start, tail.end, "tail", note="silence after the last word"))
    return removals, notes


def group_runs(numbers: list[int], order: list[int]) -> list[list[int]]:
    """Group phrase numbers that follow each other in ``order`` into runs."""
    position = {no: i for i, no in enumerate(order)}
    runs: list[list[int]] = []
    for no in sorted(numbers, key=lambda n: position[n]):
        if runs and position[no] == position[runs[-1][-1]] + 1:
            runs[-1].append(no)
        else:
            runs.append([no])
    return runs


def plan_drop_removals(
    items: list[dict[str, Any]],
    drop: list[int],
    duration: float,
    target_gap: float = DEFAULT_TARGET_GAP,
    quiet: list[Span] | None = None,
    head_air: float = DEFAULT_HEAD_AIR,
    tail_air: float = DEFAULT_TAIL_AIR,
) -> list[Removal]:
    """Removals for the phrases the student confirmed to drop.

    A run of neighbouring phrases is one removal. It starts in the silence after
    the kept phrase before it and ends in the silence before the kept phrase after
    it; ``target_gap / 2`` of silence stays on each side. A run at the start of
    the take begins at 0, a run at the end reaches the end of the file.

    Args:
        items: ``phrases`` entries of ``phrases.json``.
        drop: Confirmed phrase numbers.
        duration: Source length.
        target_gap: Gap left between the speech on both sides of the cut.
        quiet: Quiet spans, or None.
        head_air: Silence kept before the first kept phrase when the cut starts the take.
        tail_air: Silence kept after the last kept phrase when the cut ends the take.

    Raises:
        PlanError: A number is not in the table, everything is dropped, or an
            edge of the cut has no quiet sound (the cut would go through speech).
    """
    by_id = {int(i["id"]): i for i in items}
    unknown = [n for n in drop if n not in by_id]
    if unknown:
        raise PlanError(f"Phrase number(s) not in the table: {unknown}")
    if len(set(drop)) >= len(items):
        raise PlanError("Every phrase is in the drop list; nothing would be left")
    order = [int(i["id"]) for i in sorted(items, key=lambda i: float(i["core_start"]))]
    air = target_gap / 2
    removals: list[Removal] = []
    for run in group_runs(drop, order):
        first, last = by_id[run[0]], by_id[run[-1]]
        before = order.index(run[0]) - 1
        after = order.index(run[-1]) + 1
        if before >= 0:
            prev_end = float(by_id[order[before]]["core_end"])
            region = Span(prev_end, float(first["core_start"]))
            piece = quiet_piece(region, quiet)
            if piece is None:
                raise PlanError(
                    f"REFUSED: no quiet sound before phrase {run[0]} ({region.start:.2f}-{region.end:.2f} s); a cut there "
                    "goes through speech. Propose the neighbouring phrase instead, or use --no-energy if the room is noisy."
                )
            # Before a cut that reaches the end of the file the tail air is kept instead.
            start_air = air if after < len(order) else tail_air
            start = min(piece.start + start_air, (piece.start + piece.end) / 2)
        else:
            start = 0.0
        if after < len(order):
            next_start = float(by_id[order[after]]["core_start"])
            region = Span(float(last["core_end"]), next_start)
            piece = quiet_piece(region, quiet)
            if piece is None:
                raise PlanError(
                    f"REFUSED: no quiet sound after phrase {run[-1]} ({region.start:.2f}-{region.end:.2f} s); a cut there "
                    "goes through speech. Propose the neighbouring phrase instead, or use --no-energy if the room is noisy."
                )
            end_air = air if before >= 0 else head_air
            end = max(piece.end - end_air, (piece.start + piece.end) / 2)
        else:
            end = duration
        if end - start < MIN_CUT:
            raise PlanError(f"The cut of phrases {run} is empty after snapping ({start:.2f}-{end:.2f} s)")
        text = " / ".join(str(by_id[n]["text"]) for n in run)
        removals.append(Removal(start, end, "drop", phrases=run, note=text))
    return removals


def merge_removals(removals: list[Removal]) -> list[Removal]:
    """Sort removals and merge those that overlap or touch.

    The kind of a merged removal is ``drop`` when any part is a drop. Phrase lists
    and notes are joined.
    """
    merged: list[Removal] = []
    for item in sorted(removals, key=lambda r: (r.start, r.end)):
        if merged and item.start <= merged[-1].end + 1e-6:
            last = merged[-1]
            last.end = max(last.end, item.end)
            last.phrases = sorted(set(last.phrases + item.phrases))
            if item.kind == "drop" or last.kind == "drop":
                last.kind = "drop"
            last.note = "; ".join(n for n in (last.note, item.note) if n and n not in last.note)
        else:
            merged.append(Removal(item.start, item.end, item.kind, list(item.phrases), item.note))
    return merged


def keep_from_removals(removals: list[Removal], duration: float) -> list[tuple[float, float]]:
    """Kept pieces: everything of ``[0, duration]`` that is not removed.

    Pieces shorter than ``MIN_KEEP`` are dropped.
    """
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for removal in removals:
        if removal.start > cursor:
            keep.append((cursor, removal.start))
        cursor = max(cursor, removal.end)
    if cursor < duration:
        keep.append((cursor, duration))
    return [(a, b) for a, b in keep if b - a >= MIN_KEEP]


def check_collateral(items: list[dict[str, Any]], drop: list[int], removals: list[Removal]) -> list[tuple[int, float]]:
    """Kept phrases whose speech the removals cut into.

    Returns:
        ``(phrase id, seconds of its speech inside the removals)`` over the tolerance.
    """
    hits: list[tuple[int, float]] = []
    for item in items:
        no = int(item["id"])
        if no in drop:
            continue
        start, end = float(item["core_start"]), float(item["core_end"])
        taken = sum(max(0.0, min(end, r.end) - max(start, r.start)) for r in removals)
        if taken > COLLATERAL_TOLERANCE:
            hits.append((no, taken))
    return hits


def validate_keep(keep: list[tuple[float, float]], duration: float) -> None:
    """Check that kept pieces are sorted, inside the source and do not overlap.

    Raises:
        PlanError: The pieces are not a valid cut.
    """
    if not keep:
        raise PlanError("The plan keeps nothing")
    previous_end = 0.0
    for start, end in keep:
        if start < -EPS or end > duration + 0.05 or end - start < MIN_KEEP - 1e-6 or start < previous_end - 1e-6:
            raise PlanError(f"Invalid kept piece {start:.3f}-{end:.3f} s (source length {duration:.3f} s)")
        previous_end = end


# --- candidates ----------------------------------------------------------------------------------------------


def find_candidates(items: list[dict[str, Any]], lookahead: int = LOOKAHEAD) -> list[dict[str, Any]]:
    """Flag phrases that may be a repeat or a false start. Facts only: nothing is dropped.

    An earlier phrase is flagged when a later one (up to ``lookahead`` phrases
    ahead) says nearly the same (``repeat``), or starts with the same words and is
    longer (``false_start``). The later phrase is usually the better take, but the
    agent judges that, and the student confirms.

    Args:
        items: ``phrases`` entries.
        lookahead: How many later phrases are compared.

    Returns:
        Candidate entries with the phrase, the phrase it resembles, kind and similarity.
    """
    ordered = sorted(items, key=lambda i: float(i["core_start"]))
    toks = [tokens(str(i["text"])) for i in ordered]
    found: list[dict[str, Any]] = []
    for pos, item in enumerate(ordered):
        mine = toks[pos]
        if len(mine) < 2:
            continue
        best: tuple[float, str, int] | None = None
        for other in range(pos + 1, min(len(ordered), pos + 1 + lookahead)):
            theirs = toks[other]
            if len(theirs) < 2:
                continue
            prefix = difflib.SequenceMatcher(None, mine, theirs[: len(mine)]).ratio()
            if len(theirs) > len(mine) and prefix >= PREFIX_RATIO:
                score, kind = prefix, "false_start"
            else:
                score, kind = difflib.SequenceMatcher(None, mine, theirs).ratio(), "repeat"
                if score < REPEAT_RATIO:
                    continue
            if best is None or score > best[0]:
                best = (score, kind, other)
        if best is not None:
            score, kind, other = best
            found.append({
                "phrase": int(item["id"]),
                "kind": kind,
                "similar_to": int(ordered[other]["id"]),
                "similarity": round(score, 2),
                "text": str(item["text"]),
                "similar_text": str(ordered[other]["text"]),
            })
    return found


# --- assembling ------------------------------------------------------------------------------------------------


def make_plan(
    words: list[Word],
    duration: float,
    items: list[dict[str, Any]],
    drop: list[int],
    speed: float = 1.0,
    min_pause: float = DEFAULT_MIN_PAUSE,
    target_gap: float = DEFAULT_TARGET_GAP,
    head_air: float = DEFAULT_HEAD_AIR,
    tail_air: float = DEFAULT_TAIL_AIR,
    quiet: list[Span] | None = None,
) -> dict[str, Any]:
    """Build the cut plan document.

    Args:
        words: Clean words.
        duration: Source length.
        items: Phrase entries.
        drop: Confirmed phrase numbers (empty on the first pass).
        speed: Speed-up factor applied after the cut, 0.5-2.0.
        min_pause: Pauses at least this long are trimmed.
        target_gap: Gap left after trimming.
        head_air: Silence kept before the first word.
        tail_air: Silence kept after the last word.
        quiet: Quiet spans of the source audio, or None.

    Raises:
        PlanError: Bad speed, an unusable drop list, or a cut that harms a kept phrase.
    """
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise PlanError(f"--speed must be between {MIN_SPEED:g} and {MAX_SPEED:g} (ffmpeg atempo range), got {speed:g}")
    pauses, notes = plan_pause_removals(words, duration, min_pause, target_gap, head_air, tail_air, quiet)
    drops = plan_drop_removals(items, drop, duration, target_gap, quiet, head_air, tail_air) if drop else []
    # A drop already sets the air on both sides of its cut; a pause cut that touches it would eat that air.
    pauses = [p for p in pauses if not any(p.start < d.end + 1e-6 and d.start < p.end + 1e-6 for d in drops)]
    removals = merge_removals(pauses + drops)
    hits = check_collateral(items, drop, removals)
    if hits:
        detail = ", ".join(f"phrase {no}: {sec:.2f} s" for no, sec in hits)
        raise PlanError(f"The cut would eat speech of kept phrases ({detail}). Check the word times; the plan is not written.")
    keep = keep_from_removals(removals, duration)
    validate_keep(keep, duration)
    kept = sum(b - a for a, b in keep)
    return {
        "version": JSON_VERSION,
        "source_duration": round(duration, 3),
        "params": {"min_pause": min_pause, "target_gap": target_gap, "head_air": head_air, "tail_air": tail_air,
                   "energy_check": quiet is not None},
        "speed": speed,
        "drop": sorted(set(drop)),
        "removals": [r.to_dict() for r in removals],
        "keep": [{"start": round(a, 3), "end": round(b, 3)} for a, b in keep],
        "candidates": find_candidates(items),
        "notes": notes,
        "stats": {
            "kept_seconds": round(kept, 3),
            "removed_seconds": round(duration - kept, 3),
            "output_seconds": round(kept / speed, 3),
        },
    }


def format_report(plan: dict[str, Any]) -> str:
    """Markdown report the agent shows to the student: drops, pause cuts, candidates."""
    stats = plan["stats"]
    lines = [
        f"Source {plan['source_duration']:.2f} s -> output {stats['output_seconds']:.2f} s "
        f"(removed {stats['removed_seconds']:.2f} s, speed x{plan['speed']:g})",
        "",
        "## Candidates for dropping (flagged, NOT dropped)",
        "",
    ]
    if plan["candidates"]:
        lines += ["| phrase | kind | similar to | similarity | text |", "|--:|---|--:|--:|---|"]
        for c in plan["candidates"]:
            lines.append(f"| {c['phrase']} | {c['kind']} | {c['similar_to']} | {c['similarity']} | {c['text'].replace('|', '/')} |")
    else:
        lines.append("none found")
    lines += ["", "## Removals", ""]
    if plan["removals"]:
        lines += ["| from | to | length | kind | phrases | note |", "|--:|--:|--:|---|---|---|"]
        for r in plan["removals"]:
            lines.append(
                f"| {r['start']:.2f} | {r['end']:.2f} | {r['end'] - r['start']:.2f} | {r['kind']} "
                f"| {','.join(str(n) for n in r['phrases']) or '-'} | {str(r['note']).replace('|', '/')} |"
            )
    else:
        lines.append("none")
    if plan["notes"]:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in plan["notes"]]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``plan_cut.py``."""
    parser = argparse.ArgumentParser(description="Cut plan: trim pauses, flag repeats, apply confirmed drops")
    parser.add_argument("words", type=Path, help="words.json from transcribe.py")
    parser.add_argument("--phrases", type=Path, help="phrases.json (default: built from the words)")
    parser.add_argument("--drop", default="", help="CONFIRMED phrase numbers to drop, e.g. 3,5-7 (default: none)")
    parser.add_argument("--speed", type=float, default=1.0, help="speed-up factor 0.5-2.0 applied after the cut (default 1.0)")
    parser.add_argument("--min-pause", type=float, default=DEFAULT_MIN_PAUSE, help=f"trim pauses from this length, s (default {DEFAULT_MIN_PAUSE})")
    parser.add_argument("--target-gap", type=float, default=DEFAULT_TARGET_GAP, help=f"gap left after trimming, s (default {DEFAULT_TARGET_GAP})")
    parser.add_argument("--head-air", type=float, default=DEFAULT_HEAD_AIR, help=f"silence kept before the first word, s (default {DEFAULT_HEAD_AIR})")
    parser.add_argument("--tail-air", type=float, default=DEFAULT_TAIL_AIR, help=f"silence kept after the last word, s (default {DEFAULT_TAIL_AIR})")
    parser.add_argument("--source", type=Path, help="video for the quiet-sound check (default: the one in words.json)")
    parser.add_argument("--no-energy", action="store_true", help="trust the word times, skip the quiet-sound check")
    parser.add_argument("--out", type=Path, default=Path("cut-plan.json"), help="plan file (default ./cut-plan.json)")
    parser.add_argument("--report", type=Path, default=Path("cut-plan.md"), help="readable report (default ./cut-plan.md)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        wf = load_words(args.words)
        words, _ = sanitize_words(wf.words, wf.duration)
        if not words:
            raise PlanError("No words in the file")
        if args.phrases:
            items = load_phrase_items(args.phrases)
        else:
            items = [p.to_dict() for p in build_phrases(words, PHRASE_MIN_PAUSE, DEFAULT_MIN_PHRASE)]
        quiet: list[Span] | None = None
        if not args.no_energy:
            source = args.source or (Path(wf.source) if wf.source else None)
            if source is None or not source.is_file():
                raise PlanError("The video for the quiet-sound check is not found: pass --source, or --no-energy to trust the word times")
            quiet, _ = analyse_quiet(source)
        plan = make_plan(words, wf.duration, items, parse_drop_list(args.drop), args.speed, args.min_pause,
                         args.target_gap, args.head_air, args.tail_air, quiet)
        write_json(args.out, plan)
        report = format_report(plan)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report + "\n", encoding="utf-8")
    except MontageError as exc:
        LOG.error("%s", exc)
        return 2
    print(report)
    print(f"\nplan -> {args.out}, report -> {args.report}")
    if plan["candidates"] and not plan["drop"]:
        print("Candidates are only flagged. Show them to the student; drop only what they confirm (--drop).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
