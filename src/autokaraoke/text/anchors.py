"""Locate lyric lines in an ASR transcript (word-level fuzzy matching).

Gives line start times to unsynced lyrics and to lines added during lyric
reconciliation. The match is a global, order-preserving alignment of word
tokens, so repeated choruses are assigned in singing order.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from difflib import SequenceMatcher

from autokaraoke.asr import AsrWord

SECONDS_PER_WORD = 0.3  # used to back-date a line whose first words were not recognised
MIN_GAP = 0.1


def _is_cjk(ch: str) -> bool:
    return "CJK" in unicodedata.name(ch, "") or "HIRAGANA" in unicodedata.name(ch, "") \
        or "KATAKANA" in unicodedata.name(ch, "") or "HANGUL" in unicodedata.name(ch, "")


def tokens(text: str) -> list[str]:
    """Lower-case word tokens; apostrophes split (l'amore → l, amore); CJK per character."""
    out: list[str] = []
    cur: list[str] = []
    for ch in unicodedata.normalize("NFC", text.lower()):
        if unicodedata.category(ch)[0] in "LN":
            if _is_cjk(ch):
                if cur:
                    out.append("".join(cur))
                    cur = []
                out.append(ch)
            else:
                cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def match_line_starts(texts: Sequence[str], words: Sequence[AsrWord],
                      min_matched: int = 2) -> list[float | None]:
    """Start time of each lyric line in the transcript, or None if it was not found."""
    lyr: list[tuple[int, int]] = []  # (line index, token position in line)
    ltok: list[str] = []
    for i, text in enumerate(texts):
        for p, t in enumerate(tokens(text)):
            lyr.append((i, p))
            ltok.append(t)
    atok: list[str] = []
    atime: list[float] = []
    for w in words:
        for t in tokens(w.text):
            atok.append(t)
            atime.append(w.start)

    sm = SequenceMatcher(None, ltok, atok, autojunk=False)
    first: dict[int, tuple[int, float]] = {}
    count: dict[int, int] = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            line, pos = lyr[a + k]
            count[line] = count.get(line, 0) + 1
            if line not in first:
                first[line] = (pos, atime[b + k])

    out: list[float | None] = []
    for i, text in enumerate(texts):
        n = len(tokens(text))
        if i in first and count[i] >= min(min_matched, n):
            pos, t = first[i]
            out.append(max(0.0, t - pos * SECONDS_PER_WORD))
        else:
            out.append(None)
    return out


def fill_starts(starts: list[float | None], duration: float,
                hints: Sequence[float | None] | None = None) -> list[float]:
    """Make starts complete and strictly increasing.

    Missing values come from ``hints`` when they fit between the neighbours,
    otherwise they are interpolated.
    """
    vals = list(starts)
    # Drop matches that break the order (a chorus matched to the wrong repetition).
    last = -1.0
    for i, v in enumerate(vals):
        if v is not None:
            if v <= last:
                vals[i] = None
            else:
                last = v
    if hints:
        for i, h in enumerate(hints):
            if vals[i] is None and h is not None:
                prev = next((vals[j] for j in range(i - 1, -1, -1) if vals[j] is not None), -1.0)
                nxt = next((vals[j] for j in range(i + 1, len(vals)) if vals[j] is not None), duration)
                if prev < h < nxt:
                    vals[i] = h
    known = [i for i, v in enumerate(vals) if v is not None]
    if not known:
        step = duration / (len(vals) + 1)
        return [step * (i + 1) for i in range(len(vals))]
    out: list[float] = []
    for i, v in enumerate(vals):
        if v is None:
            lo = max((j for j in known if j < i), default=None)
            hi = min((j for j in known if j > i), default=None)
            t0 = vals[lo] if lo is not None else 0.0
            t1 = vals[hi] if hi is not None else duration
            i0 = lo if lo is not None else -1
            i1 = hi if hi is not None else len(vals)
            v = t0 + (t1 - t0) * (i - i0) / (i1 - i0)
        out.append(v)
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] + MIN_GAP)
    return out
