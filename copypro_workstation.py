"""CopyPro workstation entry point (used by PyInstaller and Windows builds).

The previous Tkinter app remains available as copypro_tools.py.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    # Import package modules so PyInstaller can freeze them (no dynamic runpy).
    from copypro_qt.__main__ import main as qt_main

    return qt_main()


if __name__ == "__main__":
    raise SystemExit(main())
