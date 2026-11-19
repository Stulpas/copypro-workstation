"""Print-layout sheet composition. Same modes as PrintLayoutTab."""

from __future__ import annotations

import gc
import io
import math
import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from copypro_qt.imaging import resize_pillow
from copypro_qt.legacy import ct


def metrics(paper, item, cols, rows, spacing, bleed):
    pw, ph = paper
    iw, ih = max(0.1, float(item[0])), max(0.1, float(item[1]))
    cols, rows = max(1, int(cols)), max(1, int(rows))
    gap = max(0.0, float(spacing))
    bleed = max(0.0, float(bleed))
    gw = cols * iw + (cols - 1) * gap + 2 * bleed
    gh = rows * ih + (rows - 1) * gap + 2 * bleed
    x0 = (pw - gw) / 2 + bleed
    y0 = (ph - gh) / 2 + bleed
    return pw, ph, iw, ih, cols, rows, gap, bleed, x0, y0, gw, gh


def layout_fits(paper, item, cols, rows, spacing, bleed):
    pw, ph, _iw, _ih, _c, _r, _g, _b, _x0, _y0, gw, gh = metrics(paper, item, cols, rows, spacing, bleed)
    return gw <= pw + 1e-6 and gh <= ph + 1e-6, gw, gh, pw, ph


