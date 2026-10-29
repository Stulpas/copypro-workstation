"""Polaroid frames. Presets and sheet packing match PolaroidMakerTab."""

from __future__ import annotations

import gc
import io
import math
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageOps

from copypro_qt.legacy import ct

PRESETS = OrderedDict([
    ("Classic 88 × 107 mm", (88.0, 107.0, 4.5, 5.0, 25.0, "Square")),
    ("Mini 54 × 86 mm", (54.0, 86.0, 4.0, 4.0, 20.0, "3:4")),
    ("Square 72 × 86 mm", (72.0, 86.0, 5.0, 5.0, 19.0, "Square")),
    ("Wide 108 × 86 mm", (108.0, 86.0, 4.5, 5.0, 19.0, "3:2")),
    ("Custom", (88.0, 107.0, 5.0, 5.0, 25.0, "Square")),
])
RATIOS = {"Square": 1.0, "3:4": 3 / 4, "2:3": 2 / 3, "3:2": 3 / 2, "4:5": 4 / 5}


def frame_values(outer_w, outer_h, side, top, bottom, ratio):
    ow, oh = max(10.0, outer_w), max(10.0, outer_h)
    side, top, bottom = max(0.0, side), max(0.0, top), max(0.0, bottom)
    aw, ah = max(1.0, ow - side * 2), max(1.0, oh - top - bottom)
    ratio = max(0.05, ratio)
    iw = aw
    ih = iw / ratio
    if ih > ah:
        ih = ah
        iw = ih * ratio
    return ow, oh, (ow - iw) / 2, top, iw, ih


