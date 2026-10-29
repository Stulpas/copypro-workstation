"""CopyPro workstation entry point.

Uses the project virtualenv when it exists, then opens the Qt shell.
The previous Tkinter app remains available as copypro_tools.py.
"""

import os
import runpy
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if __name__ == "__main__":
    runpy.run_module("copypro_qt", run_name="__main__")
