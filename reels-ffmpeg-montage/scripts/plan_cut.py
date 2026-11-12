#!/usr/bin/env python3
"""Cut plan: which stretches of the take are removed.

Usage:
    plan_cut.py words.json [--phrases phrases.json] [--drop 3,4] [--speed 1.3] [--out cut-plan.json]

What the script decides by itself (mechanics only):
  * pauses longer than ``--min-pause`` are trimmed to ``--target-gap``;
  * the edges of the reel come from the word stamps with a safety pad: the cut is ``--head-air``
    (0.15 s) before the first word's start and ``--tail-air`` (0.25 s) after the last word's end, in
    output seconds. The sound energy never moves an edge inward (into a stamp); it moves an edge OUTWARD
    only when speech energy runs on outside the padded words (``cut_edges``, ``speech_edges`` in the plan).

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

from audio import Span, analyse_audio
from common import (
    JSON_VERSION,
    MIN_CUT,
    MIN_KEEP,
    MontageError,
    OUT_FPS,
    Word,
    fit_words_to_edges,
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
# Air at each edge of the reel: at most EDGE_LIMIT seconds of OUTPUT (after the speed change and the frame
# grid), PASS when air <= EDGE_LIMIT. The pads are given in output seconds; the snapping to the frame grid adds
# up to one source frame (1 / (fps * speed) output seconds), so a pad is cut down until pad + one frame fits.
EDGE_LIMIT = 0.30
# verify.py measures the edges on a 10 ms loudness envelope; the plan stays this far inside the limit.
EDGE_MEASURE_SLACK = 0.01
DEFAULT_HEAD_AIR = 0.15
DEFAULT_TAIL_AIR = 0.25
# Speech energy that starts (ends) at most this far outside an edge word moves the edge outward, source s.
# A sound running on further than this is room noise or music, not a phoneme: the stamp is trusted then.
EDGE_REACH = 0.6
# Stamps lying wholly in silence: the voice nearest to them, at most this far away, is the speech, source s.
STALE_REACH = 1.5
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


def speech_edges(words: list[Word], quiet: list[Span] | None, duration: float | None = None,
                 pad_before: float = 0.0, pad_after: float = 0.0) -> tuple[float, float]:
    """Where the speech starts and ends: the word stamps, widened (never narrowed) by speech energy.

    The edges are the first word's start and the last word's end. The quiet spans of the audio may only
    move them OUTWARD:

    * a sound that overlaps the padded words and starts before the first word (ends after the last word)
      by at most ``EDGE_REACH`` is speech the stamp missed: the edge goes to its start (end);
    * when no sound at all overlaps the padded words (stale stamps, typical of a one-word answer stamped
      in the silence next to its voice), the nearest sound within ``STALE_REACH`` is the speech and the
      span grows to take it in.

    Without energy data the word times are used as they are.

    Args:
        words: Clean words, time order, at least one.
        quiet: Quiet spans of the source audio (pause threshold), or None.
        duration: Source length; the end of a sound that runs to the end of the file.
        pad_before: Pad before the first word, source seconds (where the cut will be).
        pad_after: Pad after the last word, source seconds.

    Returns:
        ``(onset, offset)`` in source seconds, ``onset <= words[0].start`` and ``offset >= words[-1].end``.
    """
    onset, offset = words[0].start, words[-1].end
    if quiet is None:
        return onset, offset
    low, high = max(0.0, onset - pad_before), offset + pad_after
    if duration is not None:
        high = min(high, duration)
    sounds = sounding_spans(quiet, duration)
    inside = [s for s in sounds if s.end > low + EPS and s.start < high - EPS]
    if not inside:
        near = [s for s in sounds if max(0.0, s.start - high, low - s.end) <= STALE_REACH]
        if near:
            sound = min(near, key=lambda s: max(0.0, s.start - high, low - s.end))
            return min(onset, sound.start), max(offset, sound.end)
        return onset, offset
    if inside[0].start < onset and onset - inside[0].start <= EDGE_REACH:
        onset = inside[0].start
    if inside[-1].end > offset and inside[-1].end - offset <= EDGE_REACH:
        offset = inside[-1].end
    return onset, offset


def cut_edges(words: list[Word], quiet: list[Span] | None, duration: float, pad_before: float,
              pad_after: float) -> tuple[float, float, float, float]:
    """Where the reel is cut at both ends.

    Args:
        words: Clean words, time order, at least one.
        quiet: Quiet spans of the source audio, or None.
        duration: Source length.
        pad_before: Pad before the speech, source seconds.
        pad_after: Pad after the speech, source seconds.

    Returns:
        ``(cut start, cut end, speech onset, speech offset)``: the cut is the speech span of
        :func:`speech_edges` with the pads, inside the file.
    """
    onset, offset = speech_edges(words, quiet, duration, pad_before, pad_after)
    return max(0.0, onset - pad_before), min(duration, offset + pad_after), onset, offset


def sounding_spans(quiet: list[Span], duration: float | None) -> list[Span]:
    """The stretches between the quiet spans (where something sounds), inside ``[0, duration]``."""
    end = duration if duration is not None else max((q.end for q in quiet), default=0.0)
    out: list[Span] = []
    cursor = 0.0
    for span in sorted(quiet, key=lambda q: q.start):
        if span.start - cursor > EPS:
            out.append(Span(cursor, span.start))
        cursor = max(cursor, span.end)
    if end - cursor > EPS:
        out.append(Span(cursor, end))
    return out


def edge_air(air: float, speed: float, fps: float = OUT_FPS) -> float:
    """Source seconds of pad to keep at an edge so the output shows at most ``EDGE_LIMIT`` of air.

    Args:
        air: Wanted pad in output seconds.
        speed: Speed-up factor applied after the cut.
        fps: Frame rate of the grid the pieces are snapped to (outward, up to one source frame).

    Returns:
        Source seconds, never negative.
    """
    frame_out = 1.0 / (fps * speed)
    return max(0.0, min(air, EDGE_LIMIT - EDGE_MEASURE_SLACK - frame_out)) * speed


def fit_items_to_edges(items: list[dict[str, Any]], onset: float, offset: float) -> list[dict[str, Any]]:
    """Copies of the phrase entries with every word and core clamped into ``[onset, offset]``.

    Keeps the phrase table consistent with :func:`common.fit_words_to_edges`. The edges of
    :func:`speech_edges` only widen the words, so this is a no-op for plans made by this version; it stays
    for plans and words made by older ones.
    """
    def clamp(value: float) -> float:
        return min(max(value, onset), offset)

    out: list[dict[str, Any]] = []
    for item in items:
        copy = dict(item)
        if isinstance(item.get("words"), list):
            copy["words"] = [
                {**w, "start": clamp(float(w["start"])), "end": clamp(float(w["end"]))}
                if isinstance(w, dict) and isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float))
                else w
                for w in item["words"]
            ]
        for key in ("core_start", "core_end"):
            if isinstance(item.get(key), (int, float)):
                copy[key] = clamp(float(item[key]))
        out.append(copy)
    return out


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
    """Trim long pauses between words and the non-speech at both ends.

    Args:
        words: Clean words.
        duration: Length of the source, seconds.
        min_pause: A gap at least this long is trimmed.
        target_gap: Gap left after trimming (half of it on each side).
        head_air: Pad kept before the first word, source seconds.
        tail_air: Pad kept after the last word, source seconds.
        quiet: Quiet spans of the audio, or None to trust the word times.

    Returns:
        ``(removals, notes)``; notes name the pauses that were left alone.
    """
    removals: list[Removal] = []
    notes: list[str] = []
    air = target_gap / 2
    if not words:
        return removals, notes
    start, end, _, _ = cut_edges(words, quiet, duration, head_air, tail_air)
    # Everything before the padded speech goes, quiet or not (a hand reaching for the camera, a breath).
    # The edge limit wins over MIN_CUT: even a sliver of non-speech over the pad is cut.
    if start > EPS:
        removals.append(Removal(0.0, start, "head", note="non-speech before the first word"))
    for left, right in zip(words, words[1:]):
        gap = right.start - left.end
        if gap < min_pause:
            continue
        cut = trim_region(Span(left.end, right.start), air, air, quiet)
        if cut:
            removals.append(Removal(cut.start, cut.end, "pause", note=f"{gap:.2f} s pause after «{left.text}»"))
        else:
            notes.append(f"pause {gap:.2f} s at {left.end:.2f} s left alone: the sound there is not quiet")
    if duration - end > EPS:
        removals.append(Removal(end, duration, "tail", note="non-speech after the last word"))
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


def widen_short_pieces(removals: list[Removal], duration: float, speech: list[Span], notes: list[str]) -> list[Removal]:
    """Give a kept piece that holds speech the minimum length, taking the room from the neighbouring cuts.

    With ``--target-gap 0`` two cuts can sit right around one short word, which leaves a kept piece
    under ``MIN_KEEP``; such a piece used to be deleted together with the word.

    Args:
        removals: Sorted, merged removals.
        duration: Source length.
        speech: Stretches of kept speech.
        notes: Receives one line per widened piece.

    Returns:
        The removals, shortened where a piece was widened; removals that shrink to nothing are gone.
    """
    out = [Removal(r.start, r.end, r.kind, list(r.phrases), r.note) for r in removals]
    for i in range(len(out) + 1):
        start = out[i - 1].end if i > 0 else 0.0
        end = out[i].start if i < len(out) else duration
        length = end - start
        if length >= MIN_KEEP or not any(min(s.end, end) - max(s.start, start) > EPS for s in speech):
            continue
        need = MIN_KEEP - length + 1e-6
        left = 0.0 if i == 0 else (need if i == len(out) else need / 2)
        right = need - left
        if i > 0:
            out[i - 1].end -= left
        if i < len(out):
            out[i].start += right
        notes.append(f"kept piece {start:.2f}-{end:.2f} s holds speech and is shorter than {MIN_KEEP:g} s: it was widened to {MIN_KEEP:g} s")
    return [r for r in out if r.end - r.start > EPS]


def check_keep_covers_speech(words: list[Word], keep: list[tuple[float, float]], dropped: list[tuple[float, float]]) -> None:
    """Final check of the keep list: every word that is not dropped must be inside the kept pieces.

    Args:
        words: Clean words.
        keep: Kept pieces.
        dropped: Time ranges of the confirmed drops; their words are meant to go.

    Raises:
        PlanError: A word is outside the kept pieces, or loses more than ``COLLATERAL_TOLERANCE`` of its length.
    """
    problems: list[str] = []
    for word in words:
        middle = (word.start + word.end) / 2
        if any(a - EPS <= middle <= b + EPS for a, b in dropped):
            continue
        inside = sum(max(0.0, min(word.end, b) - max(word.start, a)) for a, b in keep)
        in_piece = any(a - EPS <= middle <= b + EPS for a, b in keep)
        missing = (word.end - word.start) - inside
        if not in_piece or missing > COLLATERAL_TOLERANCE:
            problems.append(f"«{word.text}» at {word.start:.2f}-{word.end:.2f} s")
    if problems:
        raise PlanError("The plan loses speech that is not in --drop: " + "; ".join(problems[:5])
                        + ". Nothing is written. Use a larger --target-gap, or report this take.")


def speech_spans(item: dict[str, Any]) -> list[Span]:
    """Stretches of a phrase where something is said.

    The silence between the words of a phrase is not speech: a pause inside a
    phrase is trimmed on purpose. Without a word list the whole core counts.
    """
    spans: list[Span] = []
    for word in item.get("words") or []:
        if isinstance(word, dict) and isinstance(word.get("start"), (int, float)) and isinstance(word.get("end"), (int, float)):
            spans.append(Span(float(word["start"]), float(word["end"])))
    return spans or [Span(float(item["core_start"]), float(item["core_end"]))]


def shrink_to_silence(
    removals: list[Removal], items: list[dict[str, Any]], drop: list[int], notes: list[str]
) -> list[Removal]:
    """Cut the automatic removals (pauses, head, tail) back so they never touch kept speech.

    Pause cuts sit between two words by construction, so this changes nothing for
    a consistent phrase table. When the table says there is speech where a pause
    cut was planned (an edited ``phrases.json``), the cut is made shorter instead
    of the whole plan being refused.

    Args:
        removals: Removals of kind head, pause and tail.
        items: Phrase entries.
        drop: Phrase numbers that go; their speech may be cut.
        notes: Receives one line per shortened cut.

    Returns:
        The removals, possibly shortened or split; pieces under ``MIN_CUT`` are gone.
    """
    speech = sorted(
        (s for item in items if int(item["id"]) not in drop for s in speech_spans(item)), key=lambda s: s.start
    )
    out: list[Removal] = []
    for removal in removals:
        pieces = [Span(removal.start, removal.end)]
        for s in speech:
            pieces = [q for p in pieces for q in (
                [p] if s.end <= p.start or s.start >= p.end
                else [x for x in (Span(p.start, s.start), Span(s.end, p.end)) if x.length > EPS]
            )]
        shortest = EPS if removal.kind in ("head", "tail") else MIN_CUT
        pieces = [p for p in pieces if p.length >= shortest]
        if len(pieces) != 1 or abs(pieces[0].length - (removal.end - removal.start)) > 1e-6:
            notes.append(f"{removal.kind} cut {removal.start:.2f}-{removal.end:.2f} s shortened: the phrase table has speech there")
        out += [Removal(p.start, p.end, removal.kind, list(removal.phrases), removal.note) for p in pieces]
    return out


def check_collateral(items: list[dict[str, Any]], drop: list[int], removals: list[Removal]) -> list[tuple[int, float, Removal]]:
    """Kept phrases whose speech the removals cut into.

    Only the words count, not the pauses between them.

    Returns:
        ``(phrase id, seconds of its speech inside the removal, the removal)`` over the tolerance.
    """
    hits: list[tuple[int, float, Removal]] = []
    for item in items:
        no = int(item["id"])
        if no in drop:
            continue
        spans = speech_spans(item)
        for removal in removals:
            taken = sum(max(0.0, min(s.end, removal.end) - max(s.start, removal.start)) for s in spans)
            if taken > COLLATERAL_TOLERANCE:
                hits.append((no, taken, removal))
    return hits


def collateral_message(hits: list[tuple[int, float, Removal]], items: list[dict[str, Any]]) -> str:
    """Explain a refused plan: which phrase loses speech, to which cut, and what to do."""
    by_id = {int(i["id"]): str(i["text"]) for i in items}
    lines = []
    for no, sec, removal in hits:
        text = by_id[no]
        short = text if len(text) <= 50 else text[:47] + "..."
        cause = f"the drop of phrase(s) {removal.phrases}" if removal.phrases else f"the {removal.kind} cut"
        lines.append(f"phrase {no} («{short}») loses {sec:.2f} s of speech to {cause} at {removal.start:.2f}-{removal.end:.2f} s")
    return (
        "The plan would cut into speech that is kept: " + "; ".join(lines) + ". "
        "Nothing is written. Fix: take that phrase out of --drop or add the neighbouring phrase to it "
        "(the boundary between them is not silent), or rebuild phrases.json from the current words.json."
    )


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
    fps: float = OUT_FPS,
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
        head_air: Pad before the first word, OUTPUT seconds (cut down so that, with the speed and one
            frame of snapping, it stays within ``EDGE_LIMIT``).
        tail_air: Pad after the last word, output seconds, same limit.
        quiet: Quiet spans of the source audio (pause threshold), or None.
        fps: Frame rate of the cut grid.

    Raises:
        PlanError: Bad speed, an unusable drop list, or a cut that harms a kept phrase.
    """
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise PlanError(f"--speed must be between {MIN_SPEED:g} and {MAX_SPEED:g} (ffmpeg atempo range), got {speed:g}")
    head_src, tail_src = edge_air(head_air, speed, fps), edge_air(tail_air, speed, fps)
    _, _, onset, offset = cut_edges(words, quiet, duration, head_src, tail_src)
    words = fit_words_to_edges(words, onset, offset)
    items = fit_items_to_edges(items, onset, offset)
    pauses, notes = plan_pause_removals(words, duration, min_pause, target_gap, head_src, tail_src, quiet)
    drops = plan_drop_removals(items, drop, duration, target_gap, quiet, head_src, tail_src) if drop else []
    # A drop already sets the air on both sides of its cut; a pause cut that touches it would eat that air.
    pauses = [p for p in pauses if not any(p.start < d.end + 1e-6 and d.start < p.end + 1e-6 for d in drops)]
    pauses = shrink_to_silence(pauses, items, drop, notes)
    removals = merge_removals(pauses + drops)
    kept_speech = [s for item in items if int(item["id"]) not in drop for s in speech_spans(item)]
    removals = widen_short_pieces(removals, duration, kept_speech, notes)
    hits = check_collateral(items, drop, removals)
    if hits:
        raise PlanError(collateral_message(hits, items))
    keep = keep_from_removals(removals, duration)
    validate_keep(keep, duration)
    check_keep_covers_speech(words, keep, [(r.start, r.end) for r in removals if r.kind == "drop"])
    kept = sum(b - a for a, b in keep)
    return {
        "version": JSON_VERSION,
        "source_duration": round(duration, 3),
        "params": {"min_pause": min_pause, "target_gap": target_gap, "head_air": head_air, "tail_air": tail_air,
                   "energy_check": quiet is not None},
        "speed": speed,
        "drop": sorted(set(drop)),
        "speech_edges": {"onset": round(onset, 3), "offset": round(offset, 3)},
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
    parser.add_argument("--head-air", type=float, default=DEFAULT_HEAD_AIR, help=f"pad before the first word, output s; with the frame grid never over {EDGE_LIMIT:g} s (default {DEFAULT_HEAD_AIR})")
    parser.add_argument("--tail-air", type=float, default=DEFAULT_TAIL_AIR, help=f"pad after the last word, output s; with the frame grid never over {EDGE_LIMIT:g} s (default {DEFAULT_TAIL_AIR})")
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
            quiet = analyse_audio(source).quiet
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
