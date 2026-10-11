"""Build project lines from LRC / plain lyrics."""

from __future__ import annotations

from autokaraoke.project import Line, Syllable, Word
from autokaraoke.sources.lrclib import parse_lrc, plain_lines
from autokaraoke.text.syllabify import syllabify_line


def make_line(text: str, lang: str, start: float | None = None, end: float | None = None) -> Line:
    words = [
        Word(text="".join(sylls), syllables=[Syllable(text=s) for s in sylls])
        for sylls in syllabify_line(text, lang)
    ]
    return Line(text=text, words=words, start=start, end=end)


def lines_from_lrc(lrc: str, lang: str, duration: float) -> list[Line]:
    """Each LRC line ends where the next timestamp (sung or empty) begins."""
    entries = parse_lrc(lrc)
    lines: list[Line] = []
    for i, (t, text) in enumerate(entries):
        if not text:
            continue
        nxt = entries[i + 1][0] if i + 1 < len(entries) else duration or t + 5.0
        lines.append(make_line(text, lang, start=t, end=nxt))
    return lines


def lines_from_plain(text: str, lang: str) -> list[Line]:
    return [make_line(t, lang) for t in plain_lines(text)]
