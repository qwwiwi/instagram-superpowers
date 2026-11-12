#!/usr/bin/env python3
"""Phrase table from word timestamps: what the agent reads to find repeats.

Usage:
    phrases.py words.json [--out phrases.json] [--table phrases.md]

A phrase is speech between pauses of ``--min-pause`` seconds and longer. A
phrase shorter than ``--min-phrase`` joins its neighbour. 0.15 s of silence is
kept on each side of a phrase, never more than half of the silence. The script
only gives facts; which phrase is a repeat or an abandoned take is judged by the
agent and confirmed by the student.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common import (
    JSON_VERSION,
    MontageError,
    Word,
    load_words,
    read_json,
    sanitize_words,
    setup_logging,
    write_json,
)

LOG = logging.getLogger("reels.phrases")

# Pauses of this length and longer separate phrases.
DEFAULT_MIN_PAUSE = 0.32
# A phrase shorter than this joins a neighbour.
DEFAULT_MIN_PHRASE = 0.6
# Silence kept around a phrase, never more than half of the silence.
DEFAULT_PAD = 0.15


@dataclass
class Phrase:
    """One phrase of the take."""

    id: int
    words: list[Word] = field(default_factory=list)
    pause_before: float = 0.0
    pad_before: float = 0.0
    pad_after: float = 0.0

    @property
    def core_start(self) -> float:
        """Start of the first word."""
        return self.words[0].start

    @property
    def core_end(self) -> float:
        """End of the last word."""
        return self.words[-1].end

    @property
    def start(self) -> float:
        """Start including the kept silence."""
        return max(0.0, self.core_start - self.pad_before)

    @property
    def end(self) -> float:
        """End including the kept silence."""
        return self.core_end + self.pad_after

    @property
    def text(self) -> str:
        """The words joined with spaces."""
        return " ".join(w.text for w in self.words)

    def to_dict(self) -> dict[str, Any]:
        """JSON form of the phrase."""
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "core_start": round(self.core_start, 3),
            "core_end": round(self.core_end, 3),
            "pause_before": round(self.pause_before, 3),
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }


def build_phrases(
    words: list[Word],
    min_pause: float = DEFAULT_MIN_PAUSE,
    min_phrase: float = DEFAULT_MIN_PHRASE,
    pad: float = DEFAULT_PAD,
) -> list[Phrase]:
    """Group words into phrases.

    Args:
        words: Clean words in recogniser order.
        min_pause: A gap between two words of this length and longer starts a new phrase.
        min_phrase: A phrase whose speech is shorter than this joins the previous one
            (the first one joins the next one).
        pad: Silence kept on each side of a phrase, seconds.

    Returns:
        Phrases numbered from 1, in time order.
    """
    groups: list[list[Word]] = []
    for word in words:
        if groups and word.start - groups[-1][-1].end < min_pause:
            groups[-1].append(word)
        else:
            groups.append([word])
    index = 0
    while len(groups) > 1 and index < len(groups):
        length = groups[index][-1].end - groups[index][0].start
        if length >= min_phrase:
            index += 1
        elif index > 0:
            groups[index - 1].extend(groups[index])
            del groups[index]
        else:
            groups[1] = groups[0] + groups[1]
            del groups[0]
    phrases = [Phrase(id=n, words=g) for n, g in enumerate(groups, 1)]
    for pos, phrase in enumerate(phrases):
        previous_end = phrases[pos - 1].core_end if pos else 0.0
        phrase.pause_before = max(0.0, phrase.core_start - previous_end)
        phrase.pad_before = min(pad, phrase.pause_before / 2) if pos else min(pad, phrase.core_start)
        if pos + 1 < len(phrases):
            phrase.pad_after = min(pad, max(0.0, phrases[pos + 1].core_start - phrase.core_end) / 2)
        else:
            phrase.pad_after = pad
    return phrases


def format_table(phrases: list[Phrase]) -> str:
    """Readable markdown table: id, times, length, pause before, text."""
    lines = [
        "| # | start | end | len | pause before | text |",
        "|--:|------:|----:|----:|-------------:|------|",
    ]
    for p in phrases:
        text = p.text.replace("|", "/")
        lines.append(
            f"| {p.id} | {p.core_start:.2f} | {p.core_end:.2f} | {p.core_end - p.core_start:.2f} "
            f"| {p.pause_before:.2f} | {text} |"
        )
    return "\n".join(lines)


def phrases_payload(phrases: list[Phrase], duration: float, language: str, min_pause: float, min_phrase: float) -> dict[str, Any]:
    """The ``phrases.json`` document."""
    return {
        "version": JSON_VERSION,
        "duration": round(duration, 3),
        "language": language,
        "min_pause": min_pause,
        "min_phrase": min_phrase,
        "phrases": [p.to_dict() for p in phrases],
    }


def load_phrase_items(path: Path) -> list[dict[str, Any]]:
    """Read the ``phrases`` list of a ``phrases.json``.

    Raises:
        MontageError: The file is not a phrase table.
    """
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("phrases"), list):
        raise MontageError(f"{path} is not a phrase table (no `phrases` list)")
    for item in data["phrases"]:
        for key in ("id", "core_start", "core_end", "text"):
            if key not in item:
                raise MontageError(f"{path}: a phrase has no `{key}`")
    return list(data["phrases"])


def build_parser() -> argparse.ArgumentParser:
    """Command line of ``phrases.py``."""
    parser = argparse.ArgumentParser(description="Phrase table (phrases.json + markdown) from words.json")
    parser.add_argument("words", type=Path, help="words.json from transcribe.py")
    parser.add_argument("--out", type=Path, default=Path("phrases.json"), help="phrase table (default ./phrases.json)")
    parser.add_argument("--table", type=Path, default=Path("phrases.md"), help="readable table (default ./phrases.md)")
    parser.add_argument("--min-pause", type=float, default=DEFAULT_MIN_PAUSE, help=f"pause that separates phrases, s (default {DEFAULT_MIN_PAUSE})")
    parser.add_argument("--min-phrase", type=float, default=DEFAULT_MIN_PHRASE, help=f"shortest phrase, s (default {DEFAULT_MIN_PHRASE})")
    parser.add_argument("--pad", type=float, default=DEFAULT_PAD, help=f"silence kept around a phrase, s (default {DEFAULT_PAD})")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        wf = load_words(args.words)
        words, dropped = sanitize_words(wf.words, wf.duration)
        if dropped:
            LOG.info("dropped %d empty or invented word(s)", dropped)
        if not words:
            raise MontageError("No words in the file, nothing to build")
        phrases = build_phrases(words, args.min_pause, args.min_phrase, args.pad)
        write_json(args.out, phrases_payload(phrases, wf.duration, wf.language, args.min_pause, args.min_phrase))
        table = format_table(phrases)
        args.table.parent.mkdir(parents=True, exist_ok=True)
        args.table.write_text(table + "\n", encoding="utf-8")
    except MontageError as exc:
        LOG.error("%s", exc)
        return 2
    print(table)
    print(f"\n{len(phrases)} phrases -> {args.out}, {args.table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
