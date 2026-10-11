"""Timeline editor: vocal waveform, line spans and draggable syllable blocks.

Mouse:
  drag block            move the syllable (touching neighbours follow)
  drag block edge       move that boundary (shared with a touching neighbour;
                        hold Alt to detach)
  click empty area      seek
  double-click block    play from just before the syllable
  wheel / Shift+wheel   scroll / zoom (Ctrl+wheel also zooms)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QScrollBar, QSizePolicy, QVBoxLayout, QWidget

from autokaraoke.project import Line, Project, Syllable

MIN_DUR = 0.04
EDGE_PX = 5
LINK_EPS = 0.005

RULER_H = 20
WAVE_H = 90
LINE_H = 20
SYL_H = 36


def compute_peaks(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Max |amplitude| per 10 ms, normalized to 0..1."""
    hop = sample_rate // 100
    n = len(audio) // hop
    peaks = np.abs(audio[: n * hop]).reshape(n, hop).max(axis=1)
    return (peaks / (peaks.max() + 1e-9)).astype(np.float32)


def column_peaks(peaks: np.ndarray, t0: float, pps: float, width: int) -> np.ndarray:
    """Reduce the 10 ms envelope to one max value per pixel column."""
    n = len(peaks)
    edges = np.clip(((t0 + np.arange(width + 1) / pps) * 100).astype(int), 0, n)
    lo, hi = edges[:-1], np.maximum(edges[1:], edges[:-1] + 1)
    out = np.zeros(width, np.float32)
    valid = lo < n
    if valid.any():
        idx = np.empty(2 * int(valid.sum()), int)
        idx[0::2], idx[1::2] = lo[valid], np.minimum(hi[valid], n)
        out[valid] = np.maximum.reduceat(np.append(peaks, 0), idx)[0::2]
    return out


def confidence_color(conf: float) -> QColor:
    if conf >= 0.95:
        return QColor(80, 160, 255)  # manually edited
    if conf >= 0.5:
        return QColor(70, 180, 110)
    if conf >= 0.3:
        return QColor(230, 170, 50)
    return QColor(220, 80, 70)


@dataclass
class _Drag:
    index: int
    mode: str  # "move", "start", "end"
    origin_t: float
    orig: list[tuple[float, float]]  # (start, end) of index-1, index, index+1
    link_prev: bool
    link_next: bool


