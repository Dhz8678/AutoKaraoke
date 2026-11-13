"""Render settings: font, colours, layout, ball, background, with a live preview."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from autokaraoke.export.ass import AssStyle, chinese_typography, save_default_style
from autokaraoke.gui.lyrics_panel import fmt_time
from autokaraoke.gui.preview import KaraokePreview
from autokaraoke.project import Project

COLORS = (
    ("sung_color", '已演唱'),
    ("unsung_color", '未演唱'),
    ("next_color", '下一行'),
    ("backing_color", '和声'),
    ("outline_color", '描边'),
    ("background_color", '背景'),
    ("ball_color", '跳动小球'),
)
RESOLUTIONS = (("1080p (1920×1080)", 1920, 1080), ("720p (1280×720)", 1280, 720))


class ColorButton(QPushButton):
    def __init__(self, color: str, on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self.setFixedWidth(90)
        self.clicked.connect(self._pick)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self.color = QColor(color).name().upper()
        c = QColor(self.color)
        text = "#000" if 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue() > 140 else "#fff"
        self.setText(self.color)
        self.setStyleSheet(f"background: {self.color}; color: {text}")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.color), self, '选择颜色')
        if c.isValid():
            self.set_color(c.name())
            self._on_change()


class RenderDialog(QDialog):
    def __init__(self, project: Project, style: AssStyle, output: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle('生成视频')
        self.project = project
        self._style = AssStyle.from_dict(style.to_dict())
        st = self._style
        v = QVBoxLayout(self)

        self.preview = KaraokePreview()
        self.preview.setMinimumHeight(200)
        self.preview.set_project(project)
        v.addWidget(self.preview, 1)
        self.time = QSlider(Qt.Orientation.Horizontal)
        self.time.setRange(0, int(max(project.duration, 1.0) * 10))
        first = next((sp[0] for ln in project.lines if (sp := ln.span())), 0.0)
        self.time.setValue(int((first + 1.0) * 10))
        self.time_label = QLabel()
        row = QHBoxLayout()
        row.addWidget(QLabel('预览位置'))
        row.addWidget(self.time, 1)
        row.addWidget(self.time_label)
        v.addLayout(row)

        cols = QHBoxLayout()
        text_box = QGroupBox('文字')
        form = QFormLayout(text_box)
        self.font = QFontComboBox()
        self.font.setCurrentFont(QFont(st.font))
        self.size = QSpinBox()
        self.size.setRange(30, 160)
        self.size.setValue(st.size)
        self.size.setSuffix(" px")
        self.size.setToolTip('1080p 分辨率下的字体大小')
        self.bold = QCheckBox('粗体')
        self.bold.setChecked(st.bold)
        self.outline = QSpinBox()
        self.outline.setRange(0, 12)
        self.outline.setValue(round(st.outline))
        self.outline.setSuffix(" px")
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0, 24)
        self.spacing.setDecimals(1)
        self.spacing.setSingleStep(.5)
        self.spacing.setSuffix(" px")
        self.spacing.setValue(st.spacing)
        self.spacing.setToolTip("1080p 基准下的字间距；预览与导出视频使用相同设置")
        self.line_spacing = QDoubleSpinBox()
        self.line_spacing.setRange(1.2, 3.0)
        self.line_spacing.setDecimals(1)
        self.line_spacing.setSingleStep(.1)
        self.line_spacing.setSuffix(" 倍")
        self.line_spacing.setValue(st.line_spacing)
        self.layout_ = QComboBox()
        self.layout_.addItem('双行交替', "alternate")
        self.layout_.addItem('当前行在上，下一行在下', "scroll")
        self.layout_.setCurrentIndex(max(0, self.layout_.findData(st.layout)))
        self.layout_.setToolTip('双行交替：上下两行轮流演唱，已唱完的一行会提前换成下一句，保持歌词连续显示')
        self.ball = QCheckBox('在音节上显示跳动小球')
        self.ball.setChecked(st.ball)
        self.countdown = QCheckBox('长间奏后显示倒计时圆点')
        self.countdown.setChecked(st.countdown)
        form.addRow('字体', self.font)
        size_row = QHBoxLayout()
        size_row.addWidget(self.size)
        size_row.addWidget(self.bold)
        size_row.addWidget(QLabel('描边'))
        size_row.addWidget(self.outline)
        form.addRow('字号', size_row)
        spacing_row = QHBoxLayout()
        spacing_row.addWidget(self.spacing)
        spacing_row.addWidget(QLabel("行距"))
        spacing_row.addWidget(self.line_spacing)
        form.addRow("字距", spacing_row)
        form.addRow('布局', self.layout_)
        form.addRow(self.ball)
        form.addRow(self.countdown)
        cols.addWidget(text_box, 3)

        color_box = QGroupBox('颜色')
        grid = QGridLayout(color_box)
        self.colors: dict[str, ColorButton] = {}
        for k, (key, label) in enumerate(COLORS):
            btn = ColorButton(getattr(st, key), self._changed)
            self.colors[key] = btn
            grid.addWidget(QLabel(label), k % 4, (k // 4) * 2)
            grid.addWidget(btn, k % 4, (k // 4) * 2 + 1)
        cols.addWidget(color_box, 2)
        v.addLayout(cols)

        out_box = QGroupBox('视频')
        form = QFormLayout(out_box)
        self.background = QLineEdit(st.background_image)
        self.background.setPlaceholderText('留空则使用纯色背景')
        bg_row = QHBoxLayout()
        bg_row.addWidget(self.background, 1)
        browse = QPushButton('浏览…')
        browse.clicked.connect(self._browse_background)
        bg_row.addWidget(browse)
        form.addRow('背景图片', bg_row)
        self.resolution = QComboBox()
        for label, w, h in RESOLUTIONS:
            self.resolution.addItem(label, (w, h))
        self.resolution.setCurrentIndex(0 if st.height >= 1080 else 1)
        form.addRow('分辨率', self.resolution)
        self.output = QLineEdit(str(output))
        out_row = QHBoxLayout()
        out_row.addWidget(self.output, 1)
        browse = QPushButton('浏览…')
        browse.clicked.connect(self._browse_output)
        out_row.addWidget(browse)
        form.addRow('输出文件', out_row)
        v.addWidget(out_box)

        buttons = QDialogButtonBox()
        self.btn_default = buttons.addButton('保存为默认样式', QDialogButtonBox.ButtonRole.ResetRole)
        self.btn_default.setToolTip('将此样式用于新项目和未指定样式的项目')
        self.btn_default.clicked.connect(self._save_default)
        self.btn_save = buttons.addButton('保存样式', QDialogButtonBox.ButtonRole.ApplyRole)
        self.btn_save.setToolTip('仅将样式保存到项目，暂不生成视频')
        self.btn_save.clicked.connect(lambda: self.done(2))
        self.btn_chinese = buttons.addButton('中文舒适样式', QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_chinese.setToolTip("使用中文无衬线字体、较轻描边和宽松字距、行距，保留其他设置")
        self.btn_chinese.clicked.connect(self._chinese_style)
        buttons.addButton('生成视频', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

        self.font.currentFontChanged.connect(self._changed)
        for w in (self.size, self.outline, self.spacing, self.line_spacing):
            w.valueChanged.connect(self._changed)
        for w in (self.bold, self.ball, self.countdown):
            w.toggled.connect(self._changed)
        self.layout_.currentIndexChanged.connect(self._changed)
        self.time.valueChanged.connect(self._time_changed)
        self._changed()
        self._time_changed()
        self.resize(900, 720)

    # ---- values -----------------------------------------------------------

    def style(self) -> AssStyle:
        st = AssStyle.from_dict(self._style.to_dict())
        st.font = self.font.currentFont().family()
        st.size = self.size.value()
        st.bold = self.bold.isChecked()
        st.outline = float(self.outline.value())
        st.spacing = self.spacing.value()
        st.line_spacing = self.line_spacing.value()
        st.shadow = min(st.shadow, st.outline) if st.outline else 0.0
        st.layout = self.layout_.currentData()
        st.ball = self.ball.isChecked()
        st.countdown = self.countdown.isChecked()
        for key, btn in self.colors.items():
            setattr(st, key, btn.color)
        st.background_image = self.background.text().strip()
        st.width, st.height = self.resolution.currentData()
        return st

    def output_path(self) -> Path:
        return Path(self.output.text().strip())

    # ---- slots ------------------------------------------------------------

    def _changed(self, *_args) -> None:
        self.preview.set_style(self.style())

    def _chinese_style(self) -> None:
        st = chinese_typography(self.style())
        self._style.shadow = st.shadow
        self.font.setCurrentFont(QFont(st.font))
        self.size.setValue(st.size)
        self.bold.setChecked(st.bold)
        self.outline.setValue(round(st.outline))
        self.spacing.setValue(st.spacing)
        self.line_spacing.setValue(st.line_spacing)
        self._changed()

    def _time_changed(self) -> None:
        t = self.time.value() / 10
        self.time_label.setText(fmt_time(t))
        self.preview.set_time(t)

    def _browse_background(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, '背景图片', self.background.text(),
                                           '图片 (*.png *.jpg *.jpeg *.webp)')
        if f:
            self.background.setText(f)

    def _browse_output(self) -> None:
        f, _ = QFileDialog.getSaveFileName(self, '生成视频', self.output.text(), '视频 (*.mkv *.mp4)')
        if f:
            self.output.setText(f)

    def _save_default(self) -> None:
        st = self.style()
        st.background_image = ""  # specific to a song
        path = save_default_style(st)
        self.btn_default.setText('已保存为默认样式 ✓')
        self.btn_default.setToolTip(str(path))
