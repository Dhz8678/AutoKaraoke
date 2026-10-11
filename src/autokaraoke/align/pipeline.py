"""Syllable alignment pipeline.

Strategy:
1. Anchors: line start times come from synced LRC; for plain lyrics a first
   whole-song pass provides them.
2. Each line is aligned in a window that also contains its neighbour lines,
   so the line never sits at the window edge (the aligner tends to pin the
   first unit of a window to time 0) and repeated choruses cannot be confused.
3. Post-processing: monotonic times, minimum durations, onset snapping of
   suspiciously long first syllables, and a confidence score per syllable
   that the editor uses to highlight what to check.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from autokaraoke.align.base import SAMPLE_RATE, Aligner, Segment, Timing, load_mono
from autokaraoke.align.naive import time_line
from autokaraoke.project import Line, Project, Syllable

PAD_BEFORE = 1.0
PAD_AFTER = 0.5
CONTEXT_LINES = 1
MIN_DURATION = 0.08
# Lines followed by a long instrumental gap: cap the window instead of using the next LRC time.
MAX_LINE_SECONDS_PER_SYLLABLE = 1.2
ANCHOR_TOLERANCE = 0.8  # s; deviation from the LRC line start that lowers confidence
MAX_ANCHOR_DEVIATION = 2.0  # s; beyond this the line result is rejected
CROSS_CHECK_TOLERANCE = 0.3  # s; onset disagreement between aligners that flags a syllable
RMS_HOP = 160  # 10 ms at 16 kHz
STRANDED_GAP = 0.6

Progress = Callable[[float, str], None]


def make_aligner(engine: str, language: str = "") -> Aligner:
    if engine == "qwen":
        from autokaraoke.align.qwen import QwenAligner

        return QwenAligner()
    if engine == "mms":
        from autokaraoke.align.ctc import CtcAligner

        return CtcAligner(language=language)
    raise ValueError(f"unknown aligner: {engine}")


def _rms(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // RMS_HOP
    frames = audio[: n * RMS_HOP].reshape(n, RMS_HOP)
    return np.sqrt((frames**2).mean(axis=1) + 1e-12)


def _slice(audio: np.ndarray, start: float, end: float) -> tuple[float, np.ndarray]:
    start = max(0.0, start)
    a = int(start * SAMPLE_RATE)
    return start, audio[a : max(a + 1, int(end * SAMPLE_RATE))]


def _line_window_end(line: Line) -> float:
    n = max(1, len(line.syllables()))
    end = line.end if line.end is not None else line.start + n * 0.5
    return min(end, line.start + 1.0 + n * MAX_LINE_SECONDS_PER_SYLLABLE)


def _whole_song_pass(aligner: Aligner, audio: np.ndarray, lines: list[Line]) -> None:
    """Plain lyrics: one pass over the whole song to get line anchors."""
    duration = len(audio) / SAMPLE_RATE
    if duration > 290:
        raise ValueError(
            "Plain (unsynced) lyrics are only supported for songs up to ~4:50; "
            "provide synced LRC lyrics or sync line starts in the editor."
        )
    units = [s.text for ln in lines for s in ln.syllables()]
    pad = np.zeros(SAMPLE_RATE, np.float32)  # keeps the first unit off the window edge
    (timings,) = aligner.align([Segment(-1.0, np.concatenate([pad, audio]), units)])
    k = 0
    for ln in lines:
        n = len(ln.syllables())
        if n:
            ln.start, ln.end = timings[k].start, timings[k + n - 1].end
        k += n


def align_project(
    project: Project,
    aligner: Aligner,
    secondary: Aligner | None = None,
    progress: Progress | None = None,
) -> None:
    """Align all syllables of ``project`` in place.

    ``secondary`` (typically the MMS CTC aligner) cross-checks the primary
    result to set confidences and stands in for lines the primary loses.
    """
    report = progress or (lambda f, msg: None)
    path = project.abspath(project.audio_vocals) or project.abspath(project.audio_full)
    if path is None:
        raise ValueError("project has no audio")
    report(0.0, "Loading audio")
    audio = load_mono(path)
    rms = _rms(audio)

    lines = [ln for ln in project.lines if ln.syllables()]
    lrc_starts = [ln.start for ln in lines]
    if any(s is None for s in lrc_starts):
        report(0.05, "Locating lines (whole-song pass)")
        _whole_song_pass(aligner, audio, lines)
        lrc_starts = [None] * len(lines)

    # Line spans before alignment, used for windows and as the last-resort fallback.
    anchors = [(ln.start, _line_window_end(ln)) for ln in lines]

    # Pass A: each line with its neighbours as context.
    segments, spans = [], []
    for i, line in enumerate(lines):
        lo, hi = max(0, i - CONTEXT_LINES), min(len(lines), i + CONTEXT_LINES + 1)
        units = [s.text for ln in lines[lo:hi] for s in ln.syllables()]
        offset = sum(len(ln.syllables()) for ln in lines[lo:i])
        segments.append(_segment(audio, anchors[lo][0] - PAD_BEFORE, anchors[hi - 1][1] + PAD_AFTER,
                                 units, pad=lo == 0))
        spans.append((offset, offset + len(line.syllables())))
    report(0.1, f"Aligning {len(lines)} lines")
    primary = [res[a:b] for (a, b), res in zip(spans, aligner.align(segments))]
    ok = [_plausible(r, seg, lrc) for r, seg, lrc in zip(primary, segments, lrc_starts)]

    # Single-line windows, for the retry and the secondary aligner.
    tight = [
        _segment(audio, anchors[i][0] - 0.5, anchors[i][1] + 0.3,
                 [s.text for s in ln.syllables()], pad=True)
        for i, ln in enumerate(lines)
    ]
    failed = [i for i in range(len(lines)) if not ok[i]]
    retried: dict[int, list[Timing]] = {}
    if failed:
        report(0.4, f"Retrying {len(failed)} lines alone")
        for i, res in zip(failed, aligner.align([tight[i] for i in failed])):
            if _plausible(res, tight[i], lrc_starts[i]):
                retried[i] = res

    second: list[list[Timing] | None] = [None] * len(lines)
    if secondary is not None:
        report(0.6, f"Cross-checking with {secondary.name}")
        for i, res in enumerate(secondary.align(tight)):
            if _plausible(res, tight[i], lrc_starts[i]):
                second[i] = res

    for i, line in enumerate(lines):
        if ok[i]:
            _apply(line, primary[i], rms)
            if second[i] is not None:
                _cross_check(line.syllables(), second[i])
        elif i in retried:
            _apply(line, retried[i], rms, confidence=0.6)
        elif second[i] is not None:
            _apply(line, second[i], rms, confidence=0.45)
        else:
            # Spread evenly over the known line span; the user will fix it.
            line.start, line.end = anchors[i]
            time_line(line)
            for s in line.syllables():
                s.confidence = 0.2

    for line, lrc_start in zip(lines, lrc_starts):
        sylls = line.syllables()
        if lrc_start is not None and abs(sylls[0].start - lrc_start) > ANCHOR_TOLERANCE:
            for s in sylls:
                s.confidence *= 0.5
        line.start, line.end = sylls[0].start, sylls[-1].end
    _fix_line_overlaps(lines)
    report(1.0, "Done")


def _cross_check(sylls: list[Syllable], other: list[Timing]) -> None:
    """Two independent aligners disagreeing on an onset is a good hint to review it."""
    for s, t in zip(sylls, other):
        if abs(s.start - t.start) > CROSS_CHECK_TOLERANCE:
            s.confidence = min(s.confidence, 0.45)


def _segment(audio: np.ndarray, start: float, end: float, units: list[str], pad: bool) -> Segment:
    seg_start, seg_audio = _slice(audio, start, end)
    if pad:  # leading silence keeps the first unit off the window edge
        seg_audio = np.concatenate([np.zeros(SAMPLE_RATE // 2, np.float32), seg_audio])
        seg_start -= 0.5
    return Segment(seg_start, seg_audio, units)


def _plausible(timings: list[Timing], seg: Segment, lrc_start: float | None) -> bool:
    """Reject results where the aligner clearly lost track of the line."""
    if not timings:
        return False
    seg_end = seg.start + len(seg.audio) / SAMPLE_RATE
    if any(t.start < seg.start - 0.01 or t.end > seg_end + 0.01 for t in timings):
        return False
    collapsed = sum(1 for t in timings if t.end - t.start < MIN_DURATION - 1e-6)
    if len(timings) >= 3 and collapsed / len(timings) > 0.4:
        return False
    if lrc_start is not None and abs(timings[0].start - lrc_start) > MAX_ANCHOR_DEVIATION:
        # Allow a stranded first syllable as long as the rest of the line is near the anchor.
        if len(timings) < 2 or abs(timings[1].start - lrc_start) > MAX_ANCHOR_DEVIATION:
            return False
    return True


def _apply(line: Line, timings: list[Timing], rms: np.ndarray, confidence: float = 0.9) -> None:
    sylls = line.syllables()
    for s, t in zip(sylls, timings):
        s.start, s.end, s.confidence = t.start, t.end, confidence
    _postprocess_line(sylls, rms)


def _last_onset(rms: np.ndarray, start: float, end: float) -> float | None:
    """Start of the last voiced run that begins in [start, end), or None.

    "Voiced" is relative to the loudness around the region, so the tail of a
    previous held note followed by a breath and a new attack yields the attack.
    """
    a, b = max(int(start * 100), 0), int(end * 100)
    if b - a < 3:
        return None
    ref = float(np.percentile(rms[max(a - 100, 0) : b + 100], 95))
    loud = rms[a:b] > 0.25 * ref
    rises = np.nonzero(loud[1:] & ~loud[:-1])[0]
    return start + (rises[-1] + 1) / 100 if len(rises) else None


def _fix_line_start(sylls: list[Syllable], rms: np.ndarray) -> None:
    """The aligner sometimes glues a line's first syllable(s) to the previous line's tail."""
    durations = [s.end - s.start for s in sylls]
    median = float(np.median(durations))
    first = sylls[0]
    # Case 1: the first syllable is stretched back over a pause.
    if first.end - first.start > max(1.0, 4 * median):
        # The real attack can fall slightly after the predicted end, so search a bit beyond.
        limit = sylls[1].start + 0.15 if len(sylls) > 1 else first.end
        onset = _last_onset(rms, first.start, limit)
        if onset is not None:
            first.start = onset
            first.end = max(first.end, onset + MIN_DURATION)
            if len(sylls) > 1 and sylls[1].start < first.end:
                sylls[1].start = first.end
                sylls[1].end = max(sylls[1].end, sylls[1].start + MIN_DURATION)
            first.confidence = 0.5
        return
    # Case 2: one or two leading syllables stranded far before the rest of the line.
    for k in range(min(2, len(sylls) - 1)):
        if sylls[k + 1].start - sylls[k].end <= STRANDED_GAP:
            continue
        n = k + 1
        anchor = sylls[n].start
        onset = _last_onset(rms, anchor - 0.3 * n - 0.4, anchor - MIN_DURATION * n)
        if onset is None:
            onset = anchor - 0.2 * n
        step = (anchor - onset) / n
        for j in range(n):
            sylls[j].start = onset + j * step
            sylls[j].end = onset + (j + 1) * step
            sylls[j].confidence = 0.5
        return


def _postprocess_line(sylls: list[Syllable], rms: np.ndarray) -> None:
    # Monotonic.
    for prev, cur in zip(sylls, sylls[1:]):
        cur.start = max(cur.start, prev.start)
        cur.end = max(cur.end, cur.start)

    _fix_line_start(sylls, rms)

    # Minimum duration: borrow time from the previous syllable, else from the next.
    for i, s in enumerate(sylls):
        if s.end - s.start >= MIN_DURATION - 1e-6:
            continue
        s.confidence = min(s.confidence, 0.4)
        prev = sylls[i - 1] if i > 0 else None
        nxt = sylls[i + 1] if i + 1 < len(sylls) else None
        if prev and prev.end - prev.start >= 2 * MIN_DURATION - 1e-6:
            s.start = min(s.start, s.end - MIN_DURATION)
            prev.end = min(prev.end, s.start)
        elif nxt and nxt.end - nxt.start >= 2 * MIN_DURATION:
            s.end = s.start + MIN_DURATION
            nxt.start = max(nxt.start, s.end)
        else:
            s.end = s.start + MIN_DURATION

    # Silence where singing is expected: likely misplaced.
    line_rms = rms[int(sylls[0].start * 100) : int(sylls[-1].end * 100) + 1]
    if len(line_rms):
        ref = float(np.median(line_rms))
        for s in sylls:
            seg = rms[int(s.start * 100) : int(s.end * 100) + 1]
            if len(seg) and float(seg.mean()) < 0.15 * ref:
                s.confidence = min(s.confidence, 0.4)


def _fix_line_overlaps(lines: list[Line]) -> None:
    """Lines are sung in order; trim a line's tail if it runs into the next one."""
    for cur, nxt in zip(lines, lines[1:]):
        cs, ns = cur.syllables(), nxt.syllables()
        if cs[-1].end > ns[0].start:
            cs[-1].end = max(cs[-1].start + MIN_DURATION, ns[0].start)
            if cs[-1].end > ns[0].start:
                ns[0].start = cs[-1].end
                ns[0].end = max(ns[0].end, ns[0].start + MIN_DURATION)
            cs[-1].confidence = min(cs[-1].confidence, 0.6)
            cur.end = cs[-1].end
