"""Secondary dialogs: lyric-change review and settings."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from autokaraoke import config
from autokaraoke.gui.lyrics_panel import fmt_time
from autokaraoke.text.reconcile import Proposal

KIND_LABEL = {"edit": "Change", "add": "Add", "remove": "Remove", "doubt": "Doubt"}
KIND_COLOR = {
    "edit": QColor(220, 170, 40),
    "add": QColor(70, 170, 90),
    "remove": QColor(200, 70, 70),
    "doubt": QColor(70, 140, 210),
}


class ProposalDialog(QDialog):
    """Lists the suggested lyric changes with a checkbox each."""

    def __init__(self, proposal: Proposal, parent=None):
        super().__init__(parent)
        self.proposal = proposal
        self.setWindowTitle("Suggested lyric changes")
        v = QVBoxLayout(self)
        info = QLabel(
            "Lyrics compared with what is sung in the recording. Uncheck what you do not want.\n"
            "Doubts are lines where the transcript heard something else: check one to use the "
            "transcript text, leave it unchecked to keep the line (it stays marked for review)."
        )
        info.setWordWrap(True)
        v.addWidget(info)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["", "Time", "Lyrics"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        for c in proposal.changes:
            if c.kind == "edit":
                text = f"{c.old}\n→ {c.new}"
            elif c.kind == "doubt":
                text = f"{c.old}\n   heard: {c.new}"
            else:
                text = c.new if c.kind == "add" else c.old
            it = QTreeWidgetItem(["  " + KIND_LABEL[c.kind], fmt_time(c.start), text])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if c.accepted else Qt.CheckState.Unchecked)
            it.setForeground(0, QBrush(KIND_COLOR[c.kind]))
            it.setData(0, Qt.ItemDataRole.UserRole, c)
            self.tree.addTopLevelItem(it)
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)
        v.addWidget(self.tree, 1)

        if proposal.notes:
            notes = QLabel("\n".join(f"• {n}" for n in proposal.notes))
            notes.setWordWrap(True)
            v.addWidget(notes)
        foot = []
        if proposal.model:
            foot.append(proposal.model)
        if proposal.cost is not None:
            foot.append(f"cost ${proposal.cost:.4f}")
        foot.append(f"{len(proposal.result())} lines after the changes; run Auto-sync afterwards")
        v.addWidget(QLabel(" · ".join(foot)))

        row = QHBoxLayout()
        for label, state in (("Check all", True), ("Uncheck all", False)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, s=state: self._check_all(s))
            row.addWidget(b)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        v.addLayout(row)
        self.resize(820, 560)

    def _check_all(self, state: bool) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(
                0, Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)

    def accept(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.data(0, Qt.ItemDataRole.UserRole).accepted = it.checkState(0) == Qt.CheckState.Checked
        super().accept()


class LineEditDialog(QDialog):
    """Edit one lyric line in syllable notation; each extra text line becomes a new lyric line."""

    def __init__(self, title: str, text: str, note: str = "", parent=None, auto: bool = True,
                 lang: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.lang = lang
        v = QVBoxLayout(self)
        info = QLabel(
            "Words separated by spaces, syllables by '|' (e.g. <tt>cuo|re mi|o</tt>). "
            "Without any '|' the words are split automatically, if the box below is checked.<br>"
            "Press Enter for a new line to <b>split</b> it in two or more lines. "
            "Timings are kept if the number of syllables does not change.<br>"
            "Ctrl+Enter to apply."
        )
        info.setWordWrap(True)
        v.addWidget(info)
        if note:
            warn = QLabel(f"⚠ {note}")
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #e0a030")
            v.addWidget(warn)
        self.edit = QPlainTextEdit(text)
        f = self.edit.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        self.edit.setFont(f)
        self.edit.selectAll()
        v.addWidget(self.edit, 1)
        row = QHBoxLayout()
        self.auto = QCheckBox("Split words into syllables automatically")
        self.auto.setChecked(auto)
        self.auto.setToolTip("Uncheck for lines that should not be split, e.g. “(coro)”: each word "
                             "becomes one block. Remembered for this line.")
        self.auto.toggled.connect(self._resplit)
        again = QPushButton("Split again")
        again.setToolTip("Redo the syllable splits of the text above (whole words if the box is "
                         "unchecked). Your '|' are replaced.")
        again.clicked.connect(self._resplit)
        row.addWidget(self.auto)
        row.addStretch(1)
        row.addWidget(again)
        v.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.accept)
        v.addWidget(buttons)
        self.resize(640, 220)

    def text(self) -> str:
        return self.edit.toPlainText().strip()

    def auto_split(self) -> bool:
        return self.auto.isChecked()

    def _resplit(self) -> None:
        from autokaraoke.text.edit import resplit

        self.edit.setPlainText(resplit(self.text(), self.lang, self.auto.isChecked()))


HELP_SECTIONS = (
    ("Fixing a syllable by hand", None, (
        "Click it in the timeline, or go to it with ← / →.",
        "<b>P</b> plays it, <b>L</b> loops its line. Slow down with <i>Speed</i> if needed.",
        "Drag the block or its edges, press <b>[</b> / <b>]</b> at the right moment while it plays, "
        "or type the times in <i>Start</i> / <i>End</i>. <b>Ctrl+← / →</b> nudges it by 10 ms "
        "(with Shift: 50 ms).",
        "The <i>Spectrogram</i> view shows consonants as bright vertical streaks. Dragged starts and "
        "edges snap to the detected vocal attacks (cyan ticks); hold <b>Ctrl</b> while dragging to "
        "place them freely.",
    )),
    ("Keys", (
        ("Space", "Play / pause; the tap key when Tap sync is on"),
        ("Esc", "Stop tap sync or loop"),
        ("← / →", "Select previous / next syllable"),
        ("[ / ]", "Set the selected syllable start / end at the playhead"),
        ("P / L", "Play syllable / loop line"),
        ("Ctrl+← / →", "Nudge syllable by 10 ms (with Shift: 50 ms)"),
        ("Ctrl+Z / Ctrl+Shift+Z", "Undo / redo"),
        ("Ctrl+S", "Save"),
        ("Ctrl+E", "Export the synced lyrics (ASS / LRC)"),
        ("Ctrl+R", "Render the video"),
    ), None),
    ("Lyrics list (multiple selection with Ctrl / Shift)", (
        ("Enter / double-click", "Edit the line; typing a new line splits it"),
        ("Insert", "Add a line below"),
        ("Delete", "Delete the selected lines"),
        ("Ctrl+J", "Merge the selected lines, or the line with the next one"),
        ("B", "Backing vocals on / off (blue lines, kept in the karaoke audio)"),
    ), None),
    ("Timeline mouse", (
        ("Drag a block", "Move the syllable; its start snaps to the cyan ticks, touching "
                         "neighbours follow"),
        ("Drag a block edge", "Resize it, snapping to the cyan ticks; Alt detaches it from its "
                              "neighbour"),
        ("Ctrl while dragging", "No snapping"),
        ("Drag the lane divider", "Resize the syllable lane"),
        ("Click empty area", "Seek"),
        ("Double-click a block", "Play from just before it"),
        ("Right-click a block", "Split / merge syllables, set times, backing vocals"),
        ("Wheel / Shift+wheel", "Scroll"),
        ("Ctrl+wheel", "Zoom"),
    ), None),
    ("Colours", (
        ("Green", "Confident timing"),
        ("Yellow / red", "To check"),
        ("Blue", "Edited by you"),
    ), None),
)


class HelpDialog(QDialog):
    """Keyboard shortcuts and how-to, in a resizable window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        parts = ["<style>td { padding: 2px 14px 2px 0; vertical-align: top; } "
                 "h3 { margin: 14px 0 4px 0; } kbd, .k { font-weight: bold; white-space: nowrap; }</style>"]
        for title, rows, steps in HELP_SECTIONS:
            parts.append(f"<h3>{title}</h3>")
            if steps:
                parts.append("<ol>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>")
            if rows:
                parts.append("<table>" + "".join(
                    f"<tr><td class='k'>{k}</td><td>{d}</td></tr>" for k, d in rows) + "</table>")
        text = QTextBrowser()
        text.setHtml("".join(parts))
        text.setOpenExternalLinks(True)
        v = QVBoxLayout(self)
        v.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self.resize(760, 680)


