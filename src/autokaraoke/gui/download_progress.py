"""Visible package and byte progress above the installation log."""

import time

from PySide6.QtCore import QTimer, Signal, Slot
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from autokaraoke.component_progress import InstallProgress, format_bytes


class DownloadProgress(QWidget):
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        self.summary = QLabel()
        self.overall = QProgressBar()
        self.file_label = QLabel()
        self.file_label.setWordWrap(True)
        self.file = QProgressBar()
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.clicked.connect(self.cancelRequested.emit)
        for widget in (self.summary, self.overall, self.file_label, self.file):
            layout.addWidget(widget)
        layout.addWidget(self.cancel_button)
        self.task_title = ""
        self.component_mode = True
        self.cancel_button.hide()
        self.started_at = None
        self.stage_text = ""
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._elapsed)
        self.hide()

    def begin(self) -> None:
        self.task_title = "AI 运行组件安装"
        self.component_mode = True
        self.started_at = time.monotonic()
        self.clock.start()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.update_progress(InstallProgress(detail='正在准备 AI 运行组件…'))

    def begin_task(self, title: str) -> None:
        self.task_title = title
        self.component_mode = False
        self.started_at = time.monotonic()
        self.clock.start()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.task_message("任务已启动，其他冲突操作将在任务结束后恢复。")

    @Slot(str)
    def task_message(self, message: str) -> None:
        self.update_progress(InstallProgress(stage="running", detail=message))

    @Slot(object)
    def succeeded(self, _result=None) -> None:
        self.update_progress(InstallProgress(stage="complete", completed=1, total=1))
        self.cancel_button.hide()

    @Slot(str)
    def failed(self, message: str) -> None:
        self.update_progress(InstallProgress(stage="failed", detail=message))
        self.cancel_button.hide()

    @Slot()
    def cancelled(self) -> None:
        self.update_progress(InstallProgress(stage="cancelled", detail="任务已取消，可以继续操作。"))
        self.cancel_button.hide()

    def _elapsed(self) -> None:
        if self.started_at is not None:
            elapsed = int(time.monotonic() - self.started_at)
            self.summary.setText(f"{self.stage_text} · 已用时 {elapsed // 60:02d}:{elapsed % 60:02d}")

    @Slot(object)
    def update_progress(self, update: InstallProgress) -> None:
        self.show()
        stage = {"running": "任务进行中", "model": "正在下载 AI 模型", "processing": "正在处理音频／模型",
                 "cancelled": "任务已取消",
                 "waiting": '正在等待其他安装任务', "preparing": '正在下载并准备组件', "installing": '正在安装组件',
                 "complete": 'AI 运行组件已就绪' if self.component_mode else "任务已完成",
                 "failed": 'AI 运行组件安装失败' if self.component_mode else "任务失败"}[update.stage]
        if self.task_title:
            stage = f"{self.task_title} · {stage}"
        self.overall.setRange(0, update.total if update.total else 0)
        if update.total:
            self.overall.setValue(min(update.completed, update.total))
            self.overall.setFormat('%v / %m 步 — %p%' if update.stage == "processing" else '%v / %m 个组件 — %p%')
            self.summary.setText(f'{stage}（处理步骤）' if update.stage == "processing" else f'{stage}（按组件数量统计）')
        else:
            self.summary.setText(stage)
        if update.stage in ("failed", "cancelled"):
            self.overall.setRange(0, 100)
            self.overall.setValue(0)
            self.overall.setFormat('失败' if update.stage == "failed" else "已取消")
        elif update.stage == "complete":
            self.summary.setText(stage)
            self.overall.setFormat("已完成 — 100%")
        self.stage_text = self.summary.text()
        if update.stage in ("failed", "complete", "cancelled"):
            self.clock.stop()
        else:
            self._elapsed()
        has_file = bool(update.package and update.size and update.stage in ("preparing", "model"))
        self.file.setVisible(has_file)
        self.file_label.setVisible(has_file or bool(update.detail))
        if has_file:
            percent = min(100, update.downloaded * 100 / update.size)
            self.file.setRange(0, 1000)
            self.file.setValue(round(percent * 10))
            self.file.setFormat(f"{percent:.1f}%")
            speed = f" · {format_bytes(update.speed)}/s" if update.speed else ""
            self.file_label.setText(f"{update.package} · {format_bytes(update.downloaded)} / "
                                    f"{format_bytes(update.size)}{speed}"
                                    + (f"\n{update.detail}" if update.detail else ""))
        else:
            self.file_label.setText(update.detail)
