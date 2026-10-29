"""Saddle-stitch booklet imposition: 2-up or 4-up (A6), outer-only bleed."""

from __future__ import annotations

import gc
import io
import math

from PIL import Image, ImageDraw

from copypro_qt.engines import layout as layout_engine
from copypro_qt.legacy import ct
from copypro_qt.paper import paper_mm, paper_size_raw

PRESETS = {
    "A4 Booklet on SRA3": {
        "sheet": "SRA3", "sheet_orient": "Landscape", "page": "A4", "layout": "2up",
    },
    "A4 Booklet on A3": {
        "sheet": "A3", "sheet_orient": "Landscape", "page": "A4", "layout": "2up",
    },
    "A5 Booklet on SRA4": {
        "sheet": "SRA4", "sheet_orient": "Landscape", "page": "A5", "layout": "2up",
    },
    "A5 Booklet on A4": {
        "sheet": "A4", "sheet_orient": "Landscape", "page": "A5", "layout": "2up",
    },
    "A6 Booklet on SRA4": {
        # Portrait sheet: 2×2 A6 pages (top half = first part of book, bottom = rest).
        "sheet": "SRA4", "sheet_orient": "Portrait", "page": "A6", "layout": "4up",
    },
    "A6 Booklet on A4": {
        "sheet": "A4", "sheet_orient": "Portrait", "page": "A6", "layout": "4up",
    },
    "Custom": None,
}

PAGE_MM = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "A6": (105.0, 148.0),
}


def page_size_mm(name, custom_pair=None):
    if custom_pair and name in ("Custom", "Custom…"):
        return float(custom_pair[0]), float(custom_pair[1])
    if name in PAGE_MM:
        return PAGE_MM[name]
    try:
        return paper_size_raw(name)
    except Exception:
        return PAGE_MM["A5"]


def pad_multiple(layout="2up"):
    return 8 if layout == "4up" else 4


def pad_length(count, layout="2up"):
    step = pad_multiple(layout)
    count = max(0, int(count))
    if count == 0:
        return step
    return int(math.ceil(count / step) * step)


def pad_keys(keys, layout="2up"):
    """Pad to 4/8 pages, keeping first = front cover and last = back cover.

    Extra blanks are inserted just before the back cover (not after it).
    """
    keys = list(keys)
    target = pad_length(len(keys), layout)
    need = target - len(keys)
    if need <= 0:
        return keys
    if len(keys) >= 2:
        # … content | blanks | back cover
        return keys[:-1] + [None] * need + [keys[-1]]
    return keys + [None] * need


def saddle_sides(pages):
    """Yield (front_left, front_right, back_left, back_right) for a 4n-page nest.

    With page 1 = front cover and page N = back cover, sheet 1 front is N|1.
    """
    pages = list(pages)
    n = len(pages)
    if n % 4:
        raise ValueError("Signature page count must be a multiple of 4.")
    sheets = n // 4
    for i in range(sheets):
        fl = n - 2 * i - 1
        fr = 2 * i
        bl = 2 * i + 1
        br = n - 2 * i - 2
        yield pages[fl], pages[fr], pages[bl], pages[br]


def imposed_sides_2up(keys):
    """2-up duplex sides: (left, right, label, is_back)."""
    padded = pad_keys(keys, "2up")
    sides = []
    sheet_no = 0
    for fl, fr, bl, br in saddle_sides(padded):
        sheet_no += 1
        sides.append((fl, fr, f"Sheet {sheet_no} — front", False))
        sides.append((bl, br, f"Sheet {sheet_no} — back", True))
    return sides


