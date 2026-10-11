"""GUI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from autokaraoke.gui.main_window import MainWindow


def main(project: Path | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("AutoKaraoke")
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    if project is not None:
        win.open_project(Path(project))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
