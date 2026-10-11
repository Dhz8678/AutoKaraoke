"""Make the lyrics match what is actually sung.

Published lyrics (LRCLIB) often differ from the recording: repeated choruses
written once or as "x2", section labels, missing ad-libs, wrong words. An LLM
compares them with an ASR transcript of the vocal stem and returns the lyrics
as sung. It only edits text; timing stays with the aligners (LLMs cannot hear
the audio, and audio LLMs are not precise enough for timestamps).

The result is a :class:`Proposal` with a list of changes the user reviews
before it replaces the project lyrics.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from autokaraoke.asr import Transcript
from autokaraoke.project import Line, Project
from autokaraoke.text.anchors import fill_starts, match_line_starts
from autokaraoke.text.lyrics import make_line

SYSTEM = """\
You are a meticulous lyrics editor preparing karaoke subtitles.

You get:
1. PUBLISHED LYRICS of a song: numbered lines (L0, L1, ...), with their start time in seconds when known.
2. TRANSCRIPT: automatic speech recognition of the isolated lead vocals, as timestamped phrases. \
It is good at telling which lines are sung and how many times, but it often mishears words, \
mangles nonsense syllables ("ba-dee-ya"), merges backing vocals, and may skip quiet passages.

Return the lyrics exactly as they are sung in this recording, one subtitle line per element, in singing order.

Rules:
- Keep the published spelling, capitalisation and punctuation. Published lyrics are usually right, \
but they do contain wrong words: when the transcript has a different real word that sounds similar \
and fits the meaning at least as well, use the transcript's word. Keep the published text when the \
transcript is garbled, nonsensical, or just spells the same sounds differently (nonsense syllables, \
"gonna"/"going to").
- Every line where the transcript disagrees in a way you did not resolve goes in "doubts".
- Expand repetition shorthands (e.g. "(x2)", "[Chorus]" meaning "repeat the chorus", "Repeat") into \
the actual lines, and remove section labels, credits and any text that is not sung.
- If the transcript clearly contains sung lines that the published lyrics lack (an extra chorus, \
repeated phrases, an outro), add them, reusing published wording when it is the same text.
- Ignore short background interjections and anything you are unsure about.
- Keep lines of karaoke-friendly length; do not merge or split published lines without a reason.
- If there are no published lyrics, write the lyrics from the transcript: fix obvious recognition \
errors only, add natural punctuation and capitalisation, and split into lines at musical phrases. \
Never invent text the transcript does not contain, even if you think you know the song; if the \
transcript is too sparse or incoherent, return only what it contains and say so in "notes".

Answer with JSON only:
{"lines": [{"text": "...", "from": 3, "start": 12.3}, ...],
 "notes": ["short summary of each change"],
 "doubts": [{"line": 7, "heard": "what the transcript has there"}]}
