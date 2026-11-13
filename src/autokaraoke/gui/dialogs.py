"""Secondary dialogs: lyric-change review and settings."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from autokaraoke import config
from autokaraoke.gui.lyrics_panel import fmt_time
from autokaraoke.gui.jobs import Job
from autokaraoke.text.reconcile import Proposal

KIND_LABEL = {"edit": '修改', "add": '新增', "remove": '删除', "doubt": '待确认'}
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
        self.setWindowTitle('歌词修改建议')
        v = QVBoxLayout(self)
        info = QLabel(
            '以下为歌词与实际演唱内容的比对结果，请取消不需要的修改。\n“待确认”表示识别结果与原歌词不同：勾选后使用识别文本，不勾选则保留原歌词并继续标记为待复核。'
        )
        info.setWordWrap(True)
        v.addWidget(info)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["", '时间', '歌词'])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        for c in proposal.changes:
            if c.kind == "edit":
                text = f"{c.old}\n→ {c.new}"
            elif c.kind == "doubt":
                text = f"{c.old}\n   识别为：{c.new}"
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
            foot.append(f"费用 ${proposal.cost:.4f}")
        foot.append(f"{len(proposal.result())} 行歌词（修改后）；请重新执行自动对齐")
        v.addWidget(QLabel(" · ".join(foot)))

        row = QHBoxLayout()
        for label, state in (('全选', True), ('全不选', False)):
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
            '用空格分隔单词，用“|”分隔音节（例如 <tt>cuo|re mi|o</tt>）。勾选下方选项后，未输入“|”的单词会自动拆分音节。<br>按 Enter 换行可将歌词<b>拆成多行</b>；音节数量不变时会保留原时间。<br>按 Ctrl+Enter 应用修改。'
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
        self.auto = QCheckBox('自动将单词拆分为音节')
        self.auto.setChecked(auto)
        self.auto.setToolTip('不希望拆分的歌词行可取消勾选，每个单词作为一个音节块；此设置会保存到当前行。')
        self.auto.toggled.connect(self._resplit)
        again = QPushButton('重新拆分')
        again.setToolTip('重新拆分上方文本的音节；未勾选自动拆分时保留完整单词。手动输入的“|”将被替换。')
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
    ('手动调整音节', None, (
        '在时间轴上单击音节，或用 ← / → 选择。',
        '按 <b>P</b> 播放所选音节，按 <b>L</b> 循环本行；可通过<i>速度</i>降低播放速度。',
        '拖动音节块或边缘，播放时按 <b>[</b> / <b>]</b> 标记起止时间，或在<i>开始</i>／<i>结束</i>中输入时间。<b>Ctrl+← / →</b> 每次微调 10 毫秒，配合 Shift 则为 50 毫秒。',
        '<i>频谱图</i>中的明亮竖线通常对应辅音。拖动起点或边缘时会吸附到检测到的人声起点（青色标记），按住 <b>Ctrl</b> 可自由放置。',
    )),
    ('键盘快捷键', (
        ("Space", '播放／暂停；启用打拍对齐时用作打拍键'),
        ("Esc", '停止打拍对齐或循环'),
        ("← / →", '选择上一个／下一个音节'),
        ("[ / ]", '将播放头位置设为所选音节的开始／结束时间'),
        ("P / L", '播放音节／循环本行'),
        ("Ctrl+← / →", '微调音节 10 毫秒（配合 Shift：50 毫秒）'),
        ("Ctrl+Z / Ctrl+Shift+Z", '撤销／重做'),
        ("Ctrl+S", '保存'),
        ("Ctrl+E", '导出已对齐的歌词（ASS / LRC）'),
        ("Ctrl+R", '生成视频'),
    ), None),
    ('歌词列表（用 Ctrl / Shift 多选）', (
        ('Enter／双击', '编辑本行；输入换行可拆分歌词行'),
        ("Insert", '在下方新增一行'),
        ("Delete", '删除选中的歌词行'),
        ("Ctrl+J", '合并选中的行，或将当前行与下一行合并'),
        ("B", '设置／取消和声（蓝色歌词行，保留在卡拉 OK 音轨中）'),
    ), None),
    ('时间轴鼠标操作', (
        ('拖动音节块', '移动音节，起点吸附到青色标记，紧邻的音节边界会跟随调整'),
        ('拖动音节块边缘', '调整长度并吸附到青色标记，按 Alt 可与相邻音节分离'),
        ('拖动时按住 Ctrl', '不吸附'),
        ('拖动轨道分隔线', '调整音节轨道高度'),
        ('单击空白处', '跳转播放位置'),
        ('双击音节块', '从该音节前一点开始播放'),
        ('右键单击音节块', '拆分／合并音节，设置时间及和声'),
        ('滚轮／Shift + 滚轮', '滚动'),
        ('Ctrl + 滚轮', '缩放'),
    ), None),
    ('颜色', (
        ('绿色', '时间对齐可信'),
        ('黄色／红色', '需要复核'),
        ('蓝色', '已手动编辑'),
    ), None),
)


class HelpDialog(QDialog):
    """Keyboard shortcuts and how-to, in a resizable window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('快捷键与使用帮助')
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
        self.setWindowTitle('设置')
        self.test_job: Job | None = None
        form = QFormLayout(self)
        self.simplified = QCheckBox("中文歌词自动转为简体（已有项目可在“歌词行”菜单中转换）")
        self.simplified.setChecked(config.simplified_chinese_lyrics())
        form.addRow(self.simplified)
        self.experimental = QCheckBox('启用实验性大语言模型功能（修正歌词）')
        self.experimental.setChecked(config.experimental_llm())
        self.api_url = QLineEdit(config.llm_base_url())
        self.api_url.setPlaceholderText("例如 http://127.0.0.1:8000/v1")
        self.api_url.setToolTip("兼容 Chat Completions 的基础地址（含 /v1），也可填写完整的 /chat/completions 地址")
        self.key = QLineEdit(config.llm_api_key() or "")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('服务商 API 密钥；本地无鉴权服务可留空')
        self.model = QLineEdit(config.OPENROUTER_MODEL)
        form.addRow(self.experimental)
        description = QLabel(
            '通过你指定的兼容 API 比对和修正歌词，结果请逐项核对。远程服务会接收歌词和识别文本，费用由服务商决定；也可填写本机模型服务地址。\n'
            '这些设置不影响本地音频 AI 模型（人声分离、识别与对齐）。\n'
            '设置保存位置：' + str(config.CONFIG_DIR / ".env"))
        description.setWordWrap(True)
        description.setMinimumHeight(description.fontMetrics().lineSpacing() * 6)
        form.addRow(description)
        form.addRow('API 基础地址', self.api_url)
        form.addRow('API 密钥', self.key)
        form.addRow('歌词修正模型', self.model)
        self.test_status = QLabel("可使用当前填写的配置测试连接，无需先保存；测试只发送简短文本。")
        self.test_status.setWordWrap(True)
        self.test_status.setMinimumHeight(self.test_status.fontMetrics().lineSpacing() * 3)
        self.test_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.test_status.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(self.test_status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        self.buttons = buttons
        self.test_button = buttons.addButton("测试连接", QDialogButtonBox.ButtonRole.ActionRole)
        self.test_button.clicked.connect(self._test_connection)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.experimental.toggled.connect(self._toggle_llm)
        self._toggle_llm(self.experimental.isChecked())
        for field in (self.api_url, self.key, self.model):
            field.textChanged.connect(self._clear_test_result)
        self.resize(740, 0)

    def _toggle_llm(self, on: bool) -> None:
        on = on and self.test_job is None
        self.api_url.setEnabled(on)
        self.key.setEnabled(on)
        self.model.setEnabled(on)
        self.test_button.setEnabled(on)

    def _clear_test_result(self) -> None:
        self.test_status.setText("配置已修改，可点击“测试连接”重新验证。")
        self.test_status.setStyleSheet("")

    def _test_connection(self) -> None:
        if self.test_job is not None:
            return
        base_url = self.api_url.text().strip() or config.DEFAULT_LLM_BASE_URL
        key, model = self.key.text().strip(), self.model.text().strip()
        try:
            config.llm_chat_url(base_url)
            if not model:
                raise ValueError("请填写歌词修正模型名称。")
        except ValueError as exc:
            self.test_status.setText(f"测试失败：{exc}")
            self.test_status.setStyleSheet("color: #a52b21")
            return
        from autokaraoke.llm import test_connection

        self.test_job = Job("API 连接测试", lambda _log: test_connection(base_url, key, model), self)
        self.test_job.succeeded.connect(self._test_result)
        self.test_job.failed.connect(self._test_error)
        self.test_job.finished.connect(self._test_finished)
        self.test_status.setText("正在测试连接及模型 JSON 输出，请稍候（请求超时约 30 秒）…")
        self.test_status.setStyleSheet("")
        self.test_button.setText("正在测试…")
        self.experimental.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setEnabled(False)
        self._toggle_llm(self.experimental.isChecked())
        self.test_job.start()

    @Slot(object)
    def _test_result(self, result) -> None:
        self.test_status.setText(f"{result.message}\n用时 {result.elapsed:.1f} 秒。")
        self.test_status.setToolTip(self.test_status.text())
        self.test_status.setStyleSheet("color: #18713a" if result.success else "color: #a52b21")
        self.resize(self.width(), self.sizeHint().height())

    @Slot(str)
    def _test_error(self, error: str) -> None:
        self.test_status.setText("测试失败，请检查服务配置后重试。")
        self.test_status.setStyleSheet("color: #a52b21")

    @Slot()
    def _test_finished(self) -> None:
        job, self.test_job = self.test_job, None
        if job is not None:
            job.deleteLater()
        self.experimental.setEnabled(True)
        self.test_button.setText("测试连接")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setEnabled(True)
        self._toggle_llm(self.experimental.isChecked())

    def reject(self) -> None:
        if self.test_job is None:
            super().reject()

    def closeEvent(self, event) -> None:
        if self.test_job is not None:
            event.ignore()
        else:
            super().closeEvent(event)

    def accept(self) -> None:
        if self.test_job is not None:
            return
        base_url = self.api_url.text().strip() or config.DEFAULT_LLM_BASE_URL
        try:
            config.llm_chat_url(base_url)
        except ValueError as exc:
            QMessageBox.warning(self, "API 地址无效", str(exc))
            return
        key = self.key.text().strip()
        model = self.model.text().strip() or "deepseek/deepseek-v4.1-flash"
        save_settings({"AUTOKARAOKE_LLM_BASE_URL": base_url, "AUTOKARAOKE_LLM_API_KEY": key,
                       "OPENROUTER_API_KEY": "", "AUTOKARAOKE_LLM_MODEL": model,
                       "AUTOKARAOKE_SIMPLIFIED_CHINESE": "1" if self.simplified.isChecked() else "0",
                       "AUTOKARAOKE_EXPERIMENTAL_LLM": "1" if self.experimental.isChecked() else ""})
        config.OPENROUTER_MODEL = model
        super().accept()