def best_layout(paper, item, spacing, bleed, allow_rotate=True):
    """Highest-capacity centred grid for a fixed item size (legacy calculator).

    Optionally tries rotating the item 90°. Keeps spacing/bleed as given.
    Returns (cols, rows, gap, bleed, item_w, item_h) or None.
    """
    pw, ph = paper
    iw, ih = max(0.1, float(item[0])), max(0.1, float(item[1]))
    gap = max(0.0, float(spacing))
    bleed = max(0.0, float(bleed))
    rotations = [False, True] if allow_rotate and abs(iw - ih) > 0.001 else [False]
    candidates = []
    for rotated in rotations:
        w, h = (ih, iw) if rotated else (iw, ih)
        step_w = w + gap
        step_h = h + gap
        cols = max(0, int((pw - 2 * bleed + gap) // step_w)) if step_w > 0 else 0
        rows = max(0, int((ph - 2 * bleed + gap) // step_h)) if step_h > 0 else 0
        copies = cols * rows
        used_w = cols * w + max(0, cols - 1) * gap + 2 * bleed
        used_h = rows * h + max(0, rows - 1) * gap + 2 * bleed
        waste = 1.0 - (used_w * used_h) / (pw * ph) if pw * ph else 1.0
        # Prefer more copies, then non-rotated, then less waste.
        candidates.append((copies, 0 if not rotated else 1, -waste, cols, rows, w, h))
    if not candidates:
        return None
    best = max(candidates)
    copies, _rot, _waste, cols, rows, w, h = best
    if copies >= 1:
        return cols, rows, gap, bleed, w, h
    # Nothing fits at full size — scale the item down to a 1×1.
    return shrink_to_fit(paper, (iw, ih), 1, 1, gap, bleed)


def shrink_to_fit(paper, item, cols, rows, spacing, bleed):
    """Reduce cols/rows, spacing/bleed, then item size until the grid fits.

    Returns (cols, rows, gap, bleed, item_w, item_h) or None.
    """
    cols, rows = max(1, int(cols)), max(1, int(rows))
    gap = max(0.0, float(spacing))
    bleed = max(0.0, float(bleed))
    iw, ih = max(0.1, float(item[0])), max(0.1, float(item[1]))
    pw, ph = paper
    for _ in range(120):
        fits, _gw, _gh, _pw, _ph = layout_fits(paper, (iw, ih), cols, rows, gap, bleed)
        if fits:
            return cols, rows, gap, bleed, iw, ih
        if cols > 1 or rows > 1:
            # Prefer dropping the dimension that overflows more.
            gw = cols * iw + (cols - 1) * gap + 2 * bleed
            gh = rows * ih + (rows - 1) * gap + 2 * bleed
            if gw - pw >= gh - ph and cols > 1:
                cols -= 1
            elif rows > 1:
                rows -= 1
            elif cols > 1:
                cols -= 1
            continue
        if gap > 0:
            gap = max(0.0, gap - 0.5)
            continue
        if bleed > 0:
            bleed = max(0.0, bleed - 0.5)
            continue
        # Scale item to fit remaining paper (keep aspect).
        avail_w = max(0.1, pw - 2 * bleed - (cols - 1) * gap)
        avail_h = max(0.1, ph - 2 * bleed - (rows - 1) * gap)
        scale = min(avail_w / iw, avail_h / ih)
        if scale >= 1.0 - 1e-9:
            break
        iw = max(0.1, round(iw * scale, 2))
        ih = max(0.1, round(ih * scale, 2))
        fits, *_ = layout_fits(paper, (iw, ih), cols, rows, gap, bleed)
        return (cols, rows, gap, bleed, iw, ih) if fits else None
    fits, *_ = layout_fits(paper, (iw, ih), cols, rows, gap, bleed)
    return (cols, rows, gap, bleed, iw, ih) if fits else None


def duplo_enabled(duplo):
    return bool(duplo) and (duplo.get("barcode") or duplo.get("reg"))


def duplo_zones(spec):
    """Barcode / REG rectangles in mm (x from left, y from top) for a Duplo DC-618 sheet."""
    duplo = spec.get("duplo") or {}
    if not duplo_enabled(duplo):
        return {}
    pw, ph = float(spec["paper"][0]), float(spec["paper"][1])
    bold = str(duplo.get("reg_style") or "standard").lower() == "bold"
    reg_w = 12.0 if bold else 20.0
    gap = 10.0 if bold else 5.0
    bar_w = 35.0
    top = 3.0 + float(duplo.get("v_offset") or 0)
    right = 3.0 + float(duplo.get("h_offset") or 0)
    band_h = 20.0
    y0 = min(max(0.0, top), max(0.0, ph - 1))
    y1 = min(ph, y0 + band_h)
    reg_x1 = max(0.0, pw - max(0.0, right))
    reg_x0 = reg_x1 - reg_w
    bar_x1 = reg_x0 - gap
    bar_x0 = bar_x1 - bar_w
    zones = {}
    if duplo.get("barcode"):
        zones["barcode"] = (bar_x0, y0, bar_x1, y1)
    if duplo.get("reg"):
        zones["reg"] = (reg_x0, y0, reg_x1, y1)
    if "barcode" in zones and "reg" in zones:
        zones["keepout"] = (bar_x0, y0, reg_x1, y1)
    elif zones:
        only = next(iter(v for k, v in zones.items()))
        zones["keepout"] = only
    return zones


def _rects_hit(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def duplo_blocked_slots(spec):
    """Grid slots whose trim+bleed box intersects the Duplo keep-out."""
    zones = duplo_zones(spec)
    keep = zones.get("keepout")
    if not keep:
        return set()
    pw, ph, iw, ih, cols, rows, gap, bleed, x0, y0, _gw, _gh = metrics(
        spec["paper"], spec["item"], spec["cols"], spec["rows"], spec["spacing"], spec["bleed"],
    )
    blocked = set()
    slots = cols * rows
    for slot in range(slots):
        row, col = divmod(slot, cols)
        left = x0 + col * (iw + gap) - bleed
        top = y0 + row * (ih + gap) - bleed
        cell = (left, top, left + iw + 2 * bleed, top + ih + 2 * bleed)
        if _rects_hit(cell, keep):
            blocked.add(slot)
    return blocked


def sheet_jobs(spec, files):
    """Sheet assignments plus whether this front gets Duplo marks.

    When marks overlap cells, those cells stay empty and images continue in the free cells.
    """
    files = list(files)
    cols = int(spec["cols"])
    rows = int(spec["rows"])
    mode = spec.get("mode") or "In order"
    duplex = bool(spec.get("duplex"))
    back_path = spec.get("back")
    duplo = spec.get("duplo") or {}
    if not duplo_enabled(duplo):
        return [
            (assignments, back, label, False)
            for assignments, back, label in page_specs(files, cols, rows, mode, duplex, back_path)
        ]
    blocked = duplo_blocked_slots(spec)
    every = str(duplo.get("apply") or "first") == "every"
    if not blocked:
        jobs = []
        front_i = 0
        for assignments, back, label in page_specs(files, cols, rows, mode, duplex, back_path):
            on = (not back) and (every or front_i == 0)
            jobs.append((assignments, back, label, on))
            if not back:
                front_i += 1
        return jobs
    return _pack_around_duplo(files, cols, rows, mode, duplex, back_path, blocked, every)


def _pack_around_duplo(files, cols, rows, mode, duplex, back_path, blocked, every):
    slots = max(1, cols * rows)
    jobs = []
    front_i = 0

    def blocked_now():
        return blocked if (every or front_i == 0) else set()

    def duplo_on():
        return every or front_i == 0

    if mode == "Repeat":
        step = 2 if duplex and not back_path else 1
        for start in range(0, len(files), step):
            mask = blocked_now()
            front_img = files[start]
            front = [None if slot in mask else front_img for slot in range(slots)]
            label = f"Sheet {front_i + 1}" + (" — front" if duplex or back_path else "")
            jobs.append((front, False, label, duplo_on()))
            if back_path:
                back = [None if slot in mask else back_path for slot in range(slots)]
                jobs.append((back, True, f"Sheet {front_i + 1} — back", False))
            elif duplex:
                back_img = files[start + 1] if start + 1 < len(files) else None
                back = [None if slot in mask or back_img is None else back_img for slot in range(slots)]
                jobs.append((back, True, f"Sheet {front_i + 1} — back", False))
            front_i += 1
        return jobs

    if mode == "Cut & stack" and not back_path:
        cards = [files[i:i + 2] for i in range(0, len(files), 2)]
        index = 0
        while index < len(cards):
            mask = blocked_now()
            front = [None] * slots
            back = [None] * slots
            placed = False
            for slot in range(slots):
                if slot in mask or index >= len(cards):
                    continue
                pair = cards[index]
                index += 1
                placed = True
                front[slot] = pair[0]
                back[slot] = pair[1] if len(pair) > 1 else None
            if not placed:
                break
            jobs.append((front, False, f"Sheet {front_i + 1} — front", duplo_on()))
            jobs.append((back, True, f"Sheet {front_i + 1} — back", False))
            front_i += 1
        return jobs

    index = 0
    while index < len(files):
        mask = blocked_now()
        front = [None] * slots
        placed = False
        for slot in range(slots):
            if slot in mask or index >= len(files):
                continue
            front[slot] = files[index]
            index += 1
            placed = True
        if not placed:
            break
        if back_path or duplex:
            jobs.append((front, False, f"Sheet {front_i + 1} — front", duplo_on()))
            if back_path:
                back = [None if item is None else back_path for item in front]
            else:
                back = [None] * slots
            jobs.append((back, True, f"Sheet {front_i + 1} — back", False))
        else:
            jobs.append((front, False, f"Sheet {front_i + 1}", duplo_on()))
        front_i += 1
    return jobs


def page_specs(files, cols, rows, mode, duplex, back_path=None):
    slots = max(1, int(cols) * int(rows))
    specs = []
    sheet_no = 1
    files = list(files)
    if back_path:
        back_fill = [back_path] * slots
        if mode == "Repeat":
            for front in files:
                specs.append(([front] * slots, False, f"Sheet {sheet_no} — front"))
                specs.append((back_fill, True, f"Sheet {sheet_no} — back"))
                sheet_no += 1
        elif mode == "Cut & stack":
            ns = math.ceil(len(files) / slots) if files else 0
            for page in range(ns):
                front = []
                for slot in range(slots):
                    idx = slot * ns + page
                    front.append(files[idx] if idx < len(files) else None)
                specs.append((front, False, f"Sheet {sheet_no} — front"))
                specs.append((back_fill, True, f"Sheet {sheet_no} — back"))
                sheet_no += 1
        else:
            for start in range(0, len(files), slots):
                chunk = files[start:start + slots]
                chunk = chunk + [None] * (slots - len(chunk))
                specs.append((chunk, False, f"Sheet {sheet_no} — front"))
                specs.append(([back_path if item else None for item in chunk], True, f"Sheet {sheet_no} — back"))
                sheet_no += 1
        return specs
    if mode == "Repeat":
        step = 2 if duplex else 1
        for start in range(0, len(files), step):
            label = f"Sheet {sheet_no} — front" if duplex else f"Sheet {sheet_no}"
            specs.append(([files[start]] * slots, False, label))
            if duplex:
                back = files[start + 1] if start + 1 < len(files) else None
                specs.append(([back] * slots, True, f"Sheet {sheet_no} — back"))
            sheet_no += 1
    elif mode == "Cut & stack":
        pairs = [files[i:i + 2] for i in range(0, len(files), 2)]
        ns = math.ceil(len(pairs) / slots) if pairs else 0
        for page in range(ns):
            front, back = [], []
            for slot in range(slots):
                idx = slot * ns + page
                pair = pairs[idx] if idx < len(pairs) else []
                front.append(pair[0] if pair else None)
                back.append(pair[1] if len(pair) > 1 else None)
            specs.append((front, False, f"Sheet {sheet_no} — front"))
            specs.append((back, True, f"Sheet {sheet_no} — back"))
            sheet_no += 1
    else:
        for start in range(0, len(files), slots):
            chunk = files[start:start + slots]
            chunk = chunk + [None] * (slots - len(chunk))
            specs.append((chunk, False, f"Sheet {sheet_no}"))
            sheet_no += 1
    return specs


def _load_item(path, dpi, item_mm, bleed, grayscale, brightness, preview, rotation, pdf_source, fractions=None):
    page_no = None
    if pdf_source:
        path, page_no = pdf_source[0], pdf_source[1]
    if page_no is not None:
        raw = ct.open_pdf_page(path, page_no, dpi=72 if preview else dpi).convert("RGB")
    else:
        with Image.open(path) as opened:
            corrected = ImageOps.exif_transpose(opened)
            if preview:
                limit = 900
                corrected.thumbnail((limit, limit), Image.Resampling.BILINEAR)
            raw = corrected.convert("RGB")
            if raw is corrected:
                raw = corrected.copy()
            if corrected is not opened:
                corrected.close()
    if rotation:
        turned = raw.rotate(int(rotation) % 360, expand=True)
        if turned is not raw:
            raw.close()
        raw = turned
    target = (ct.mm_to_px(item_mm[0], dpi), ct.mm_to_px(item_mm[1], dpi))
    # Fractions are relative to the already-rotated image (same as CropView).
    if fractions is not None:
        left, top, right, bottom = fractions
        iw, ih = raw.size
        box = (
            max(0, min(iw - 1, int(left * iw))),
            max(0, min(ih - 1, int(top * ih))),
            max(1, min(iw, int(right * iw))),
            max(1, min(ih, int(bottom * ih))),
        )
        cropped = raw.crop(box)
        raw.close()
        raw = cropped
    if raw.width and raw.height and (raw.width > raw.height) != (target[0] > target[1]):
        turned = raw.rotate(90, expand=True)
        raw.close()
        raw = turned
    fitted = resize_pillow(raw, target, None, False)
    raw.close()
    if grayscale:
        gray = ImageOps.grayscale(fitted).convert("RGB")
        fitted.close()
        fitted = gray
    if int(brightness) != 100:
        adjusted = ImageEnhance.Brightness(fitted).enhance(int(brightness) / 100.0)
        fitted.close()
        fitted = adjusted
    if bleed > 0:
        finished = ct.add_bleed_and_marks(fitted, bleed, dpi, False)
        fitted.close()
        fitted = finished
    return fitted


def _label_font(mark_len_px, dpi):
    font_size = max(14, int(max(mark_len_px, ct.mm_to_px(3.2, dpi)) * 0.9))
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, font_size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_cut_guides(sheet, pw, ph, iw, ih, cols, rows, gap, bleed, x0, y0, dpi, labels, style="outside"):
    if style == "in_bleed" and bleed > 0:
        _draw_cut_guides_in_bleed(sheet, iw, ih, cols, rows, gap, bleed, x0, y0, dpi)
        return
    draw = ImageDraw.Draw(sheet)
    safe_mm = 4.0
    desired_mm = 5.0
    artwork_gap_mm = 1.0
    safe = ct.mm_to_px(safe_mm, dpi)
    lw = max(1, ct.mm_to_px(0.25, dpi))
    xs = sorted({x0 + c * (iw + gap) for c in range(cols)} | {x0 + c * (iw + gap) + iw for c in range(cols)})
    ys = sorted({y0 + r * (ih + gap) for r in range(rows)} | {y0 + r * (ih + gap) + ih for r in range(rows)})
    sw, sh = sheet.size
    layout_right = x0 + cols * iw + max(0, cols - 1) * gap
    layout_bottom = y0 + rows * ih + max(0, rows - 1) * gap
    top_len = ct.mm_to_px(min(desired_mm, max(0, (y0 - bleed - artwork_gap_mm) - safe_mm)), dpi)
    bottom_len = ct.mm_to_px(min(desired_mm, max(0, (ph - safe_mm) - (layout_bottom + bleed + artwork_gap_mm))), dpi)
    left_len = ct.mm_to_px(min(desired_mm, max(0, (x0 - bleed - artwork_gap_mm) - safe_mm)), dpi)
    right_len = ct.mm_to_px(min(desired_mm, max(0, (pw - safe_mm) - (layout_right + bleed + artwork_gap_mm))), dpi)
    label_gap = max(2, ct.mm_to_px(0.8, dpi))

    def hlabel(cx, line_y, text, mark_len):
        font = _label_font(mark_len, dpi)
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        draw.text((int(cx - tw / 2), int(line_y - label_gap - th - box[1])), text, fill="black", font=font)

    def vlabel(line_x, cy, text, mark_len):
        font = _label_font(mark_len, dpi)
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        tile = Image.new("RGBA", (tw + 4, th + 4), (255, 255, 255, 0))
        ImageDraw.Draw(tile).text((2 - box[0], 2 - box[1]), text, fill="black", font=font)
        tile = tile.rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
        sheet.paste(tile, (int(line_x + label_gap), int(cy - tile.height / 2)), tile)

    for x in xs:
        px = ct.mm_to_px(x, dpi)
        text = f"{min(x, pw - x):.1f}"
        if top_len > 0:
            draw.line((px, safe, px, safe + top_len), fill="black", width=lw)
            if labels:
                vlabel(px, safe + top_len / 2, text, top_len)
        if bottom_len > 0:
            y2 = sh - safe
            draw.line((px, y2 - bottom_len, px, y2), fill="black", width=lw)
            if labels:
                vlabel(px, y2 - bottom_len / 2, text, bottom_len)
    for y in ys:
        py = ct.mm_to_px(y, dpi)
        text = f"{min(y, ph - y):.1f}"
        if left_len > 0:
            draw.line((safe, py, safe + left_len, py), fill="black", width=lw)
            if labels:
                hlabel(safe + left_len / 2, py, text, left_len)
        if right_len > 0:
            x2 = sw - safe
            draw.line((x2 - right_len, py, x2, py), fill="black", width=lw)
            if labels:
                hlabel(x2 - right_len / 2, py, text, right_len)


def _draw_cut_guides_in_bleed(sheet, iw, ih, cols, rows, gap, bleed, x0, y0, dpi):
    """Short L-marks in the bleed at each trim corner, next to the print."""
    draw = ImageDraw.Draw(sheet)
    outer = max(2, ct.mm_to_px(0.45, dpi))
    inner = max(1, ct.mm_to_px(0.22, dpi))
    gap_px = max(1, ct.mm_to_px(0.35, dpi))
    mlen = max(1, ct.mm_to_px(min(4.0, max(0.8, bleed - 0.35)), dpi))
    for row in range(rows):
        for col in range(cols):
            left = x0 + col * (iw + gap)
            top = y0 + row * (ih + gap)
            right = left + iw
            bottom = top + ih
            for cx, cy, dx, dy in (
                (left, top, -1, -1), (right, top, 1, -1),
                (left, bottom, -1, 1), (right, bottom, 1, 1),
            ):
                px, py = ct.mm_to_px(cx, dpi), ct.mm_to_px(cy, dpi)
                for fill, width in (("black", outer), ("white", inner)):
                    draw.line((px + dx * gap_px, py, px + dx * (gap_px + mlen), py), fill=fill, width=width)
                    draw.line((px, py + dy * gap_px, px, py + dy * (gap_px + mlen)), fill=fill, width=width)


def _barcode_payload(text):
    return "".join(
        ch for ch in str(text or "").upper().strip("*")
        if ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-.$/+% "
    ).strip()


def _barcode_font(px):
    candidates = []
    try:
        import barcode
        candidates.append(os.path.join(os.path.dirname(barcode.__file__), "fonts", "DejaVuSansMono.ttf"))
    except Exception:
        pass
    candidates.extend(("DejaVuSansMono.ttf", "DejaVuSans.ttf", "Arial.ttf"))
    for name in candidates:
        try:
            return ImageFont.truetype(name, max(8, int(px)))
        except Exception:
            continue
    return ImageFont.load_default()


def _code39_image(text):
    """Code 39 bars only. Human-readable text is drawn underneath, never in the bars."""
    payload = _barcode_payload(text)
    if not payload:
        return None
    try:
        from barcode import Code39
        from barcode.writer import ImageWriter
    except Exception:
        return None
    code = Code39(payload, writer=ImageWriter(), add_checksum=False)
    buf = io.BytesIO()
    try:
        code.write(buf, options={
            "module_width": 0.25,
            "module_height": 12.0,
            "quiet_zone": 0.4,
            "write_text": False,
        })
        buf.seek(0)
        image = Image.open(buf).convert("RGB")
        return image.copy()
    except Exception:
        return None
    finally:
        buf.close()


def _draw_duplo(sheet, spec, dpi):
    zones = duplo_zones(spec)
    if not zones:
        return
    duplo = spec.get("duplo") or {}
    draw = ImageDraw.Draw(sheet)
    keep = zones.get("keepout")
    if keep:
        box = tuple(ct.mm_to_px(v, dpi) for v in keep)
        draw.rectangle(box, fill="white")
    bold = str(duplo.get("reg_style") or "standard").lower() == "bold"
    reg = zones.get("reg")
    if reg:
        x0, y0, x1, y1 = (ct.mm_to_px(v, dpi) for v in reg)
        stroke = max(2, ct.mm_to_px(2.2 if bold else 1.0, dpi))
        inset = max(1, ct.mm_to_px(1.5, dpi))
        arm = int(min(x1 - x0, y1 - y0) * (0.85 if bold else 0.65))
        # Top-right corner mark (┐), sitting inside the REG zone.
        draw.line((x1 - inset - arm, y0 + inset, x1 - inset, y0 + inset), fill="black", width=stroke)
        draw.line((x1 - inset, y0 + inset, x1 - inset, y0 + inset + arm), fill="black", width=stroke)
    bar = zones.get("barcode")
    if bar:
        payload = _barcode_payload(duplo.get("text") or "000")
        image = _code39_image(payload)
        if image is not None:
            rotation = int(duplo.get("rotation") or 0) % 360
            if rotation:
                image = image.rotate(rotation, expand=True)
            quiet = max(0.0, float(duplo.get("quiet") or 0))
            height = max(4.0, float(duplo.get("height") or 12))
            show_text = bool(duplo.get("show_text"))
            text_mm = 3.2 if show_text else 0.0
            x0, y0, x1, y1 = bar
            # Keep a strip under the bars so the digits never share the bar block.
            bar_bottom = min(y1 - 0.4 - text_mm, y0 + 1.0 + height)
            inner = (x0 + quiet, y0 + 1.0, x1 - quiet, bar_bottom)
            if inner[2] > inner[0] + 2 and inner[3] > inner[1] + 2:
                tw = max(1, ct.mm_to_px(inner[2] - inner[0], dpi))
                th = max(1, ct.mm_to_px(inner[3] - inner[1], dpi))
                fitted = image.resize((tw, th), Image.Resampling.NEAREST)
                left = ct.mm_to_px(inner[0], dpi)
                top = ct.mm_to_px(inner[1], dpi)
                sheet.paste(fitted, (left, top))
                if show_text:
                    text_top = top + th
                    text_h = max(1, ct.mm_to_px(text_mm, dpi))
                    draw.rectangle((left, text_top, left + tw, text_top + text_h), fill="white")
                    font = _barcode_font(text_h * 0.72)
                    box = draw.textbbox((0, 0), payload, font=font)
                    label_w = box[2] - box[0]
                    label_h = box[3] - box[1]
                    tx = left + max(0, (tw - label_w) // 2) - box[0]
                    ty = text_top + max(0, (text_h - label_h) // 2) - box[1]
                    draw.text((tx, ty), payload, fill="black", font=font)
                fitted.close()
            image.close()


def render_sheet(spec, assignments, back, preview=False, duplo_on=False):
    m = metrics(spec["paper"], spec["item"], spec["cols"], spec["rows"], spec["spacing"], spec["bleed"])
    pw, ph, iw, ih, cols, rows, gap, bleed, x0, y0, gw, gh = m
    dpi = 48 if preview else int(spec["dpi"])
    sheet = Image.new("RGB", (ct.mm_to_px(pw, dpi), ct.mm_to_px(ph, dpi)), "white")
    sources = spec.get("sources") or {}
    rotations = spec.get("rotations") or {}
    for slot, path in enumerate(assignments):
        if not path:
            continue
        row, col = divmod(slot, cols)
        if back:
            col = cols - 1 - col
        item = _load_item(
            path, dpi, (iw, ih), bleed, spec.get("grayscale"), spec.get("brightness", 100),
            preview, rotations.get(path, 0), sources.get(path), (spec.get("fractions") or {}).get(path),
        )
        try:
            tx = x0 + col * (iw + gap) - bleed
            ty = y0 + row * (ih + gap) - bleed
            sheet.paste(item, (ct.mm_to_px(tx, dpi), ct.mm_to_px(ty, dpi)))
        finally:
            item.close()
    if spec.get("cut_marks"):
        _draw_cut_guides(
            sheet, pw, ph, iw, ih, cols, rows, gap, bleed, x0, y0, dpi,
            spec.get("cut_labels"), spec.get("cut_mark_style", "outside"),
        )
    if duplo_on and not back:
        _draw_duplo(sheet, spec, dpi)
    if preview:
        draw = ImageDraw.Draw(sheet)
        for row in range(rows):
            for col in range(cols):
                left = x0 + col * (iw + gap)
                top = y0 + row * (ih + gap)
                draw.rectangle(
                    (ct.mm_to_px(left, dpi), ct.mm_to_px(top, dpi),
                     ct.mm_to_px(left + iw, dpi), ct.mm_to_px(top + ih, dpi)),
                    outline=(0, 90, 210),
                )
    return sheet


def write_pdf(spec, files, dest):
    m = metrics(spec["paper"], spec["item"], spec["cols"], spec["rows"], spec["spacing"], spec["bleed"])
    if m[-2] > m[0] + 1e-6 or m[-1] > m[1] + 1e-6:
        raise ValueError("The centered grid is larger than the selected paper.")
    specs = sheet_jobs(spec, files)
    if not specs:
        raise ValueError("Add at least one image.")
    pdf = ct.fitz.open()
    pw, ph = spec["paper"]
    try:
        for index, (assignments, back, _label, duplo_on) in enumerate(specs):
            sheet = render_sheet(spec, assignments, back, preview=False, duplo_on=duplo_on)
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
    return len(specs)