def imposed_sides_4up(keys):
    """4-up: top row = first half of book, bottom row = second half.

    Each side is (tl, tr, bl, br, label, is_back).
    """
    padded = pad_keys(keys, "4up")
    mid = len(padded) // 2
    top = padded[:mid]
    bottom = padded[mid:]
    top_sides = list(saddle_sides(top))
    bot_sides = list(saddle_sides(bottom))
    sides = []
    for index, ((tfl, tfr, tbl, tbr), (bfl, bfr, bbl, bbr)) in enumerate(zip(top_sides, bot_sides)):
        sheet_no = index + 1
        sides.append((tfl, tfr, bfl, bfr, f"Sheet {sheet_no} — front", False))
        sides.append((tbl, tbr, bbl, bbr, f"Sheet {sheet_no} — back", True))
    return sides


def imposed_sides(keys, layout="2up"):
    if layout == "4up":
        return imposed_sides_4up(keys)
    return imposed_sides_2up(keys)


def reader_spreads(keys, layout="2up"):
    """Reader-order facing pages after pad: [(left, right, label), ...].

    First file page = front cover (right-only); last file page = back cover (left-only).
    """
    pages = pad_keys(keys, layout)
    spreads = []
    if not pages:
        return spreads
    spreads.append((None, pages[0], "Front cover"))
    index = 1
    spread_no = 1
    while index < len(pages):
        left = pages[index]
        right = pages[index + 1] if index + 1 < len(pages) else None
        label = "Back cover" if index == len(pages) - 1 else f"Spread {spread_no}"
        spreads.append((left, right, label))
        spread_no += 1
        index += 2
    return spreads


def metrics_2up(paper, page, bleed, gap):
    """Trim slots centred; bleed only outside (not into spine)."""
    pw, ph = float(paper[0]), float(paper[1])
    iw, ih = max(0.1, float(page[0])), max(0.1, float(page[1]))
    bleed = max(0.0, float(bleed))
    gap = max(0.0, float(gap))
    # Outer bleed on left of left page and right of right page only.
    gw = 2 * iw + gap + 2 * bleed
    gh = ih + 2 * bleed
    x0 = (pw - gw) / 2 + bleed  # trim left of left page
    y0 = (ph - gh) / 2 + bleed
    return pw, ph, iw, ih, gap, bleed, x0, y0, gw, gh


def metrics_4up(paper, page, bleed, gap, row_gap=0.0):
    """2×2 trim grid; outer bleed only on sheet perimeter."""
    pw, ph = float(paper[0]), float(paper[1])
    iw, ih = max(0.1, float(page[0])), max(0.1, float(page[1]))
    bleed = max(0.0, float(bleed))
    gap = max(0.0, float(gap))
    row_gap = max(0.0, float(row_gap))
    gw = 2 * iw + gap + 2 * bleed
    gh = 2 * ih + row_gap + 2 * bleed
    x0 = (pw - gw) / 2 + bleed
    y0 = (ph - gh) / 2 + bleed
    return pw, ph, iw, ih, gap, row_gap, bleed, x0, y0, gw, gh


def metrics(paper, page, bleed, gap, layout="2up", row_gap=0.0):
    if layout == "4up":
        return metrics_4up(paper, page, bleed, gap, row_gap)
    return metrics_2up(paper, page, bleed, gap)


def fits(paper, page, bleed, gap, layout="2up", row_gap=0.0):
    if layout == "4up":
        pw, ph, *_rest, gw, gh = metrics_4up(paper, page, bleed, gap, row_gap)
    else:
        pw, ph, *_rest, gw, gh = metrics_2up(paper, page, bleed, gap)
    return gw <= pw + 1e-6 and gh <= ph + 1e-6, gw, gh, pw, ph


def _blank_rgb(spec, key):
    """RGB for blank slots, or None if key is real content. Auto-pad (None) = white."""
    colors = (spec or {}).get("blank_colors") or {}
    if key is None:
        return (255, 255, 255)
    if key in colors:
        r, g, b = colors[key]
        return int(r), int(g), int(b)
    if isinstance(key, str) and key.startswith("__blank__"):
        return (255, 255, 255)
    return None


def _solid_page(page_mm, dpi, rgb):
    w = max(1, ct.mm_to_px(page_mm[0], dpi))
    h = max(1, ct.mm_to_px(page_mm[1], dpi))
    return Image.new("RGB", (w, h), tuple(rgb))