- "from": index of the published line this line comes from (also when you edit or repeat it), or null for a new line.
- "start": your estimate of when the line starts, in seconds, read from the transcript (null if unknown).
- "notes": at most 10 short items, written in the language of the lyrics, empty if nothing changed.
- "doubts": index in YOUR "lines" list and the transcript text, for lines a human should listen to.
"""


@dataclass
class Change:
    kind: str  # "edit" | "add" | "remove" | "doubt" (old = line text, new = what was heard)
    old: str | None
    new: str | None
    start: float | None
    accepted: bool = True


@dataclass
class _Entry:
    """One slot of the result: the line if its change is accepted, else the fallback."""

    line: Line | None
    fallback: Line | None
    change: Change | None = None
    doubt: Change | None = None


@dataclass
class Proposal:
    entries: list[_Entry] = field(default_factory=list)
    language: str = ""
    notes: list[str] = field(default_factory=list)
    cost: float | None = None
    model: str = ""

    @property
    def changes(self) -> list[Change]:
        return [c for e in self.entries for c in (e.change, e.doubt) if c is not None]

    def result(self) -> list[Line]:
        """Lyrics with the accepted changes applied (``Change.accepted``)."""
        out: list[Line] = []
        for e in self.entries:
            ln = e.line if e.change is None or e.change.accepted else e.fallback
            if ln is None:
                continue
            ln = copy.deepcopy(ln)
            if e.doubt is not None and e.doubt.new:
                if e.doubt.accepted:
                    ln = make_line(e.doubt.new, self.language, start=ln.start, end=ln.end)
                else:
                    ln.note = f"Transcript heard: {e.doubt.new}"
            out.append(ln)
        return out


def _fmt(t: float | None) -> str:
    return "?" if t is None else f"{t:.2f}"


def build_prompt(project: Project, transcript: Transcript) -> str:
    parts = [f"Song: {project.artist} - {project.title} ({project.duration:.0f} s)"]
    if project.language:
        parts.append(f"Language: {project.language}")
    parts.append("\nPUBLISHED LYRICS:")
    if project.lines:
        parts += [f"L{i} [{_fmt(ln.start)}] {ln.text}" for i, ln in enumerate(project.lines)]
    else:
        parts.append("(none)")
    parts.append("\nTRANSCRIPT:")
    parts += [f"[{a:.2f}-{b:.2f}] {text}" for a, b, text in transcript.phrases()]
    return "\n".join(parts)


def _norm(s: str) -> str:
    return " ".join(s.split())


def apply_reply(project: Project, transcript: Transcript, reply: dict) -> Proposal:
    """Turn the LLM answer into new lines with start times, plus a reviewable change list."""
    old = project.lines
    synced = bool(old) and all(ln.start is not None for ln in old)
    items = []
    for it in reply.get("lines") or []:
        if not isinstance(it, dict) or not _norm(str(it.get("text") or "")):
            continue
        src = it.get("from")
        src = src if isinstance(src, int) and 0 <= src < len(old) else None
        hint = it.get("start")
        hint = float(hint) if isinstance(hint, (int, float)) and 0 <= hint <= project.duration else None
        items.append((_norm(str(it["text"])), src, hint))
    if not items:
        raise ValueError("the model returned no lyrics")

    # Published lines keep their (LRC or aligned) start on first use; everything else is placed
    # by matching the text in the transcript, falling back to the model's estimate.
    used: set[int] = set()
    trusted: list[float | None] = []
    for text, src, _ in items:
        if src is not None and src not in used and synced:
            trusted.append(old[src].start)
        else:
            trusted.append(None)
        if src is not None:
            used.add(src)
    matched = match_line_starts([t for t, _, _ in items], transcript.words)
    hints = [h for _, _, h in items]
    guesses: list[float | None] = []
    for tr, m, h in zip(trusted, matched, hints):
        if tr is not None:
            guesses.append(tr)
        elif m is not None and (h is None or abs(m - h) < 8.0):
            guesses.append(m)
        else:
            guesses.append(None)
    starts = fill_starts(guesses, project.duration, hints)

    entries: list[_Entry] = []
    srcs: list[int | None] = []
    first_use: set[int] = set()
    lang = project.language
    for k, ((text, src, _), start) in enumerate(zip(items, starts)):
        nxt = starts[k + 1] if k + 1 < len(starts) else project.duration
        new = make_line(text, lang, start=start, end=nxt)
        srcs.append(src)
        if src is not None and src not in first_use:
            first_use.add(src)
            prev = copy.deepcopy(old[src])
            prev.start, prev.end = start, nxt if prev.end is None else min(prev.end, nxt)
            if _norm(prev.text) == text:
                entries.append(_Entry(prev if trusted[k] is not None else new, None))
            else:
                entries.append(_Entry(new, prev, Change("edit", prev.text, text, start)))
        else:
            entries.append(_Entry(new, None, Change("add", None, text, start)))

    for d in reply.get("doubts") or []:
        if isinstance(d, dict) and isinstance(d.get("line"), int) and 0 <= d["line"] < len(entries):
            e = entries[d["line"]]
            heard = _norm(str(d.get("heard") or ""))
            if e.line is not None and heard and heard.lower() != e.line.text.lower():
                e.doubt = Change("doubt", e.line.text, heard, e.line.start, accepted=False)

    # Dropped published lines go back where they were, so rejecting the removal restores them.
    for i in sorted(set(range(len(old))) - used, reverse=True):
        pos = next((k + 1 for k in range(len(srcs) - 1, -1, -1)
                    if srcs[k] is not None and srcs[k] < i), 0)
        entries.insert(pos, _Entry(None, copy.deepcopy(old[i]),
                                   Change("remove", old[i].text, None, old[i].start)))
        srcs.insert(pos, i)
    notes = [str(n) for n in reply.get("notes") or [] if str(n).strip()][:10]
    return Proposal(entries=entries, language=lang, notes=notes)


def propose(project: Project, transcript: Transcript, model: str | None = None) -> Proposal:
    from autokaraoke.llm import chat_json

    reply = chat_json(SYSTEM, build_prompt(project, transcript), model=model)
    proposal = apply_reply(project, transcript, reply.data)
    proposal.cost, proposal.model = reply.cost, reply.model
    return proposal


def lines_from_transcript(transcript: Transcript, lang: str) -> list[Line]:
    """No lyrics and no LLM: one line per sung phrase, straight from the transcript."""
    phrases = transcript.phrases(gap=0.6, max_words=9)
    lines = []
    for k, (a, _b, text) in enumerate(phrases):
        nxt = phrases[k + 1][0] if k + 1 < len(phrases) else a + 10.0
        lines.append(make_line(text, lang, start=a, end=nxt))
    return lines
