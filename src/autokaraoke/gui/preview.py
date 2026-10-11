"""Live karaoke preview: current line with progressive syllable fill, next line below."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from autokaraoke.project import Line, Project

SUNG = QColor(255, 200, 0)
UNSUNG = QColor(255, 255, 255)
NEXT = QColor(170, 170, 180)


def _pieces(line: Line) -> list[tuple[str, float | None, float | None]]:
    out = []
    for wi, word in enumerate(line.words):
        for si, s in enumerate(word.syllables):
            text = s.text + (" " if si == len(word.syllables) - 1 and wi < len(line.words) - 1 else "")
            out.append((text, s.start, s.end))
    return out


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
        self.t = 0.0
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_project(self, project: Project | None) -> None:
        self.project = project
        self.update()

    def set_time(self, t: float) -> None:
        self.t = t
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(16, 16, 24))
        if not self.project or not self.project.lines:
            return
        i = current_line_index(self.project, self.t)
        h = self.height()
        font = QFont(self.font())
        font.setPixelSize(max(16, min(42, int(h * 0.26))))
        font.setBold(True)
        p.setFont(font)
        self._draw_line(p, self.project.lines[i], h * 0.42, font, active=True)
        if i + 1 < len(self.project.lines):
            self._draw_line(p, self.project.lines[i + 1], h * 0.82, font, active=False)

    def _draw_line(self, p: QPainter, line: Line, baseline: float, font: QFont, active: bool) -> None:
        fm = QFontMetricsF(font)
        pieces = _pieces(line)
        total = sum(fm.horizontalAdvance(t) for t, _, _ in pieces)
        x = (self.width() - total) / 2
        for text, start, end in pieces:
            w = fm.horizontalAdvance(text)
            path = QPainterPath()
            path.addText(x, baseline, font, text)
            # Variable fonts draw glyphs as overlapping contours: with the default odd-even
            # rule the overlaps (e.g. the bar of "f" and "t") would be left unfilled.
            path.setFillRule(Qt.FillRule.WindingFill)
            p.setPen(QPen(QColor(0, 0, 0), 3))
            p.drawPath(path.simplified())
            p.fillPath(path, UNSUNG if active else NEXT)
            if active and start is not None and end is not None and self.t > start:
                frac = 1.0 if self.t >= end else (self.t - start) / max(end - start, 1e-3)
                p.save()
                p.setClipRect(QRectF(x, 0, w * frac, self.height()))
                p.fillPath(path, SUNG)
                p.restore()
            x += w
