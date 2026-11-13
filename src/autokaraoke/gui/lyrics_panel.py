"""List of lyric lines with timing and review status; double-click to edit text.

Backing lines (kept in the karaoke mix) are shown in blue.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from autokaraoke.gui.timeline import BACKING, confidence_color
from autokaraoke.project import Project
from autokaraoke.text.edit import to_notation


def fmt_time(t: float | None) -> str:
    if t is None:
        return "--:--.--"
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


class LyricsPanel(QListWidget):
    lineActivated = Signal(int)  # single click: select / seek
    editRequested = Signal(int)  # double click

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Project | None = None
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.itemClicked.connect(lambda it: self.lineActivated.emit(self.row(it)))
        self.itemDoubleClicked.connect(lambda it: self.editRequested.emit(self.row(it)))
        self.setToolTip('单击跳转到本行，双击或按 Enter 编辑歌词及音节。\n右键可插入、删除、合并歌词行或标记和声。')

    def set_project(self, project: Project | None) -> None:
        self.project = project
        self.refresh()

    def refresh(self) -> None:
        row = self.currentRow()
        self.clear()
        if not self.project:
            return
        for line in self.project.lines:
            sylls = line.syllables()
            span = line.span()
            start = span[0] if span else line.start
            mark = "  ⚠" if line.note else ""
            text = to_notation(line).replace("|", "·")
            if line.backing:
                bracketed = text[:1] in "([{" and text[-1:] in ")]}"
                text = f"♪ {text}" if bracketed else f"♪ ({text})"
            item = QListWidgetItem(f"{fmt_time(start)}   {text}{mark}")
            if line.backing:
                item.setForeground(QBrush(BACKING))
            worst = min((s.confidence for s in sylls), default=1.0)
            tips = []
            if worst < 0.5:
                color = confidence_color(worst)
                color.setAlpha(70)
                item.setBackground(QBrush(color))
                tips.append('部分音节需要复核')
            if line.backing:
                tips.append('和声：保留在卡拉 OK 音轨中，并在视频中以和声样式显示')
            if line.note:
                tips.append(line.note + '（双击编辑本行）')
            if tips:
                item.setToolTip("\n".join(tips))
            self.addItem(item)
        if 0 <= row < self.count():
            self.setCurrentRow(row)

    def selected_rows(self) -> list[int]:
        rows = sorted({i.row() for i in self.selectedIndexes()})
        return rows or ([self.currentRow()] if self.currentRow() >= 0 else [])

    def highlight(self, index: int) -> None:
        if 0 <= index < self.count() and index != self.currentRow():
            self.blockSignals(True)
            self.setCurrentRow(index)
            self.scrollToItem(self.item(index), QListWidget.ScrollHint.PositionAtCenter)
            self.blockSignals(False)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentRow() >= 0:
            self.editRequested.emit(self.currentRow())
        else:
            super().keyPressEvent(e)
