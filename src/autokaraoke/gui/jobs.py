"""Run long pipeline steps off the UI thread."""

from __future__ import annotations

import traceback
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal


class Job(QThread):
    message = Signal(str)
    failed = Signal(str)
    succeeded = Signal(object)

    def __init__(self, title: str, fn: Callable[[Callable[[str], None]], object], parent=None):
        """``fn`` receives a ``log`` callback and returns a result passed to ``succeeded``."""
        super().__init__(parent)
        self.title = title
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(self.message.emit)
        except Exception as e:  # surfaced to the user in a dialog
            traceback.print_exc()
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.succeeded.emit(result)