def _load_trim(key, dpi, page_mm, grayscale, brightness, preview, rotation, pdf_source, fractions, spec=None):
    """Load at trim size only (no bleed expansion toward spine). Blank keys → solid fill."""
    rgb = _blank_rgb(spec, key)
    if rgb is not None:
        return _solid_page(page_mm, dpi, rgb)
    return layout_engine._load_item(
        key, dpi, page_mm, 0, grayscale, brightness, preview, rotation, pdf_source, fractions,
    )


def _paste_outer_bleed(sheet, item, trim_x_mm, trim_y_mm, iw, ih, bleed, dpi, outer_left, outer_right):
    """Paste trim image and extend only outer edges by bleed (reflect)."""
    tx = ct.mm_to_px(trim_x_mm, dpi)
    ty = ct.mm_to_px(trim_y_mm, dpi)
    sheet.paste(item, (tx, ty))
    if bleed <= 0:
        return
    b = ct.mm_to_px(bleed, dpi)
    w, h = item.size
    # Top / bottom always outer for both pages.
    top_strip = item.crop((0, 0, w, min(b, h))).transpose(Image.FLIP_TOP_BOTTOM)
    bot_strip = item.crop((0, max(0, h - b), w, h)).transpose(Image.FLIP_TOP_BOTTOM)
    sheet.paste(top_strip, (tx, ty - top_strip.height))
    sheet.paste(bot_strip, (tx, ty + h))
    top_strip.close()
    bot_strip.close()
    if outer_left:
        left = item.crop((0, 0, min(b, w), h)).transpose(Image.FLIP_LEFT_RIGHT)
        sheet.paste(left, (tx - left.width, ty))
        # Outer corners
        tl = item.crop((0, 0, min(b, w), min(b, h))).transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM)
        bl = item.crop((0, max(0, h - b), min(b, w), h)).transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM)
        sheet.paste(tl, (tx - tl.width, ty - tl.height))
        sheet.paste(bl, (tx - bl.width, ty + h))
        left.close()
        tl.close()
        bl.close()
    if outer_right:
        right = item.crop((max(0, w - b), 0, w, h)).transpose(Image.FLIP_LEFT_RIGHT)
        sheet.paste(right, (tx + w, ty))
        tr = item.crop((max(0, w - b), 0, w, min(b, h))).transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM)
        br = item.crop((max(0, w - b), max(0, h - b), w, h)).transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM)
        sheet.paste(tr, (tx + w, ty - tr.height))
        sheet.paste(br, (tx + w, ty + h))
        right.close()
        tr.close()
        br.close()


def render_side_2up(spec, left_key, right_key, preview=False):
    paper = spec["paper"]
    page = spec["page"]
    bleed = spec.get("bleed", 0)
    gap = spec.get("gap", 0)
    pw, ph, iw, ih, gap, bleed, x0, y0, _gw, _gh = metrics_2up(paper, page, bleed, gap)
    dpi = 48 if preview else int(spec.get("dpi", 300))
    sheet = Image.new("RGB", (ct.mm_to_px(pw, dpi), ct.mm_to_px(ph, dpi)), "white")
    sources = spec.get("sources") or {}
    rotations = spec.get("rotations") or {}
    fractions = spec.get("fractions") or {}
    for col, key in enumerate((left_key, right_key)):
        item = _load_trim(
            key, dpi, (iw, ih), spec.get("grayscale"), spec.get("brightness", 100),
            preview, rotations.get(key, 0) if key else 0,
            sources.get(key) if key else None, fractions.get(key) if key else None, spec,
        )
        try:
            trim_x = x0 + col * (iw + gap)
            _paste_outer_bleed(
                sheet, item, trim_x, y0, iw, ih, bleed, dpi,
                outer_left=(col == 0), outer_right=(col == 1),
            )
        finally:
            item.close()
    if spec.get("cut_marks"):
        layout_engine._draw_cut_guides(
            sheet, pw, ph, iw, ih, 2, 1, gap, bleed, x0, y0, dpi,
            spec.get("cut_labels"), spec.get("cut_mark_style", "outside"),
        )
    if preview:
        draw = ImageDraw.Draw(sheet)
        for col in range(2):
            left = x0 + col * (iw + gap)
            draw.rectangle(
                (ct.mm_to_px(left, dpi), ct.mm_to_px(y0, dpi),
                 ct.mm_to_px(left + iw, dpi), ct.mm_to_px(y0 + ih, dpi)),
                outline=(0, 90, 210),
            )
    return sheet


