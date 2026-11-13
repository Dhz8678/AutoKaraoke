"""Live karaoke preview drawn like the rendered video (same layout, colours and ball)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from autokaraoke.export.ass import (
    PLAY_W,
    AssStyle,
    ball_at,
    ball_radius,
    fit_scale,
    line_ball,
    pieces,
    project_style,
    schedule,
    slot_y,
)
from autokaraoke.fonts import TextMeasure
from autokaraoke.project import Project


def current_line_index(project: Project, t: float) -> int:
    """Line being sung at ``t``, or the next one to be sung."""
    idx = -1
    for i, line in enumerate(project.lines):
        span = line.span()
        if span and span[0] - 0.3 <= t:
            idx = i
    if idx >= 0:
        _, end = project.lines[idx].span()
        if t > end + 0.8 and idx + 1 < len(project.lines):
            return idx + 1
    return max(idx, 0)


class KaraokePreview(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.project: Project | None = None
        self.style_: AssStyle = AssStyle()
        self._measure: TextMeasure | None = None
        self.t = 0.0
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_project(self, project: Project | None) -> None:
        self.project = project
        self.set_style(project_style(project) if project else AssStyle())

    def set_style(self, st: AssStyle) -> None:
        self.style_ = st
        self._measure = TextMeasure(st.font, st.size, st.bold, st.spacing)
        self.update()

    def set_time(self, t: float) -> None:
        self.t = t
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        st = self.style_
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(8, 8, 12))
        # Show the band of the video frame that holds the lyrics, as large as it fits.
        top = st.upper_y - st.size * 2.0
        band_h = st.lower_y + st.size * 0.4 - top
        k = min(self.width() / PLAY_W, self.height() / band_h)
        ox = (self.width() - PLAY_W * k) / 2
        oy = (self.height() - band_h * k) / 2 - top * k
        p.fillRect(QRectF(ox, oy + top * k, PLAY_W * k, band_h * k), QColor(st.background_color))
        if not self.project or not self.project.lines or self._measure is None:
            return
        p.translate(ox, oy)
        p.scale(k, k)
        t = self.t
        for pl in schedule(self.project, st):
            if pl.show <= t < pl.end:
                self._draw_line(p, pl, t)

    def _draw_line(self, p: QPainter, pl, t: float) -> None:
        st, m = self.style_, self._measure
        line = self.project.lines[pl.line]
        backing = line.backing
        ps = pieces(line, backing)
        full = "".join(text for text, _, _ in ps)
        scale = fit_scale(m.width(full))
        font = QFont(st.font)
        font.setPixelSize(max(1, round(m.em * scale)))
        font.setBold(st.bold)
        font.setItalic(backing)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, st.spacing * scale)
        left = PLAY_W / 2 - m.width(full) * scale / 2
        baseline = slot_y(st, pl.slot) - m.descent * scale
        waiting = t < pl.active
        base = QColor(st.next_color if waiting else st.backing_color if backing else st.unsung_color)
        sung = QColor(st.sung_color)
        outline = QPen(QColor(st.outline_color), st.outline * 2 * scale)
        prefix = ""
        for text, start, end in ps:
            x = left + m.advance(prefix) * scale
            w = m.advance(text) * scale
            prefix += text
            path = QPainterPath()
            path.addText(QPointF(x, baseline), font, text)
            # Variable fonts draw glyphs as overlapping contours: with the default odd-even
            # rule the overlaps (e.g. the bar of "f" and "t") would be left unfilled.
            path.setFillRule(Qt.FillRule.WindingFill)
            if st.outline > 0:
                p.strokePath(path.simplified(), outline)
            p.fillPath(path, base)
            if not waiting and start is not None and end is not None and t > start:
                frac = 1.0 if t >= end else (t - start) / max(end - start, 1e-3)
                p.save()
                p.setClipRect(QRectF(x, baseline - st.size * 2, w * frac, st.size * 3))
                p.fillPath(path, sung)
                p.restore()
        if st.ball and not backing and not waiting and (ball := line_ball(line, m, pl, st)):
            kf, y = ball
            pos = ball_at(kf, t)
            if pos is not None:
                r = ball_radius(st)
                p.setPen(QPen(QColor(st.outline_color), max(1.0, st.outline / 2)))
                p.setBrush(QColor(st.ball_color))
                p.drawEllipse(QPointF(pos[0], y + pos[1]), r, r)
