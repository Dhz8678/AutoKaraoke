"""Secondary dialogs: lyric-change review and settings."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
        self.key = QLineEdit(config.openrouter_key() or "")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk-or-…  (optional, from openrouter.ai/keys)")
        self.model = QLineEdit(config.OPENROUTER_MODEL)
        form.addRow(QLabel(
            "An OpenRouter key enables 'Fix lyrics' and cleaner lyrics transcribed from audio.\n"
            "A song costs a fraction of a cent. The key is stored in " + str(config.CONFIG_DIR / ".env")
        ))
        form.addRow("OpenRouter key", self.key)
        form.addRow("LLM model", self.model)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(560, 0)

    def accept(self) -> None:
        key = self.key.text().strip()
        model = self.model.text().strip() or "deepseek/deepseek-v4.1-flash"
        save_settings({"OPENROUTER_API_KEY": key, "AUTOKARAOKE_LLM_MODEL": model})
        if not key:
            os.environ.pop("OPENROUTER_KEY", None)
        config.OPENROUTER_MODEL = model
        super().accept()
