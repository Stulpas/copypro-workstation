"""Paper sizes and output folders. Excel data stays the source of truth."""

from __future__ import annotations

import os

from copypro_qt.legacy import ct

FALLBACK_MM = {
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "A6": (105.0, 148.0),
    "A3": (297.0, 420.0),
    "SRA4": (225.0, 320.0),
    "SRA3": (320.0, 450.0),
    "10×15 cm (102×152)": (102.0, 152.0),
}


def paper_names():
    names = list(ct.PAPER_SIZES_MM.keys())
    custom = ct.load_custom_sizes()
    for name in custom:
        if name not in names:
            names.append(name)
    if not names:
        names = list(FALLBACK_MM.keys())
    return names


def paper_size_raw(name, custom_pair=None):
    """Stored W×H in mm, without portrait/landscape swap (for item presets)."""
    custom = ct.load_custom_sizes()
    if custom_pair and name == "Custom…":
        width, height = custom_pair
    elif name in ct.PAPER_SIZES_MM:
        width, height = ct.PAPER_SIZES_MM[name]
    elif name in custom:
        width, height = custom[name]
    else:
        width, height = FALLBACK_MM.get(name, (210.0, 297.0))
    return float(width), float(height)


def paper_mm(name, orientation="Portrait", custom_pair=None):
    width, height = paper_size_raw(name, custom_pair)
    if orientation == "Landscape":
        return max(width, height), min(width, height)
    return min(width, height), max(width, height)


def target_px(name, dpi, orientation="Portrait", custom_pair=None):
    width, height = paper_mm(name, orientation, custom_pair)
    return ct.mm_to_px(width, dpi), ct.mm_to_px(height, dpi), width, height


def resolve_dir(mode, source_path, chosen, subfolder):
    if mode == "choose":
        if not chosen:
            raise ValueError("Choose an output folder.")
        os.makedirs(chosen, exist_ok=True)
        return chosen
    parent = os.path.dirname(os.path.abspath(source_path))
    if mode == "same":
        return parent
    folder = os.path.join(parent, (subfolder or "output").strip() or "output")
    os.makedirs(folder, exist_ok=True)
    return folder
