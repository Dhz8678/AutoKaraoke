"""Timeline editor: waveform / spectrogram, line spans and draggable syllable blocks.

Mouse:
  drag block            move the syllable (touching neighbours follow)
  drag block edge       move that boundary (shared with a touching neighbour;
                        hold Alt to detach)
  drag lane divider     make the syllable lane taller / shorter
  click empty area      seek
  double-click block    play from just before the syllable
  right-click block     syllable menu
  wheel / Shift+wheel   scroll
  Ctrl+wheel            zoom
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QScrollBar, QSizePolicy, QVBoxLayout, QWidget

from autokaraoke.project import Line, Project, Syllable
from autokaraoke.spectrum import HOP_S, colormap_lut

MIN_DUR = 0.04
EDGE_PX = 5
LINK_EPS = 0.005
SNAP_PX = 6

RULER_H = 20
LINE_H = 20
MIN_SIGNAL_H = 40
MIN_SYL_H = 24
DIVIDER_PX = 4

BACKING = QColor(90, 150, 255)

HINTS = {
    "move": "Drag: move the syllable (touching neighbours stretch) · Double-click: play it · "
            "Right-click: more",
    "start": "Drag: move the start (shared with a touching neighbour; hold Alt to detach)",
    "end": "Drag: move the end (shared with a touching neighbour; hold Alt to detach)",
    "divider": "Drag: resize the syllable lane",
    "": "Click: seek · Wheel: scroll · Ctrl+wheel: zoom · Select a syllable, then "
        "[ / ] set its start / end at the playhead",
}


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


def spectrogram_image(spec: np.ndarray) -> QImage:
    """uint8 (bands, frames) -> RGB image with high frequencies at the top."""
    rgb = np.ascontiguousarray(colormap_lut()[spec[::-1]])
    h, w, _ = rgb.shape
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return img.copy()  # detach from the numpy buffer


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
    mode: str  # "move", "start", "end", "divider"
    origin_t: float
    orig: list[tuple[float, float]]  # (start, end) of index-1, index, index+1
    link_prev: bool
    link_next: bool
    origin_y: float = 0.0
    origin_h: int = 0


class TimelineCanvas(QWidget):
    seekRequested = Signal(float)
    playFromRequested = Signal(float)
    selectionChanged = Signal(int)  # flat syllable index, -1 for none
    aboutToEdit = Signal()
    edited = Signal()
    viewChanged = Signal()
    hintChanged = Signal(str)
    contextRequested = Signal(int, QPoint)  # syllable index, global position

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(RULER_H + MIN_SIGNAL_H + LINE_H + MIN_SYL_H + 4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.project: Project | None = None
        self.sylls: list[Syllable] = []
        self.syl_line: list[int] = []
        self.peaks = np.zeros(0, np.float32)  # max |amplitude| per 10 ms
        self.spec_image: QImage | None = None
        self.onsets = np.zeros(0)
        self.view_mode = "wave"  # "wave", "spec", "both"
        self.snap = False
        self.syl_h = 48
        self.duration = 0.0
        self.view_start = 0.0
        self.pps = 120.0  # pixels per second
        self.playhead = 0.0
        self.selected = -1
        self._drag: _Drag | None = None
        self._hint = None

    # ---- data -------------------------------------------------------------

    def set_project(self, project: Project | None, peaks: np.ndarray | None) -> None:
        self.project = project
        self.duration = project.duration if project else 0.0
        self.set_signal(peaks, None, None)
        self.refresh()

    def set_signal(self, peaks: np.ndarray | None, spec_image: QImage | None,
                   onsets: np.ndarray | None) -> None:
        self.peaks = peaks if peaks is not None else np.zeros(0, np.float32)
        self.spec_image = spec_image
        self.onsets = onsets if onsets is not None else np.zeros(0)
        self.update()

    def set_peaks(self, peaks: np.ndarray | None) -> None:
        self.set_signal(peaks, self.spec_image, self.onsets)

    def set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
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

    # ---- geometry ---------------------------------------------------------

    def _syl_h(self) -> int:
        return int(max(MIN_SYL_H, min(self.syl_h, self.height() - RULER_H - LINE_H - MIN_SIGNAL_H)))

    def lanes(self) -> tuple[float, float, float, float]:
        """(signal top, signal height, line lane top, syllable lane top)."""
        syl_h = self._syl_h()
        signal_h = self.height() - RULER_H - LINE_H - syl_h
        line_top = RULER_H + signal_h
        return RULER_H, signal_h, line_top, line_top + LINE_H

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
        self._paint_signal(p, w)
        self._paint_lines(p)
        self._paint_syllables(p)
        x = self.x_of(self.playhead)
        p.setPen(QPen(QColor(255, 80, 80), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))
        p.end()

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

    def _paint_signal(self, p: QPainter, w: int) -> None:
        top, height, line_top, _ = self.lanes()
        p.fillRect(QRectF(0, top, w, height), QColor(30, 30, 38))
        spec = self.view_mode in ("spec", "both") and self.spec_image is not None
        if spec:
            # Source rectangle in spectrogram frames (10 ms each) for the visible time range.
            src = QRectF(self.view_start / HOP_S, 0, self.visible_seconds() / HOP_S,
                         self.spec_image.height())
            p.drawImage(QRectF(0, top, w, height), self.spec_image, src)
        if self.view_mode in ("wave", "both") and len(self.peaks):
            cols = column_peaks(self.peaks, self.view_start, self.pps, w)
            mid, half = top + height / 2, height / 2 - 3
            p.setPen(QColor(255, 255, 255, 110) if spec else QColor(110, 140, 190))
            for x, v in enumerate(cols):
                if v > 0.002:
                    d = float(v) * half
                    p.drawLine(QPointF(x, mid - d), QPointF(x, mid + d))
        if len(self.onsets):
            a, b = np.searchsorted(self.onsets, [self.view_start, self.t_of(w)])
            p.setPen(QPen(QColor(80, 230, 230, 230 if self.snap else 160), 2))
            for t in self.onsets[a:b]:
                x = self.x_of(float(t))
                p.drawLine(QPointF(x, line_top - 12), QPointF(x, line_top))
        p.fillRect(QRectF(0, line_top - 1, w, 1), QColor(70, 70, 85))

    def _paint_lines(self, p: QPainter) -> None:
        if not self.project:
            return
        _, _, line_top, _ = self.lanes()
        top = line_top + 2
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
            if line.backing:
                p.fillRect(r, QColor(40, 60, 110) if li % 2 else QColor(48, 70, 125))
                p.setPen(BACKING.lighter(130))
            else:
                p.fillRect(r, QColor(60, 60, 75) if li % 2 else QColor(70, 70, 88))
                p.setPen(QColor(200, 200, 210))
            p.drawText(r.adjusted(3, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, line.text)

    def _paint_syllables(self, p: QPainter) -> None:
        _, _, _, top = self.lanes()
        syl_h = self._syl_h()
        top += 2
        f = QFont(self.font())
        f.setPixelSize(int(min(28, max(11, syl_h * 0.32))))
        p.setFont(f)
        w = self.width()
        if self.snap and len(self.onsets):
            a, b = np.searchsorted(self.onsets, [self.view_start, self.t_of(w)])
            p.setPen(QPen(QColor(80, 230, 230, 50), 1))
            for t in self.onsets[a:b]:
                x = self.x_of(float(t))
                p.drawLine(QPointF(x, top), QPointF(x, top + syl_h))
        for i, s in enumerate(self.sylls):
            if s.start is None or s.end is None:
                continue
            x0, x1 = self.x_of(s.start), self.x_of(s.end)
            if x1 < -2 or x0 > w + 2:
                continue
            r = QRectF(x0, top, max(x1 - x0, 2), syl_h - 4)
            color = confidence_color(s.confidence)
            if i == self.selected:
                color = color.lighter(150)
            p.fillRect(r, color.darker(170))
            p.setPen(QPen(color, 1))
            p.drawRect(r)
            if self.project.lines[self.syl_line[i]].backing:
                p.fillRect(QRectF(r.left(), r.bottom() - 3, r.width(), 3), BACKING)
            if i == self.selected:
                p.setPen(QPen(QColor(255, 255, 255), 2))
                p.drawRect(r)
            p.setPen(QColor(240, 240, 240))
            p.drawText(r.adjusted(3, 0, -1, 0), Qt.AlignmentFlag.AlignVCenter, s.text)

    # ---- hit testing & editing --------------------------------------------

    def _on_divider(self, pos: QPointF) -> bool:
        _, _, line_top, _ = self.lanes()
        return abs(pos.y() - line_top) <= DIVIDER_PX

    def _hit(self, pos: QPointF) -> tuple[int, str]:
        """Return (syllable index, "move" | "start" | "end") under the cursor, or (-1, "")."""
        if pos.y() < self.lanes()[3]:
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

    def _start_drag(self, i: int, mode: str, origin_t: float, detach: bool) -> _Drag:
        s = self.sylls[i]
        prev, nxt = self._neighbours(i)
        link_prev = not detach and prev is not None and abs(prev.end - s.start) < LINK_EPS
        link_next = not detach and nxt is not None and abs(nxt.start - s.end) < LINK_EPS

        def span(x):
            return (x.start, x.end) if x is not None else (0.0, 0.0)

        return _Drag(i, mode, origin_t, [span(prev), span(s), span(nxt)], link_prev, link_next)

    def _snap(self, t: float) -> float:
        if not self.snap or not len(self.onsets):
            return t
        k = int(np.searchsorted(self.onsets, t))
        best = min((self.onsets[j] for j in (k - 1, k) if 0 <= j < len(self.onsets)),
                   key=lambda o: abs(o - t))
        return float(best) if abs(best - t) * self.pps <= SNAP_PX else t

    def _apply_drag(self, d: _Drag, dt: float, snap: bool = True) -> None:
        s = self.sylls[d.index]
        prev, nxt = self._neighbours(d.index)
        (ps, pe), (ss, se), (ns, ne) = d.orig
        # Limits: a linked neighbour can shrink to MIN_DUR, an unlinked one is an obstacle.
        lo = (ps + MIN_DUR if d.link_prev else pe) if prev else 0.0
        hi = (ne - MIN_DUR if d.link_next else ns) if nxt else self.duration

        if d.mode == "move":
            if snap:
                dt = self._snap(ss + dt) - ss
            dt = min(max(dt, lo - ss), hi - se)
            s.start, s.end = ss + dt, se + dt
        elif d.mode == "start":
            t = self._snap(ss + dt) if snap else ss + dt
            s.start = min(max(t, lo), se - MIN_DUR)
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

    def nudge(self, index: int, dt: float) -> None:
        """Move a syllable by ``dt`` seconds like a drag (touching neighbours stretch)."""
        s = self.sylls[index]
        if s.start is None or s.end is None:
            return
        self.aboutToEdit.emit()
        self._apply_drag(self._start_drag(index, "move", 0.0, False), dt, snap=False)
        self.edited.emit()

    def set_edge(self, index: int, mode: str, t: float) -> None:
        """Put the start or end of a syllable at ``t``; an overlapping or touching neighbour
        gives way. An untimed syllable gets a short default length."""
        s = self.sylls[index]
        prev, nxt = self._neighbours(index)
        if s.start is None or s.end is None:
            s.start, s.end = (t, t + 0.3) if mode == "start" else (max(0.0, t - 0.3), t)
        elif mode == "start":
            linked = prev is not None and abs(prev.end - s.start) < LINK_EPS
            s.start = min(t, s.end - MIN_DUR)
            if prev is not None and (linked or prev.end > s.start):
                prev.end = max(s.start, prev.start + MIN_DUR)
                prev.confidence = 1.0
                s.start = max(s.start, prev.end)
        else:
            linked = nxt is not None and abs(nxt.start - s.end) < LINK_EPS
            s.end = max(t, s.start + MIN_DUR)
            if nxt is not None and (linked or nxt.start < s.end):
                nxt.start = min(s.end, nxt.end - MIN_DUR)
                nxt.confidence = 1.0
                s.end = min(s.end, nxt.start)
        s.confidence = 1.0
        self._sync_line_bounds(index)
        self.update()

    def _set_hint(self, mode: str) -> None:
        if mode != self._hint:
            self._hint = mode
            self.hintChanged.emit(HINTS[mode])

    def mousePressEvent(self, e: QMouseEvent) -> None:
        pos = e.position()
        if e.button() == Qt.MouseButton.RightButton:
            i, _ = self._hit(pos)
            if i >= 0:
                self.select(i, scroll=False)
                self.contextRequested.emit(i, e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._on_divider(pos):
            self._drag = _Drag(-1, "divider", 0.0, [], False, False, pos.y(), self._syl_h())
            return
        i, mode = self._hit(pos)
        if i < 0:
            self.seekRequested.emit(max(0.0, self.t_of(pos.x())))
            return
        self.select(i, scroll=False)
        detach = bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
        self.aboutToEdit.emit()
        self._drag = self._start_drag(i, mode, self.t_of(pos.x()), detach)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        pos = e.position()
        d = self._drag
        if d is None:
            if self._on_divider(pos):
                mode = "divider"
            else:
                mode = self._hit(pos)[1]
            cursor = {
                "start": Qt.CursorShape.SizeHorCursor,
                "end": Qt.CursorShape.SizeHorCursor,
                "move": Qt.CursorShape.OpenHandCursor,
                "divider": Qt.CursorShape.SplitVCursor,
            }.get(mode, Qt.CursorShape.ArrowCursor)
            self.setCursor(cursor)
            self._set_hint(mode)
            return
        if d.mode == "divider":
            self.syl_h = int(d.origin_h + d.origin_y - pos.y())
            self.syl_h = self._syl_h()
            self.update()
            return
        self._apply_drag(d, self.t_of(pos.x()) - d.origin_t)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._drag is not None:
            mode = self._drag.mode
            self._drag = None
            if mode != "divider":
                self.edited.emit()

    def leaveEvent(self, e) -> None:
        self._set_hint("")

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        i, _ = self._hit(e.position())
        if i >= 0 and self.sylls[i].start is not None:
            self.playFromRequested.emit(max(0.0, self.sylls[i].start - 0.5))

    def wheelEvent(self, e: QWheelEvent) -> None:
        delta = e.angleDelta().y() or e.angleDelta().x()
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
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
        lay.addWidget(self.canvas, 1)
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
