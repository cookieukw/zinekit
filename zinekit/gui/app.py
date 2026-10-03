"""Start the editor: python -m zinekit (or zinekit gui FILES...)."""
from __future__ import annotations

import os
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QCursor, QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .. import i18n
    from ..engine import EngineError, get_plugin

    files = list(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("zinekit")
    app.setOrganizationName("zinekit")
    app.setApplicationDisplayName("zinekit")
    QGuiApplication.setDesktopFileName("zinekit")

    from .window import MainWindow, settings
    if not os.environ.get("ZINEKIT_LANG"):
        saved = settings().value("language")
        if saved:
            i18n.set_language(str(saved))

    from .util import app_icon
    app.setWindowIcon(app_icon())
    QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
    try:
        plugin = get_plugin()          # compiles the plugin on the first run (a few seconds)
    except EngineError as e:
        QApplication.restoreOverrideCursor()
        QMessageBox.critical(None, "zinekit", str(e))
        return 1
    QApplication.restoreOverrideCursor()
    windows = []

    def open_window(old=None):
        win = MainWindow(plugin, [] if old is not None else files)
        if old is not None:
            win.adopt(old)
        win.languageChanged.connect(lambda _code, w=win: QTimer.singleShot(0, lambda: relaunch(w)))
        win.show()
        windows.append(win)
        return win

    def relaunch(old) -> None:
        """Rebuild the window in the new language, keeping what was open."""
        open_window(old)
        old.close()
        QTimer.singleShot(0, lambda: old in windows and windows.remove(old))

    open_window()
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())