def choose_layout(paper, frame, spacing, auto, cols, rows):
    ow, oh = frame[0], frame[1]
    spacing = max(0.0, spacing)
    margin = 5.0
    candidates = []
    for sw, sh in (paper, (paper[1], paper[0])):
        for rotated in (False, True):
            iw, ih = (oh, ow) if rotated else (ow, oh)
            fit_cols = max(0, int((sw - margin * 2 + spacing) // (iw + spacing)))
            fit_rows = max(0, int((sh - margin * 2 + spacing) // (ih + spacing)))
            used_w = fit_cols * iw + max(0, fit_cols - 1) * spacing
            used_h = fit_rows * ih + max(0, fit_rows - 1) * spacing
            waste = sw * sh - used_w * used_h
            candidates.append((fit_cols * fit_rows, not rotated, waste, sw, sh, rotated, fit_cols, fit_rows, iw, ih))
    if auto:
        best = max(candidates, key=lambda item: (item[0], item[1], item[2]))
        _cap, _prefer, _waste, sw, sh, rotated, cols, rows, iw, ih = best
        cols, rows = max(1, cols), max(1, rows)
    else:
        cols, rows = max(1, int(cols)), max(1, int(rows))
        fitting = [c for c in candidates if c[6] >= cols and c[7] >= rows]
        best = max(fitting or candidates, key=lambda item: (item[1], item[2]))
        _cap, _prefer, _waste, sw, sh, rotated, _c, _r, iw, ih = best
    total_w = cols * iw + max(0, cols - 1) * spacing
    total_h = rows * ih + max(0, rows - 1) * spacing
    return {
        "sw": sw, "sh": sh, "rotated": rotated, "cols": cols, "rows": rows,
        "iw": iw, "ih": ih, "spacing": spacing,
        "ox": (sw - total_w) / 2, "oy": (sh - total_h) / 2,
        "capacity": max(1, cols * rows),
        "fits": total_w <= sw - 2 and total_h <= sh - 2,
    }


def _draw_marks(draw, x, y, w, h, dpi, mode):
    if mode == "Crop marks":
        gap, length = ct.mm_to_px(1.5, dpi), ct.mm_to_px(4, dpi)
        lw = max(1, ct.mm_to_px(0.2, dpi))
        for cx, dx in ((x, -1), (x + w, 1)):
            for cy, dy in ((y, -1), (y + h, 1)):
                draw.line((cx + dx * gap, cy, cx + dx * (gap + length), cy), fill="black", width=lw)
                draw.line((cx, cy + dy * gap, cx, cy + dy * (gap + length)), fill="black", width=lw)
    else:
        lw = max(1, ct.mm_to_px(0.15, dpi))
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline="black", width=lw)


def _render_card(path, dpi, frame, rotation, pdf_source, preview, fractions=None):
    ow, oh, x, y, iw, ih = frame
    outer = Image.new("RGB", (max(1, ct.mm_to_px(ow, dpi)), max(1, ct.mm_to_px(oh, dpi))), "white")
    page_no = None
    if pdf_source:
        path, page_no = pdf_source[0], pdf_source[1]
    if page_no is not None:
        source = ct.open_pdf_page(path, page_no, dpi=72 if preview else dpi).convert("RGB")
    else:
        with Image.open(path) as opened:
            corrected = ImageOps.exif_transpose(opened)
            source = corrected.convert("RGB")
            if source is corrected:
                source = corrected.copy()
            if corrected is not opened:
                corrected.close()
    if rotation:
        source = source.rotate(int(rotation) % 360, expand=True)
    try:
        size = (max(1, ct.mm_to_px(iw, dpi)), max(1, ct.mm_to_px(ih, dpi)))
        if fractions:
            from copypro_qt.imaging import resize_pillow
            photo = resize_pillow(source, size, fractions, False)
        else:
            photo = ImageOps.fit(
                source,
                size,
                method=Image.Resampling.BILINEAR if preview else Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        outer.paste(photo, (ct.mm_to_px(x, dpi), ct.mm_to_px(y, dpi)))
        photo.close()
    finally:
        source.close()
    return outer


def render_sheet(files, start, layout, frame, dpi, mark_mode, rotations, sources, preview=False, fractions=None):
    sheet = Image.new("RGB", (ct.mm_to_px(layout["sw"], dpi), ct.mm_to_px(layout["sh"], dpi)), "white")
    draw = ImageDraw.Draw(sheet)
    used = 0
    for slot in range(layout["capacity"]):
        index = start + slot
        if index >= len(files):
            break
        path = files[index]
        card = _render_card(
            path, dpi, frame, rotations.get(path, 0), (sources or {}).get(path), preview,
            (fractions or {}).get(path),
        )
        try:
            if layout["rotated"]:
                turned = card.rotate(90, expand=True)
                card.close()
                card = turned
            col, row = slot % layout["cols"], slot // layout["cols"]
            x = ct.mm_to_px(layout["ox"] + col * (layout["iw"] + layout["spacing"]), dpi)
            y = ct.mm_to_px(layout["oy"] + row * (layout["ih"] + layout["spacing"]), dpi)
            sheet.paste(card, (x, y))
            _draw_marks(draw, x, y, card.width, card.height, dpi, mark_mode)
            used += 1
        finally:
            card.close()
    return sheet, used


def write_pdf(files, dest, layout, frame, mark_mode, rotations=None, sources=None, dpi=300, fractions=None):
    if not layout["fits"]:
        raise ValueError("Reduce rows, columns, spacing, or frame size.")
    pdf = ct.fitz.open()
    start = 0
    pages = 0
    try:
        while start < len(files):
            sheet, used = render_sheet(files, start, layout, frame, dpi, mark_mode, rotations or {}, sources or {}, False, fractions)
            if used <= 0:
                sheet.close()
                break
            buffer = io.BytesIO()
            try:
                sheet.save(buffer, "PNG", compress_level=2)
                page = pdf.new_page(width=layout["sw"] * 72 / 25.4, height=layout["sh"] * 72 / 25.4)
                page.insert_image(page.rect, stream=buffer.getvalue())
            finally:
                buffer.close()
                sheet.close()
            start += used
            pages += 1
            gc.collect(0)
        pdf.save(dest, garbage=3, deflate=True)
    finally:
        pdf.close()
    return pages or math.ceil(len(files) / layout["capacity"])
