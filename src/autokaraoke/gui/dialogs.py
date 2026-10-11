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

    def __init__(self, title: str, text: str, note: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        v = QVBoxLayout(self)
        info = QLabel(
            "Words separated by spaces, syllables by '|' (e.g. <tt>cuo|re mi|o</tt>). "
            "Without any '|' the words are split automatically.<br>"
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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.accept)
        v.addWidget(buttons)
        self.resize(640, 220)

    def text(self) -> str:
        return self.edit.toPlainText().strip()


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
