"""GUI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from autokaraoke.gui.main_window import MainWindow


ICON = Path(__file__).parent / "assets" / "icon.png"


def _log_without_console() -> None:
    """Without a console (pythonw on Windows) stdout/stderr are None and any print fails."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from autokaraoke import config

    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(config.CONFIG_DIR / "autokaraoke.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def main(project: Path | None = None) -> int:
    _log_without_console()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("AutoKaraoke")
    app.setDesktopFileName("autokaraoke")
    app.setStyle("Fusion")
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    win = MainWindow()
    win.show()
    if project is not None:
        win.open_project(Path(project))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
