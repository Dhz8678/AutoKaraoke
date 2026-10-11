"""Editing a line's text in "syllable notation": words split by spaces, syllables by '|'."""

from __future__ import annotations

from autokaraoke.align.naive import time_line
from autokaraoke.project import Line, Syllable, Word
from autokaraoke.text.syllabify import split_words, syllabify_line


def to_notation(line: Line) -> str:
    return " ".join("|".join(s.text for s in w.syllables) for w in line.words)


def parse_notation(notation: str, lang: str, auto: bool = True) -> list[list[str]]:
    """Words of syllables. With '|' the splits are explicit; without any, the words are
    syllabified automatically, or kept whole when ``auto`` is off."""
    if "|" in notation:
        parsed = [[p for p in w.split("|") if p] for w in notation.split()]
    elif auto:
        parsed = syllabify_line(notation, lang)
    else:
        parsed = [[w] for w in split_words(notation)]
    return [w for w in parsed if w]


def resplit(text: str, lang: str, auto: bool = True) -> str:
    """Redo the syllable splits of every text line (whole words when ``auto`` is off)."""
    out = []
    for t in text.splitlines():
        words = parse_notation(t.replace("|", ""), lang, auto)
        out.append(" ".join("|".join(w) for w in words))
    return "\n".join(out)


def _words(parsed: list[list[str]]) -> list[Word]:
    return [Word(text="".join(sylls), syllables=[Syllable(text=s) for s in sylls]) for sylls in parsed]


def retext_line(line: Line, notation: str, lang: str) -> None:
    """Replace the line's words/syllables, keeping timings when the syllable count matches.

    Without any '|' in ``notation`` the words are syllabified automatically, unless the
    line has ``auto_split`` off.
    """
    parsed = parse_notation(notation, lang, line.auto_split)
    old = line.syllables()
    start = old[0].start if old and old[0].start is not None else line.start
    end = old[-1].end if old and old[-1].end is not None else line.end

    line.words = _words(parsed)
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


def split_into_lines(line: Line, text: str, lang: str) -> list[Line]:
    """Edit a line with one notation per text line: each text line becomes a lyric line.

    Timings are kept when the total syllable count does not change, otherwise the old span
    is shared out by syllable count and spread evenly.
    """
    chunks = [parse_notation(t, lang, line.auto_split) for t in text.splitlines() if t.strip()]
    chunks = [c for c in chunks if c]
    if len(chunks) <= 1:
        if chunks:
            retext_line(line, text.strip(), lang)
        return [line]
    old = line.syllables()
    span = line.span()
    lines = []
    for parsed in chunks:
        words = _words(parsed)
        lines.append(Line(text=" ".join(w.text for w in words), words=words, backing=line.backing,
                          auto_split=line.auto_split))
    new = [s for ln in lines for s in ln.syllables()]
    if len(new) == len(old):
        for n, o in zip(new, old):
            n.start, n.end, n.confidence = o.start, o.end, o.confidence
        for ln in lines:
            ln.start, ln.end = ln.span() or (None, None)
    elif span is not None:
        t, total = span[0], len(new)
        for ln in lines:
            dur = (span[1] - span[0]) * len(ln.syllables()) / total
            ln.start, ln.end = t, t + dur
            t += dur
            time_line(ln)
            for s in ln.syllables():
                s.confidence = 0.2
    return lines


def merge_lines(a: Line, b: Line) -> Line:
    """One line with the words (and timings) of ``a`` followed by those of ``b``."""
    merged = Line(text=f"{a.text} {b.text}".strip(), words=a.words + b.words,
                  note="; ".join(n for n in (a.note, b.note) if n), backing=a.backing)
    merged.start, merged.end = merged.span() or (
        a.start if a.start is not None else b.start, b.end if b.end is not None else a.end)
    return merged


def new_line(text: str, lang: str, prev: Line | None = None, nxt: Line | None = None,
             auto: bool = True) -> Line:
    """A line to insert between ``prev`` and ``nxt``, roughly timed into the gap if there is one."""
    words = _words(parse_notation(text, lang, auto))
    line = Line(text=" ".join(w.text for w in words), words=words, auto_split=auto)
    n = len(line.syllables())
    if not n:
        return line
    after = prev.span()[1] + 0.1 if prev is not None and prev.span() else None
    before = nxt.span()[0] - 0.1 if nxt is not None and nxt.span() else None
    if after is not None and before is not None:
        start, end = after, before
    elif after is not None:
        start, end = after, after + n * 0.4
    elif before is not None:
        start, end = max(0.0, before - n * 0.4), before
    else:
        return line
    if end - start < 0.3:
        return line  # no room: leave it untimed, to be tapped or auto-synced
    line.start, line.end = start, end
    time_line(line)
    for s in line.syllables():
        s.confidence = 0.2
    return line


def split_syllable(word: Word, index: int, pos: int) -> None:
    """Split ``word.syllables[index]`` before character ``pos``; the time is cut proportionally."""
    syl = word.syllables[index]
    if not 0 < pos < len(syl.text):
        raise ValueError("split position must be inside the syllable")
    a, b = Syllable(text=syl.text[:pos]), Syllable(text=syl.text[pos:])
    if syl.start is not None and syl.end is not None:
        cut = syl.start + (syl.end - syl.start) * pos / len(syl.text)
        a.start, a.end, b.start, b.end = syl.start, cut, cut, syl.end
        a.confidence = b.confidence = 1.0
    word.syllables[index : index + 1] = [a, b]


def merge_syllable_with_next(word: Word, index: int) -> None:
    """Join ``word.syllables[index]`` with the following syllable of the same word."""
    a, b = word.syllables[index], word.syllables[index + 1]
    merged = Syllable(text=a.text + b.text, start=a.start if a.start is not None else b.start,
                      end=b.end if b.end is not None else a.end, confidence=1.0)
    word.syllables[index : index + 2] = [merged]