def save_settings(values: dict[str, str]) -> None:
    """Store settings in the user config .env (keeps unrelated keys) and apply them now."""
    path = config.CONFIG_DIR / ".env"
    lines: list[str] = []
    if path.exists():
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                 if ln.split("=", 1)[0].strip() not in values]
    for k, v in values.items():
        if v:
            lines.append(f"{k}={v}")
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        form = QFormLayout(self)
        self.experimental = QCheckBox("Enable experimental LLM features (Fix lyrics)")
        self.experimental.setChecked(config.experimental_llm())
        self.key = QLineEdit(config.openrouter_key() or "")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk-or-…  (optional, from openrouter.ai/keys)")
        self.model = QLineEdit(config.OPENROUTER_MODEL)
        form.addRow(self.experimental)
        form.addRow(QLabel(
            "Experimental: an LLM compares the lyrics with a transcript of the vocals ('Fix lyrics')\n"
            "and cleans up lyrics transcribed from audio. Results are hit and miss: review every change.\n"
            "Needs an OpenRouter key; a song costs a fraction of a cent.\n"
            "Settings are stored in " + str(config.CONFIG_DIR / ".env")
        ))
        form.addRow("OpenRouter key", self.key)
        form.addRow("LLM model", self.model)
        self.experimental.toggled.connect(self._toggle_llm)
        self._toggle_llm(self.experimental.isChecked())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(560, 0)

    def _toggle_llm(self, on: bool) -> None:
        self.key.setEnabled(on)
        self.model.setEnabled(on)

    def accept(self) -> None:
        key = self.key.text().strip()
        model = self.model.text().strip() or "deepseek/deepseek-v4.1-flash"
        save_settings({"OPENROUTER_API_KEY": key, "AUTOKARAOKE_LLM_MODEL": model,
                       "AUTOKARAOKE_EXPERIMENTAL_LLM": "1" if self.experimental.isChecked() else ""})
        config.OPENROUTER_MODEL = model
        super().accept()
