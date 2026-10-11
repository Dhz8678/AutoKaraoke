"""Fallback timing: spread syllables evenly over each line's LRC span.

Gives a usable (if rough) karaoke when no aligner is available, and a starting
point for manual sync in the editor.
"""

from __future__ import annotations

import re

from autokaraoke.project import Line

# Rough singing rate used to cap a line that is followed by a long instrumental gap.
SECONDS_PER_SYLLABLE = 0.4
LINE_TAIL = 0.5


def _weight(text: str) -> float:
    letters = len(re.findall(r"\w", text))
    return 1.0 + 0.15 * max(letters - 2, 0)


def time_line(line: Line) -> None:
    sylls = line.syllables()
    if not sylls or line.start is None:
        return
    end = line.end if line.end is not None else line.start + 3.0
    cap = line.start + len(sylls) * SECONDS_PER_SYLLABLE + LINE_TAIL
    end = min(end, max(cap, line.start + 1.0))
    weights = [_weight(s.text) for s in sylls]
    total = sum(weights)
    t = line.start
    for s, w in zip(sylls, weights):
        s.start = t
        t += (end - line.start) * w / total
        s.end = t
        s.confidence = 0.0


def time_lines(lines: list[Line]) -> None:
    for line in lines:
        time_line(line)
