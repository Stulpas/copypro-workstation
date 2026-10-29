"""Import the existing CopyPro module without launching its Tk window."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import copypro_tools as ct  # noqa: E402

__all__ = ["ct", "ROOT"]
