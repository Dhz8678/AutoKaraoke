"""Export a timed project to an ASS subtitle file with \\kf karaoke tags.

Two layouts:
  alternate  lines alternate between an upper and a lower slot, so the singer can read
             the next line while the current one is sung; a waiting line is shown in the
             "next" colour until the previous one ends
  scroll     the current line on top, the next one below it in the "next" colour

The placement of lines over time (``schedule``) and the ball path (``ball_keyframes``)
are shared with the editor preview, so the preview matches the video.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from autokaraoke.project import Line, Project

PLAY_W, PLAY_H = 1920, 1080  # ASS coordinate system; libass scales it to the video size
SIDE_MARGIN = 60
LAYOUTS = ("alternate", "scroll")


@dataclass
class AssStyle:
    font: str = "Noto Sans"
    size: int = 80
    bold: bool = True
    sung_color: str = "#FFC800"
    unsung_color: str = "#FFFFFF"
    next_color: str = "#9A9AAA"
    backing_color: str = "#7FB2FF"
    outline_color: str = "#000000"
    background_color: str = "#101018"
    background_image: str = ""  # path; used instead of the colour when it exists
    ball_color: str = "#FF4A4A"
    outline: float = 4.0
    shadow: float = 2.0
    layout: str = "alternate"
    ball: bool = True  # bouncing ball over the syllables
    lower_y: int = 900  # bottom of the lower line; the upper one sits above it
    lead: float = 5.0  # max seconds a line is shown before it is sung (if its slot is free)
    tail: float = 0.6  # seconds it stays after its last syllable
    countdown: bool = True  # dots counting down to the first line after a long pause
    countdown_gap: float = 5.0  # pause length (s) that triggers it
    countdown_dots: int = 3  # one dot disappears per second
    width: int = 1920  # video size
    height: int = 1080

    @property
    def upper_y(self) -> int:
        return self.lower_y - round(self.size * 1.6)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None, base: AssStyle | None = None) -> AssStyle:
        """Override ``base`` (default style) with the known keys of ``d``, cast to the right type."""
        st = cls(**asdict(base)) if base else cls()
        for f in fields(cls):
            if d and f.name in d and d[f.name] is not None:
                value = d[f.name]
                kind = type(getattr(st, f.name))
                try:
                    setattr(st, f.name, value if kind is bool else kind(value))
                except (TypeError, ValueError):
                    pass
        if st.layout not in LAYOUTS:
            st.layout = "alternate"
        return st


def default_style_path() -> Path:
    from autokaraoke import config

    return config.CONFIG_DIR / "render.json"


def default_style() -> AssStyle:
    """Built-in defaults, overridden by the user's saved defaults if any."""
    try:
        data = json.loads(default_style_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    return AssStyle.from_dict(data if isinstance(data, dict) else None)


def save_default_style(st: AssStyle) -> Path:
    path = default_style_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st.to_dict(), indent=1), encoding="utf-8")
    return path


def project_style(project: Project) -> AssStyle:
    return AssStyle.from_dict(project.render, default_style())