def render_side_4up(spec, tl, tr, bl, br, preview=False):
    paper = spec["paper"]
    page = spec["page"]
    bleed = spec.get("bleed", 0)
    gap = spec.get("gap", 0)
    row_gap = spec.get("row_gap", 0)
    pw, ph, iw, ih, gap, row_gap, bleed, x0, y0, _gw, _gh = metrics_4up(
        paper, page, bleed, gap, row_gap,
    )
    dpi = 48 if preview else int(spec.get("dpi", 300))
    sheet = Image.new("RGB", (ct.mm_to_px(pw, dpi), ct.mm_to_px(ph, dpi)), "white")
    sources = spec.get("sources") or {}
    rotations = spec.get("rotations") or {}
    fractions = spec.get("fractions") or {}
    slots = (
        (0, 0, tl), (1, 0, tr),
        (0, 1, bl), (1, 1, br),
    )
    for col, row, key in slots:
        item = _load_trim(
            key, dpi, (iw, ih), spec.get("grayscale"), spec.get("brightness", 100),
            preview, rotations.get(key, 0) if key else 0,
            sources.get(key) if key else None, fractions.get(key) if key else None, spec,
        )
        try:
            trim_x = x0 + col * (iw + gap)
            trim_y = y0 + row * (ih + row_gap)
            _paste_outer_bleed(
                sheet, item, trim_x, trim_y, iw, ih, bleed, dpi,
                outer_left=(col == 0), outer_right=(col == 1),
            )
            if bleed > 0:
                b = ct.mm_to_px(bleed, dpi)
                tx = ct.mm_to_px(trim_x, dpi)
                ty = ct.mm_to_px(trim_y, dpi)
                w, h = item.size
                if row == 0:
                    ImageDraw.Draw(sheet).rectangle(
                        (tx - b, ty + h, tx + w + b, ty + h + b), fill="white",
                    )
                else:
                    ImageDraw.Draw(sheet).rectangle(
                        (tx - b, ty - b, tx + w + b, ty), fill="white",
                    )
        finally:
            item.close()
    if preview:
        draw = ImageDraw.Draw(sheet)
        for col in range(2):
            for row in range(2):
                left = x0 + col * (iw + gap)
                top = y0 + row * (ih + row_gap)
                draw.rectangle(
                    (ct.mm_to_px(left, dpi), ct.mm_to_px(top, dpi),
                     ct.mm_to_px(left + iw, dpi), ct.mm_to_px(top + ih, dpi)),
                    outline=(0, 90, 210),
                )
    return sheet


def render_side(spec, *slots, preview=False):
    layout = spec.get("layout", "2up")
    if layout == "4up":
        tl, tr, bl, br = slots[:4]
        return render_side_4up(spec, tl, tr, bl, br, preview=preview)
    left, right = slots[:2]
    return render_side_2up(spec, left, right, preview=preview)


