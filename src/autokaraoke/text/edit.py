"""Editing a line's text in "syllable notation": words split by spaces, syllables by '|'."""

from __future__ import annotations

from autokaraoke.align.naive import time_line
from autokaraoke.project import Line, Syllable, Word
from autokaraoke.text.syllabify import syllabify_line


def to_notation(line: Line) -> str:
    return " ".join("|".join(s.text for s in w.syllables) for w in line.words)


def retext_line(line: Line, notation: str, lang: str) -> None:
    """Replace the line's words/syllables, keeping timings when the syllable count matches.

    Without any '|' in ``notation`` the words are syllabified automatically.
    """
    if "|" in notation:
        parsed = [[p for p in w.split("|") if p] for w in notation.split()]
    else:
        parsed = syllabify_line(notation, lang)
    parsed = [w for w in parsed if w]
    old = line.syllables()
    start = old[0].start if old and old[0].start is not None else line.start
    end = old[-1].end if old and old[-1].end is not None else line.end

    line.words = [
        Word(text="".join(sylls), syllables=[Syllable(text=s) for s in sylls]) for sylls in parsed
    ]
    line.text = " ".join(w.text for w in line.words)
    new = line.syllables()
    if len(new) == len(old):
        for n, o in zip(new, old):
            n.start, n.end, n.confidence = o.start, o.end, o.confidence
    elif start is not None:
        line.start, line.end = start, end
        time_line(line)
        for s in new:
            s.confidence = 0.2