def ass_color(rgb: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> '&HAABBGGRR'."""
    h = rgb.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def pieces(line: Line, parens: bool = False) -> list[tuple[str, float | None, float | None]]:
    """(text, start, end) per syllable, with the space after the last syllable of each word.

    ``parens`` wraps the line in brackets (backing vocals) unless it already is, e.g. "[Coro]".
    """
    out = []
    for wi, word in enumerate(line.words):
        for si, s in enumerate(word.syllables):
            text = s.text + (" " if si == len(word.syllables) - 1 and wi < len(line.words) - 1 else "")
            out.append([text, s.start, s.end])
    already = line.text.strip()[:1] in "([{" and line.text.strip()[-1:] in ")]}"
    if parens and out and not already:
        out[0][0] = "(" + out[0][0]
        out[-1][0] += ")"
    return [tuple(p) for p in out]


def karaoke_text(line: Line, show_start: float, parens: bool = False) -> str:
    """Build '{\\k..}{\\kf..}syl...' with durations in centiseconds, drift-free."""
    parts: list[str] = []
    prev = round(show_start * 100)
    for text, start_t, end_t in pieces(line, parens):
        if start_t is None or end_t is None:
            continue
        start = max(round(start_t * 100), prev)
        end = max(round(end_t * 100), start + 1)
        if start > prev:
            parts.append(f"{{\\k{start - prev}}}")
        parts.append(f"{{\\kf{end - start}}}{_escape(text)}")
        prev = end
    return "".join(parts)


def _sung_span(line: Line) -> tuple[float, float] | None:
    return line.span()


@dataclass
class Placement:
    """A line on screen: shown from ``show`` in the "next" colour, highlighted from ``active``."""

    line: int  # index in project.lines
    slot: int  # 0 upper, 1 lower
    show: float
    active: float
    end: float


def schedule(project: Project, st: AssStyle) -> list[Placement]:
    timed = [(i, span) for i, ln in enumerate(project.lines) if (span := _sung_span(ln))]
    out: list[Placement] = []
    if st.layout == "scroll":
        cur = []
        for k, (_, (start, _end)) in enumerate(timed):
            t = max(0.0, start - st.lead)
            if k > 0:
                t = min(max(t, timed[k - 1][1][1] + 0.1), start)
            cur.append(t)
        for k, (i, (_start, end)) in enumerate(timed):
            stop = min(end + st.tail, cur[k + 1]) if k + 1 < len(timed) else end + st.tail
            out.append(Placement(i, 0, cur[k], cur[k], stop))
            if k + 1 < len(timed):
                out.append(Placement(timed[k + 1][0], 1, cur[k], stop, stop))
        return out

    show: list[float] = []
    for k, (_, (start, _end)) in enumerate(timed):
        t = max(0.0, start - st.lead)
        if k >= 2:  # do not overwrite the line still visible in the same slot
            t = max(t, timed[k - 2][1][1] + 0.1)
        show.append(min(t, start))
    for k, (i, (start, end)) in enumerate(timed):
        stop = end + st.tail
        if k + 2 < len(timed):
            stop = min(stop, show[k + 2])
        # Waits in the "next" colour while the previous line is being sung.
        active = show[k] if k == 0 else min(max(show[k], timed[k - 1][1][1]), start)
        out.append(Placement(i, k % 2, show[k], active, max(stop, active)))
    return out


def slot_y(st: AssStyle, slot: int) -> int:
    return st.upper_y if slot == 0 else st.lower_y


def fit_scale(width: float) -> float:
    """Shrink factor for a line wider than the screen."""
    room = PLAY_W - 2 * SIDE_MARGIN
    return min(1.0, room / width) if width > 0 else 1.0


def syllable_starts(line: Line, measure, parens: bool = False) -> tuple[list[float], float, float]:
    """x where each syllable of a centred line begins, its left edge and scale (ASS pixels).

    The ball lands there, where the colour fill of the syllable starts.
    """
    ps = pieces(line, parens)
    full = "".join(t for t, _, _ in ps)
    scale = fit_scale(measure.width(full))
    left = PLAY_W / 2 - measure.width(full) * scale / 2
    starts, prefix = [], ""
    for text, _, _ in ps:
        starts.append(left + measure.width(prefix) * scale)
        prefix += text
    return starts, left, scale


HOP_MAX = 0.6  # seconds: longer gaps wait on the syllable, then hop
HOP_STEPS = 6


def ball_keyframes(xs: list[float], starts: list[float], ends: list[float], height: float,
                   enter_x: float, enter_t: float) -> list[tuple[float, float, float]]:
    """Piecewise-linear ball path (t, x, dy) landing on each syllable as it starts.

    ``dy`` is negative upwards; hops are parabolic arcs sampled in HOP_STEPS segments.
    """
    kf: list[tuple[float, float, float]] = []

    def hop(x0: float, t0: float, x1: float, t1: float) -> None:
        h = height * min(1.0, max(t1 - t0, 0.0) / 0.4)
        for j in range(HOP_STEPS + 1):
            u = j / HOP_STEPS
            kf.append((t0 + u * (t1 - t0), x0 + u * (x1 - x0), -4 * h * u * (1 - u)))

    if not xs:
        return kf
    hop(enter_x, max(enter_t, starts[0] - HOP_MAX), xs[0], starts[0])
    for k in range(len(xs) - 1):
        a, b = starts[k], starts[k + 1]
        t0 = max(a, b - HOP_MAX)
        if t0 > a:
            kf.append((t0, xs[k], 0.0))
        hop(xs[k], t0, xs[k + 1], b)
    kf.append((max(ends[-1], starts[-1]), xs[-1], 0.0))
    out: list[tuple[float, float, float]] = []
    for t, x, dy in kf:
        if out and t <= out[-1][0]:
            out[-1] = (out[-1][0], x, dy) if t == out[-1][0] else out[-1]
            continue
        out.append((t, x, dy))
    return out


def line_ball(line: Line, measure, placement: Placement, st: AssStyle):
    """Ball keyframes and landing height for a sung line, or None."""
    xs, left, scale = syllable_starts(line, measure)
    timed = [(x, s.start, s.end) for x, s in zip(xs, line.syllables())
             if s.start is not None and s.end is not None]
    if not timed:
        return None
    xs, starts, ends = [c for c, _, _ in timed], [a for _, a, _ in timed], [b for _, _, b in timed]
    # Just above the cap height (~0.75 of the font size).
    y = slot_y(st, placement.slot) - st.size * 0.82 * scale - ball_radius(st) - 4
    kf = ball_keyframes(xs, starts, ends, st.size * 0.5, left - st.size * 0.4, placement.active)
    return kf, y


def ball_radius(st: AssStyle) -> int:
    return max(5, round(st.size * 0.14))


def ball_at(kf: list[tuple[float, float, float]], t: float) -> tuple[float, float] | None:
    """(x, dy) of the ball at time ``t``, or None outside the path."""
    if not kf or t < kf[0][0] or t > kf[-1][0]:
        return None
    for (t0, x0, y0), (t1, x1, y1) in zip(kf, kf[1:]):
        if t0 <= t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return x0 + u * (x1 - x0), y0 + u * (y1 - y0)
    return kf[-1][1], kf[-1][2]


def _circle(r: int) -> str:
    c = round(r * 0.552)
    return (f"m 0 {-r} b {c} {-r} {r} {-c} {r} 0 b {r} {c} {c} {r} 0 {r} "
            f"b {-c} {r} {-r} {c} {-r} 0 b {-r} {-c} {-c} {-r} 0 {-r}")


def _ball_events(kf, y: float, st: AssStyle) -> list[str]:
    events = []
    shape = _circle(ball_radius(st))
    cs = [(round(t * 100), x, dy) for t, x, dy in kf]
    for (a, xa, ya), (b, xb, yb) in zip(cs, cs[1:]):
        if b <= a:
            continue
        events.append(
            f"Dialogue: 2,{ass_time(a / 100)},{ass_time(b / 100)},Ball,,0,0,0,,"
            f"{{\\an5\\move({xa:.1f},{y + ya:.1f},{xb:.1f},{y + yb:.1f})\\p1}}{shape}{{\\p0}}"
        )
    return events


def _countdowns(project: Project, places: list[Placement], st: AssStyle) -> list[str]:
    """'● ● ●' above a line that follows a long pause; one dot vanishes per second."""
    events = []
    prev_end = 0.0
    for pl in (p for p in places if p.active < p.end):  # where each line is sung
        start, end = project.lines[pl.line].span()
        if start - prev_end >= st.countdown_gap:
            lift = 2.1 if st.ball else 1.25
            y = slot_y(st, pl.slot) - int(st.size * lift)
            for k in range(st.countdown_dots, 0, -1):
                a, b = start - k, start - k + 1
                if a < max(pl.show, prev_end + 0.5):
                    continue
                dots = " ".join(["●"] * k)
                events.append(f"Dialogue: 1,{ass_time(a)},{ass_time(b)},Countdown,,0,0,0,,"
                              f"{{\\pos({PLAY_W // 2},{y})}}{dots}")
        prev_end = max(prev_end, end)
    return events


def build_ass(project: Project, style: AssStyle | None = None, measure=None) -> str:
    st = style or project_style(project)
    if measure is None:
        from autokaraoke.fonts import TextMeasure

        measure = TextMeasure(st.font, st.size, st.bold)
    places = schedule(project, st)

    events: list[str] = []
    for pl in places:
        line = project.lines[pl.line]
        backing = line.backing
        full = "".join(t for t, _, _ in pieces(line, backing))
        scale = fit_scale(measure.width(full))
        y = slot_y(st, pl.slot)
        pos = f"\\pos({PLAY_W // 2},{y})"
        if scale < 1.0:
            pos += f"\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f}"
        if pl.active > pl.show:
            italic = "\\i1" if backing else ""
            events.append(f"Dialogue: 0,{ass_time(pl.show)},{ass_time(min(pl.active, pl.end))},Next,,0,0,0,,"
                          f"{{{pos}{italic}}}{_escape(full)}")
        if pl.active < pl.end:
            text = karaoke_text(line, pl.active, backing)
            events.append(f"Dialogue: 0,{ass_time(pl.active)},{ass_time(pl.end)},"
                          f"{'Backing' if backing else 'Karaoke'},,0,0,0,,{{{pos}}}{text}")
            if st.ball and not backing and (ball := line_ball(line, measure, pl, st)):
                events += _ball_events(*ball, st)

    if st.countdown:
        events += _countdowns(project, places, st)

    # Title card during the intro.
    first = min((p.show for p in places), default=5.0)
    if first > 2.0 and project.title:
        title = _escape(project.title)
        artist = _escape(project.artist)
        events.insert(
            0,
            f"Dialogue: 0,{ass_time(0.3)},{ass_time(min(first - 0.3, 8.0))},Title,,0,0,0,,"
            f"{{\\fad(400,400)}}{title}\\N{{\\fs{st.size * 2 // 3}}}{artist}",
        )

    bold = -1 if st.bold else 0
    sung, unsung, nxt = ass_color(st.sung_color), ass_color(st.unsung_color), ass_color(st.next_color)
    backing_c, outline, ball_c = ass_color(st.backing_color), ass_color(st.outline_color), ass_color(st.ball_color)
    shade = "&H80000000"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
Title: {_escape(project.artist)} - {_escape(project.title)}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{st.font},{st.size},{sung},{unsung},{outline},{shade},{bold},0,0,0,100,100,0,0,1,{st.outline},{st.shadow},2,40,40,0,1
Style: Backing,{st.font},{st.size},{sung},{backing_c},{outline},{shade},{bold},-1,0,0,100,100,0,0,1,{st.outline},{st.shadow},2,40,40,0,1
Style: Next,{st.font},{st.size},{nxt},{nxt},{outline},{shade},{bold},0,0,0,100,100,0,0,1,{st.outline},{st.shadow},2,40,40,0,1
Style: Ball,{st.font},{st.size},{ball_c},{ball_c},{outline},{shade},0,0,0,0,100,100,0,0,1,{max(1.0, st.outline / 2)},0,5,0,0,0,1
Style: Countdown,{st.font},{st.size // 2},{sung},{sung},{outline},{shade},{bold},0,0,0,100,100,0,0,1,{st.outline / 2},0,2,40,40,0,1
Style: Title,{st.font},{st.size * 3 // 2},{unsung},{unsung},{outline},{shade},{bold},0,0,0,100,100,0,0,1,{st.outline},{st.shadow},5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header + "\n".join(events) + "\n"


def write_ass(project: Project, path: Path, style: AssStyle | None = None) -> Path:
    path.write_text(build_ass(project, style), encoding="utf-8")
    return path
