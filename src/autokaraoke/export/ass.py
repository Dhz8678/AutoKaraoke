"""Export a timed project to an ASS subtitle file with \\kf karaoke tags.

Lines alternate between two on-screen slots (upper/lower) so the singer can
always read the next line while the current one is highlighted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autokaraoke.project import Line, Project


@dataclass
class AssStyle:
    width: int = 1920
    height: int = 1080
    font: str = "Noto Sans"
    size: int = 80
    sung_color: str = "&H0000C8FF"  # &HAABBGGRR: amber
    unsung_color: str = "&H00FFFFFF"
    outline_color: str = "&H00000000"
    outline: float = 4.0
    shadow: float = 2.0
    upper_y: int = 760
    lower_y: int = 900
    lead: float = 5.0  # max seconds a line is shown before it is sung (if its slot is free)
    tail: float = 0.6  # seconds it stays after its last syllable
    countdown: bool = True  # dots counting down to the first line after a long pause
    countdown_gap: float = 5.0  # pause length (s) that triggers it
    countdown_dots: int = 3  # one dot disappears per second


def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def karaoke_text(line: Line, show_start: float) -> str:
    """Build '{\\k..}{\\kf..}syl...' with durations in centiseconds, drift-free."""
    parts: list[str] = []
    prev = round(show_start * 100)
    for wi, word in enumerate(line.words):
        for si, syl in enumerate(word.syllables):
            if syl.start is None or syl.end is None:
                continue
            start = max(round(syl.start * 100), prev)
            end = max(round(syl.end * 100), start + 1)
            if start > prev:
                parts.append(f"{{\\k{start - prev}}}")
            text = syl.text
            if si == len(word.syllables) - 1 and wi < len(line.words) - 1:
                text += " "
            parts.append(f"{{\\kf{end - start}}}{_escape(text)}")
            prev = end
    return "".join(parts)


def _sung_span(line: Line) -> tuple[float, float] | None:
    sylls = [s for s in line.syllables() if s.start is not None and s.end is not None]
    if not sylls:
        return None
    return sylls[0].start, sylls[-1].end


def _countdowns(timed: list, show_start: list[float], st: AssStyle) -> list[str]:
    """'● ● ●' above a line that follows a long pause; one dot vanishes per second."""
    events = []
    prev_end = 0.0
    for i, (_, (start, end)) in enumerate(timed):
        if start - prev_end >= st.countdown_gap:
            y = (st.upper_y if i % 2 == 0 else st.lower_y) - int(st.size * 1.25)
            for k in range(st.countdown_dots, 0, -1):
                a, b = start - k, start - k + 1
                if a < max(show_start[i], prev_end + 0.5):
                    continue
                dots = " ".join(["●"] * k)
                events.append(f"Dialogue: 1,{ass_time(a)},{ass_time(b)},Countdown,,0,0,0,,"
                              f"{{\\pos({st.width // 2},{y})}}{dots}")
        prev_end = max(prev_end, end)
    return events


def build_ass(project: Project, style: AssStyle | None = None) -> str:
    st = style or AssStyle()
    timed = [(ln, span) for ln in project.lines if (span := _sung_span(ln))]

    show_start: list[float] = []
    for i, (_, (start, _end)) in enumerate(timed):
        t = max(0.0, start - st.lead)
        if i >= 2:  # do not overwrite the line still visible in the same slot
            t = max(t, timed[i - 2][1][1] + 0.1)
        show_start.append(min(t, start))

    events: list[str] = []
    for i, (line, (start, end)) in enumerate(timed):
        show_end = end + st.tail
        if i + 2 < len(timed):
            show_end = min(show_end, show_start[i + 2])
        y = st.upper_y if i % 2 == 0 else st.lower_y
        text = f"{{\\pos({st.width // 2},{y})}}" + karaoke_text(line, show_start[i])
        events.append(
            f"Dialogue: 0,{ass_time(show_start[i])},{ass_time(show_end)},Karaoke,,0,0,0,,{text}"
        )

    if st.countdown:
        events += _countdowns(timed, show_start, st)

    # Title card during the intro.
    first = show_start[0] if show_start else 5.0
    if first > 2.0 and project.title:
        title = _escape(project.title)
        artist = _escape(project.artist)
        events.insert(
            0,
            f"Dialogue: 0,{ass_time(0.3)},{ass_time(min(first - 0.3, 8.0))},Title,,0,0,0,,"
            f"{{\\fad(400,400)}}{title}\\N{{\\fs{st.size * 2 // 3}}}{artist}",
        )

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {st.width}
PlayResY: {st.height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
Title: {_escape(project.artist)} - {_escape(project.title)}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{st.font},{st.size},{st.sung_color},{st.unsung_color},{st.outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{st.outline},{st.shadow},2,40,40,0,1
Style: Countdown,{st.font},{st.size // 2},{st.sung_color},{st.sung_color},{st.outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{st.outline / 2},0,2,40,40,0,1
Style: Title,{st.font},{st.size * 3 // 2},{st.unsung_color},{st.unsung_color},{st.outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{st.outline},{st.shadow},5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header + "\n".join(events) + "\n"


def write_ass(project: Project, path: Path, style: AssStyle | None = None) -> Path:
    path.write_text(build_ass(project, style), encoding="utf-8")
    return path
