"""Launch the CopyPro workstation from source (`python -m copypro_qt`)."""

from __future__ import annotations

import os
import sys


def _prefer_project_venv():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    python = os.path.join(root, "venv", "bin", "python")
    if os.name == "nt":
        python = os.path.join(root, "venv", "Scripts", "python.exe")
    if not os.path.exists(python):
        return
    try:
        same = os.path.samefile(sys.executable, python)
    except OSError:
        same = os.path.normcase(sys.executable) == os.path.normcase(python)
    if not same:
        os.execv(python, [python, os.path.abspath(sys.argv[0]), *sys.argv[1:]])


def main():
    if not getattr(sys, "frozen", False):
        _prefer_project_venv()
    try:
        from copypro_update_support import ensure_installed_in_appdata

        if ensure_installed_in_appdata():
            return 0
    except Exception:
        pass

    from PySide6.QtWidgets import QApplication

    from copypro_qt.shell import Workstation

    app = QApplication(sys.argv)
    app.setApplicationName("CopyPro Tools")
    window = Workstation()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