class TimelineCanvas(QWidget):
    seekRequested = Signal(float)
    playFromRequested = Signal(float)
    selectionChanged = Signal(int)  # flat syllable index, -1 for none
    aboutToEdit = Signal()
    edited = Signal()
    viewChanged = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(RULER_H + WAVE_H + LINE_H + SYL_H + 10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.project: Project | None = None
        self.sylls: list[Syllable] = []
        self.syl_line: list[int] = []
        self.peaks = np.zeros(0, np.float32)  # max |amplitude| per 10 ms
        self.duration = 0.0
        self.view_start = 0.0
        self.pps = 120.0  # pixels per second
        self.playhead = 0.0
        self.selected = -1
        self._drag: _Drag | None = None

    # ---- data -------------------------------------------------------------

    def set_project(self, project: Project | None, peaks: np.ndarray | None) -> None:
        self.project = project
        self.duration = project.duration if project else 0.0
        self.set_peaks(peaks)
        self.refresh()

    def set_peaks(self, peaks: np.ndarray | None) -> None:
        self.peaks = peaks if peaks is not None else np.zeros(0, np.float32)
        self.update()

    def refresh(self) -> None:
        self.sylls, self.syl_line = [], []
        if self.project:
            for li, line in enumerate(self.project.lines):
                for s in line.syllables():
                    self.sylls.append(s)
                    self.syl_line.append(li)
        if self.selected >= len(self.sylls):
            self.selected = -1
        self.update()

    # ---- coordinates ------------------------------------------------------

    def x_of(self, t: float) -> float:
        return (t - self.view_start) * self.pps

    def t_of(self, x: float) -> float:
        return self.view_start + x / self.pps

    def visible_seconds(self) -> float:
        return self.width() / self.pps

    def set_view_start(self, t: float) -> None:
        t = max(0.0, min(t, max(0.0, self.duration - self.visible_seconds() * 0.5)))
        if t != self.view_start:
            self.view_start = t
            self.viewChanged.emit()
            self.update()

    def zoom(self, factor: float, anchor_x: float | None = None) -> None:
        anchor_x = self.width() / 2 if anchor_x is None else anchor_x
        anchor_t = self.t_of(anchor_x)
        self.pps = float(np.clip(self.pps * factor, 8, 2000))
        self.set_view_start(anchor_t - anchor_x / self.pps)
        self.viewChanged.emit()
        self.update()

    def ensure_visible(self, t: float, margin: float = 0.15) -> None:
        vis = self.visible_seconds()
        if t < self.view_start + vis * margin * 0.3 or t > self.view_start + vis * (1 - margin):
            self.set_view_start(t - vis * 0.3)

    def set_playhead(self, t: float, follow: bool) -> None:
        old_x = self.x_of(self.playhead)
        self.playhead = t
        if follow:
            vis = self.visible_seconds()
            if t > self.view_start + vis * 0.85 or t < self.view_start:
                self.set_view_start(t - vis * 0.15)
        new_x = self.x_of(t)
        self.update(QRectF(min(old_x, new_x) - 3, 0, abs(new_x - old_x) + 6, self.height()).toRect())

    def select(self, index: int, scroll: bool = True) -> None:
        self.selected = index
        if scroll and 0 <= index < len(self.sylls) and self.sylls[index].start is not None:
            self.ensure_visible(self.sylls[index].start)
        self.selectionChanged.emit(index)
        self.update()

    # ---- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(24, 24, 30))
        self._paint_ruler(p, w)
        self._paint_wave(p, w)
        self._paint_lines(p)
        self._paint_syllables(p)
        x = self.x_of(self.playhead)
        p.setPen(QPen(QColor(255, 80, 80), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))

    def _paint_ruler(self, p: QPainter, w: int) -> None:
        p.fillRect(0, 0, w, RULER_H, QColor(36, 36, 44))
        step = next(s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60) if s * self.pps >= 60)
        t = np.floor(self.view_start / step) * step
        p.setPen(QColor(150, 150, 160))
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        while t < self.view_start + w / self.pps:
            x = self.x_of(t)
            p.drawLine(QPointF(x, RULER_H - 6), QPointF(x, RULER_H))
            m, s = divmod(t, 60)
            label = f"{int(m)}:{s:05.2f}" if step < 1 else f"{int(m)}:{int(s):02d}"
            p.drawText(QPointF(x + 3, RULER_H - 7), label)
            t += step

    def _paint_wave(self, p: QPainter, w: int) -> None:
        top, mid = RULER_H, RULER_H + WAVE_H / 2
        p.fillRect(0, top, w, WAVE_H, QColor(30, 30, 38))
        if not len(self.peaks):
            return
        cols = column_peaks(self.peaks, self.view_start, self.pps, w)
        p.setPen(QColor(110, 140, 190))
        half = WAVE_H / 2 - 4
        for x, v in enumerate(cols):
            if v > 0.002:
                d = float(v) * half
                p.drawLine(QPointF(x, mid - d), QPointF(x, mid + d))

    def _paint_lines(self, p: QPainter) -> None:
        if not self.project:
            return
        top = RULER_H + WAVE_H + 2
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        for li, line in enumerate(self.project.lines):
            span = line.span()
            if span is None:
                continue
            x0, x1 = self.x_of(span[0]), self.x_of(span[1])
            if x1 < 0 or x0 > self.width():
                continue
            r = QRectF(x0, top, max(x1 - x0, 2), LINE_H - 4)
            p.fillRect(r, QColor(60, 60, 75) if li % 2 else QColor(70, 70, 88))
            p.setPen(QColor(200, 200, 210))
            p.drawText(r.adjusted(3, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, line.text)

    def _paint_syllables(self, p: QPainter) -> None:
        top = RULER_H + WAVE_H + LINE_H + 2
        f = QFont(self.font())
        f.setPointSizeF(10)
        p.setFont(f)
        w = self.width()
        for i, s in enumerate(self.sylls):
            if s.start is None or s.end is None:
                continue
            x0, x1 = self.x_of(s.start), self.x_of(s.end)
            if x1 < -2 or x0 > w + 2:
                continue
            r = QRectF(x0, top, max(x1 - x0, 2), SYL_H - 4)
            color = confidence_color(s.confidence)
            if i == self.selected:
                color = color.lighter(150)
            p.fillRect(r, color.darker(170))
            p.setPen(QPen(color, 1))
            p.drawRect(r)
            if i == self.selected:
                p.setPen(QPen(QColor(255, 255, 255), 2))
                p.drawRect(r)
            p.setPen(QColor(240, 240, 240))
            p.drawText(r.adjusted(3, 0, -1, 0), Qt.AlignmentFlag.AlignVCenter, s.text)

    # ---- hit testing & editing --------------------------------------------

    def _hit(self, pos: QPointF) -> tuple[int, str]:
        """Return (syllable index, "move" | "start" | "end") under the cursor, or (-1, "")."""
        if pos.y() < RULER_H + WAVE_H + LINE_H:
            return -1, ""
        x = pos.x()
        best, best_dist = (-1, ""), EDGE_PX + 1.0
        for i, s in enumerate(self.sylls):
            if s.start is None or s.end is None:
                continue
            x0, x1 = self.x_of(s.start), self.x_of(s.end)
            if x < x0 - EDGE_PX or x > x1 + EDGE_PX:
                continue
            wide = x1 - x0 > 3 * EDGE_PX
            if x0 <= x <= x1 and (not wide or x0 + EDGE_PX < x < x1 - EDGE_PX):
                return i, "move"
            for mode, edge in (("start", x0), ("end", x1)):
                if abs(x - edge) < best_dist:
                    best, best_dist = (i, mode), abs(x - edge)
        return best

    def _neighbours(self, i: int) -> tuple[Syllable | None, Syllable | None]:
        """Adjacent syllables, ignoring those not timed yet."""
        prev = self.sylls[i - 1] if i > 0 else None
        nxt = self.sylls[i + 1] if i + 1 < len(self.sylls) else None
        if prev is not None and (prev.start is None or prev.end is None):
            prev = None
        if nxt is not None and (nxt.start is None or nxt.end is None):
            nxt = None
        return prev, nxt

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        i, mode = self._hit(e.position())
        if i < 0:
            self.seekRequested.emit(max(0.0, self.t_of(e.position().x())))
            return
        self.select(i, scroll=False)
        s = self.sylls[i]
        prev, nxt = self._neighbours(i)
        detach = bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
        link_prev = not detach and prev is not None and abs(prev.end - s.start) < LINK_EPS
        link_next = not detach and nxt is not None and abs(nxt.start - s.end) < LINK_EPS

        def span(x):
            return (x.start, x.end) if x is not None else (0.0, 0.0)

        self.aboutToEdit.emit()
        self._drag = _Drag(i, mode, self.t_of(e.position().x()),
                           [span(prev), span(s), span(nxt)], link_prev, link_next)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            _, mode = self._hit(e.position())
            cursor = {
                "start": Qt.CursorShape.SizeHorCursor,
                "end": Qt.CursorShape.SizeHorCursor,
                "move": Qt.CursorShape.OpenHandCursor,
            }.get(mode, Qt.CursorShape.ArrowCursor)
            self.setCursor(cursor)
            return
        d = self._drag
        dt = self.t_of(e.position().x()) - d.origin_t
        s = self.sylls[d.index]
        prev, nxt = self._neighbours(d.index)
        (ps, pe), (ss, se), (ns, ne) = d.orig
        # Limits: a linked neighbour can shrink to MIN_DUR, an unlinked one is an obstacle.
        lo = (ps + MIN_DUR if d.link_prev else pe) if prev else 0.0
        hi = (ne - MIN_DUR if d.link_next else ns) if nxt else self.duration

        if d.mode == "move":
            dt = min(max(dt, lo - ss), hi - se)
            s.start, s.end = ss + dt, se + dt
        elif d.mode == "start":
            s.start = min(max(ss + dt, lo), se - MIN_DUR)
        else:
            s.end = max(min(se + dt, hi), ss + MIN_DUR)
        if d.link_prev and prev is not None:
            prev.end = s.start
            prev.confidence = 1.0
        if d.link_next and nxt is not None:
            nxt.start = s.end
            nxt.confidence = 1.0
        s.confidence = 1.0
        self._sync_line_bounds(d.index)
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._drag is not None:
            self._drag = None
            self.edited.emit()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        i, _ = self._hit(e.position())
        if i >= 0 and self.sylls[i].start is not None:
            self.playFromRequested.emit(max(0.0, self.sylls[i].start - 0.5))

    def wheelEvent(self, e: QWheelEvent) -> None:
        delta = e.angleDelta().y() or e.angleDelta().x()
        mods = e.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self.zoom(1.15 if delta > 0 else 1 / 1.15, e.position().x())
        else:
            self.set_view_start(self.view_start - delta / 120 * self.visible_seconds() * 0.1)

    def _sync_line_bounds(self, index: int) -> None:
        for li in {self.syl_line[j] for j in (index - 1, index, index + 1) if 0 <= j < len(self.sylls)}:
            line: Line = self.project.lines[li]
            if span := line.span():
                line.start, line.end = span


class Timeline(QWidget):
    """Canvas plus horizontal scrollbar."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.canvas = TimelineCanvas(self)
        self.scroll = QScrollBar(Qt.Orientation.Horizontal, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.canvas)
        lay.addWidget(self.scroll)
        self.canvas.viewChanged.connect(self._sync_scrollbar)
        self.scroll.valueChanged.connect(lambda v: self.canvas.set_view_start(v / 100))

    def _sync_scrollbar(self) -> None:
        c = self.canvas
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, int(max(0.0, c.duration - c.visible_seconds() * 0.5) * 100))
        self.scroll.setPageStep(int(c.visible_seconds() * 100))
        self.scroll.setValue(int(c.view_start * 100))
        self.scroll.blockSignals(False)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._sync_scrollbar()