def render_reader_spread(spec, left_key, right_key, preview=True):
    """Simple facing-page preview (not imposed). Spine gap only when set in the job."""
    page = spec["page"]
    iw, ih = float(page[0]), float(page[1])
    gap = max(0.0, float(spec.get("gap") or 0))
    dpi = 96 if preview else int(spec.get("dpi", 300))
    pw = 2 * iw + gap
    ph = ih
    sheet = Image.new("RGB", (ct.mm_to_px(pw, dpi), ct.mm_to_px(ph, dpi)), "white")
    sources = spec.get("sources") or {}
    rotations = spec.get("rotations") or {}
    fractions = spec.get("fractions") or {}
    for col, key in enumerate((left_key, right_key)):
        item = _load_trim(
            key, dpi, (iw, ih), spec.get("grayscale"), spec.get("brightness", 100),
            preview, rotations.get(key, 0) if key else 0,
            sources.get(key) if key else None, fractions.get(key) if key else None, spec,
        )
        try:
            sheet.paste(item, (ct.mm_to_px(col * (iw + gap), dpi), 0))
        finally:
            item.close()
    draw = ImageDraw.Draw(sheet)
    for col in range(2):
        left = col * (iw + gap)
        draw.rectangle(
            (ct.mm_to_px(left, dpi), 0,
             ct.mm_to_px(left + iw, dpi), ct.mm_to_px(ih, dpi)),
            outline=(0, 90, 210),
        )
    # Hairline fold when pages abut (no mm spine gap).
    if gap <= 0:
        mid = ct.mm_to_px(iw, dpi)
        draw.line((mid, 0, mid, ct.mm_to_px(ih, dpi)), fill=(180, 180, 190), width=1)
    return sheet


def preview_sheets(spec, keys):
    layout = spec.get("layout", "2up")
    out = []
    if layout == "4up":
        for tl, tr, bl, br, label, _back in imposed_sides_4up(keys):
            sheet = render_side_4up(spec, tl, tr, bl, br, preview=True)
            try:
                buf = io.BytesIO()
                sheet.save(buf, "PNG")
                out.append((label, buf.getvalue()))
            finally:
                sheet.close()
    else:
        for left, right, label, _back in imposed_sides_2up(keys):
            sheet = render_side_2up(spec, left, right, preview=True)
            try:
                buf = io.BytesIO()
                sheet.save(buf, "PNG")
                out.append((label, buf.getvalue()))
            finally:
                sheet.close()
    return out


def preview_book(spec, keys):
    layout = spec.get("layout", "2up")
    out = []
    for left, right, label in reader_spreads(keys, layout):
        sheet = render_reader_spread(spec, left, right, preview=True)
        try:
            buf = io.BytesIO()
            sheet.save(buf, "PNG")
            out.append((label, buf.getvalue()))
        finally:
            sheet.close()
    return out


def write_pdf(spec, keys, dest):
    layout = spec.get("layout", "2up")
    ok, gw, gh, pw, ph = fits(
        spec["paper"], spec["page"], spec.get("bleed", 0), spec.get("gap", 0),
        layout, spec.get("row_gap", 0),
    )
    if not ok:
        raise ValueError(
            f"Booklet pages ({gw:.1f} × {gh:.1f} mm) do not fit the sheet ({pw:.0f} × {ph:.0f} mm)."
        )
    sides = imposed_sides(keys, layout)
    if not sides:
        raise ValueError("Add at least one page.")
    pdf = ct.fitz.open()
    pw, ph = spec["paper"]
    try:
        for index, side in enumerate(sides):
            if layout == "4up":
                tl, tr, bl, br, _label, _back = side
                sheet = render_side_4up(spec, tl, tr, bl, br, preview=False)
            else:
                left, right, _label, _back = side
                sheet = render_side_2up(spec, left, right, preview=False)
            buffer = io.BytesIO()
            try:
                sheet.save(buffer, "PNG", compress_level=3)
                page = pdf.new_page(width=pw * 72 / 25.4, height=ph * 72 / 25.4)
                page.insert_image(page.rect, stream=buffer.getvalue())
            finally:
                buffer.close()
                sheet.close()
            if index and index % 4 == 0:
                gc.collect(0)
        pdf.save(dest, garbage=3, deflate=True)
    finally:
        pdf.close()
    return len(sides)
