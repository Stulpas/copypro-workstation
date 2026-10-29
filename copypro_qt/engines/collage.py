"""Collage packing. Uses the existing fit-page and photo-size placers."""

from __future__ import annotations

import io

from PIL import Image

from copypro_qt.legacy import ct


def placements(count, aspects, paper, mode, gap, margin, photo_size):
    if mode == "Photo size":
        items, _cols, _rows, _cell_w, _cell_h = ct.pack_photo_size_grid(
            count, photo_size[0], photo_size[1], paper[0], paper[1], gap, margin,
        )
        return items
    return ct.pack_fit_page(list(aspects), paper[0], paper[1], gap, margin)


def _open_tile(path, dpi, sources):
    source = (sources or {}).get(path)
    if source:
        file_path, page_no = source
        if page_no is not None:
            return ct.open_pdf_page(file_path, page_no, dpi=dpi).convert("RGB")
        path = file_path
    return ct.load_raster_source(path, {}, dpi=dpi)


def render_sheet(files, paper, items, dpi, bg, crops, preview=False, sources=None):
    sheet = Image.new("RGB", (ct.mm_to_px(paper[0], dpi), ct.mm_to_px(paper[1], dpi)), bg)
    resample = Image.Resampling.BILINEAR if preview else Image.Resampling.LANCZOS
    for index, x, y, w, h in items:
        path = files[index]
        crop = (crops or {}).get(path) or {"cx": 0.5, "cy": 0.5, "zoom": 1.0}
        image = _open_tile(path, 72 if preview else dpi, sources)
        try:
            tile = ct.cover_fit_image(
                image,
                ct.mm_to_px(w, dpi),
                ct.mm_to_px(h, dpi),
                crop.get("cx", 0.5),
                crop.get("cy", 0.5),
                crop.get("zoom", 1.0),
                resample,
            )
            sheet.paste(tile, (ct.mm_to_px(x, dpi), ct.mm_to_px(y, dpi)))
            tile.close()
        finally:
            image.close()
    return sheet


def write_pdf(files, dest, paper, items, dpi, bg, crops, sources=None):
    sheet = render_sheet(files, paper, items, dpi, bg, crops, preview=False, sources=sources)
    pdf = ct.fitz.open()
    buffer = io.BytesIO()
    try:
        sheet.save(buffer, "PNG", compress_level=3)
        page = pdf.new_page(width=paper[0] * 72 / 25.4, height=paper[1] * 72 / 25.4)
        page.insert_image(page.rect, stream=buffer.getvalue())
        pdf.save(dest, garbage=3, deflate=True)
    finally:
        buffer.close()
        sheet.close()
        pdf.close()


def write_jpeg(files, dest, paper, items, dpi, bg, crops, quality=90, sources=None):
    sheet = render_sheet(files, paper, items, dpi, bg, crops, preview=False, sources=sources)
    try:
        sheet.save(dest, "JPEG", quality=int(quality), dpi=(dpi, dpi))
    finally:
        sheet.close()
