"""GUI entry point."""

from __future__ import annotations

import sys
import os
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from autokaraoke.gui.main_window import MainWindow


ICON = Path(__file__).parent / "assets" / "icon.png"


def install_chinese_translation(app: QApplication) -> None:
    """Translate Qt's standard buttons, file dialogs and colour pickers too."""
    QLocale.setDefault(QLocale("zh_CN"))
    preferred = ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "SimSun")
    families = set(QFontDatabase.families())
    if sys.platform == "win32" and not any(name in families for name in preferred):
        # Some Windows Qt backends (including offscreen previews) start with an
        # empty font database; explicitly load the existing system Chinese font.
        fonts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
        for filename in ("msyh.ttc", "simsun.ttc"):
            if QFontDatabase.addApplicationFont(str(fonts / filename)) >= 0:
                break
        QFontDatabase.addApplicationFont(str(fonts / "seguisym.ttf"))
        families = set(QFontDatabase.families())
    for family in preferred:
        if family in families:
            app.setFont(QFont(family, app.font().pointSize() if app.font().pointSize() > 0 else 9))
            break
    translator = QTranslator(app)
    if translator.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    app._chinese_translator = translator


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
    install_chinese_translation(app)
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
