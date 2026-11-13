"""Run long pipeline steps off the UI thread."""

from __future__ import annotations

import traceback
from contextlib import nullcontext
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal


class Job(QThread):
    message = Signal(str)
    failed = Signal(str)
    succeeded = Signal(object)
    progress = Signal(object)
    cancelled = Signal()

    def __init__(self, title: str, fn: Callable[..., object], parent=None, *, with_progress=False,
                 capture_downloads=False):
        """``fn`` receives a ``log`` callback and returns a result passed to ``succeeded``."""
        super().__init__(parent)
        self.title = title
        self._fn = fn
        self._with_progress = with_progress
        self._capture_downloads = capture_downloads

    def run(self) -> None:
        from autokaraoke.task_progress import TaskCancelled, model_download_progress

        def check():
            if self.isInterruptionRequested():
                raise TaskCancelled()

        def log(message):
            check()
            self.message.emit(message)

        def progress(update):
            check()
            self.progress.emit(update)

        try:
            check()
            context = model_download_progress(progress, self.isInterruptionRequested) if self._capture_downloads else nullcontext()
            with context:
                if self._with_progress:
                    result = self._fn(log, progress)
                else:
                    result = self._fn(log)
            check()
        except TaskCancelled:
            self.cancelled.emit()
        except Exception as e:  # surfaced to the user in a dialog
            if self.isInterruptionRequested():
                self.cancelled.emit()
            else:
                traceback.print_exc()
                self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.succeeded.emit(result)
