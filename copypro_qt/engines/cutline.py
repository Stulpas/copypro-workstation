"""Vector sticker outlines. Smoothness stays at 0, as in the current tool."""

from __future__ import annotations

import base64
import io
import os

import cv2
import numpy as np
from PIL import Image, ImageOps

from copypro_qt.legacy import ct

SMOOTHNESS = 0


def load_analysis(path, max_side=2600):
    ext = os.path.splitext(path)[1].lower()
    page_points = None
    if ext == ".pdf":
        doc = ct.fitz.open(path)
        try:
            if doc.page_count < 1:
                raise ValueError("PDF has no pages.")
            page = doc[0]
            page_points = (page.rect.width, page.rect.height)
            render_dpi = 180.0
            pix = page.get_pixmap(matrix=ct.fitz.Matrix(render_dpi / 72, render_dpi / 72), alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            doc.close()
        dpi = render_dpi
        info = f"PDF page 1 • {page_points[0]:.0f} × {page_points[1]:.0f} pt"
    else:
        raw = ImageOps.exif_transpose(Image.open(path))
        detected = raw.info.get("dpi", (300, 300))
        try:
            dpi = float(detected[0] if isinstance(detected, (tuple, list)) else detected)
            if dpi < 20:
                dpi = 300.0
        except Exception:
            dpi = 300.0
        if raw.mode in ("RGBA", "LA") or "transparency" in raw.info:
            rgba = raw.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, "white")
            bg.alpha_composite(rgba)
            image = bg.convert("RGB")
        else:
            image = raw.convert("RGB")
        if raw is not image:
            raw.close()
        info = f"{image.width} × {image.height} px • {dpi:.0f} DPI"
    if max(image.size) > max_side:
        scale = max_side / max(image.size)
        resized = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        image.close()
        image = resized
    return image, dpi, page_points, info


def analyse(image, tolerance, min_area, offset_mm, include_holes, dpi, source_pixel_width, detail):
    array = np.asarray(image.convert("RGB"), dtype=np.uint8)
    white_floor = 255 - max(0, min(254, int(tolerance)))
    mask = np.any(array < white_floor, axis=2).astype(np.uint8) * 255
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    dpi = max(20.0, float(dpi))
    offset_px = int(round(float(offset_mm) / 25.4 * dpi * (image.width / max(1, source_pixel_width))))
    if offset_px:
        radius = min(250, abs(offset_px))
        element = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        mask = cv2.dilate(mask, element) if offset_px > 0 else cv2.erode(mask, element)
    retrieval = cv2.RETR_TREE if include_holes else cv2.RETR_EXTERNAL
    contours, _hierarchy = cv2.findContours(mask, retrieval, cv2.CHAIN_APPROX_NONE)
    min_pixels = image.width * image.height * max(0.0, float(min_area)) / 100.0
    detail = max(0, min(100, int(detail)))
    result = []
    for contour in contours:
        if abs(cv2.contourArea(contour)) < min_pixels:
            continue
        points = contour[:, 0, :].astype(np.float32)
        smoothed = ct.StickerCutlineTab._corner_preserving_smooth(points, SMOOTHNESS)
        shaped = smoothed.reshape((-1, 1, 2)).astype(np.float32)
        epsilon = cv2.arcLength(shaped, True) * (detail / 100.0) * 0.006
        approx = cv2.approxPolyDP(shaped, epsilon, True) if epsilon > 0 else shaped
        path = [(float(p[0][0]), float(p[0][1])) for p in approx]
        if len(path) >= 3:
            result.append(path)
    return result


def source_size_mm(path, dpi, page_points, analysis_size):
    if page_points:
        return page_points[0] * 25.4 / 72.0, page_points[1] * 25.4 / 72.0
    dpi = max(20.0, float(dpi))
    try:
        with Image.open(path) as image:
            width, height = ImageOps.exif_transpose(image).size
    except Exception:
        width, height = analysis_size
    return width * 25.4 / dpi, height * 25.4 / dpi


def source_pixel_width(path, dpi, page_points, analysis_width):
    if page_points:
        return page_points[0] / 72.0 * max(20.0, float(dpi))
    try:
        with Image.open(path) as image:
            return image.width
    except Exception:
        return analysis_width


def export_pdf(path, dest, contours, analysis_size, width_mm, height_mm, line_mm, artwork):
    width_pt, height_pt = width_mm * 72 / 25.4, height_mm * 72 / 25.4
    doc = ct.fitz.open()
    page = doc.new_page(width=width_pt, height=height_pt)
    artwork_ocg = doc.add_ocg("Artwork") if artwork else 0
    cut_ocg = doc.add_ocg("Cut line")
    rect = ct.fitz.Rect(0, 0, width_pt, height_pt)
    source_doc = None
    if artwork:
        if os.path.splitext(path)[1].lower() == ".pdf":
            source_doc = ct.fitz.open(path)
            page.show_pdf_page(rect, source_doc, 0, keep_proportion=False, oc=artwork_ocg)
        else:
            try:
                page.insert_image(rect, filename=path, keep_proportion=False, oc=artwork_ocg)
            except Exception:
                with Image.open(path) as raw:
                    raw = ImageOps.exif_transpose(raw)
                    buffer = io.BytesIO()
                    raw.save(buffer, format="PNG", compress_level=0)
                page.insert_image(rect, stream=buffer.getvalue(), keep_proportion=False, oc=artwork_ocg)
    aw, ah = analysis_size
    sx, sy = width_pt / aw, height_pt / ah
    width = max(0.01, float(line_mm)) * 72 / 25.4
    for contour in contours:
        page.draw_polyline(
            [ct.fitz.Point(x * sx, y * sy) for x, y in contour],
            color=(1, 0, 0),
            width=width,
            closePath=True,
            lineJoin=1,
            lineCap=1,
            oc=cut_ocg,
        )
    doc.save(dest, garbage=4)
    if source_doc is not None:
        source_doc.close()
    doc.close()


def export_svg(path, dest, contours, analysis_size, width_mm, height_mm, line_mm, artwork, dpi):
    aw, ah = analysis_size
    line_px = max(0.01, float(line_mm)) / max(width_mm / aw, height_mm / ah)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width_mm:.6f}mm" height="{height_mm:.6f}mm" viewBox="0 0 {aw} {ah}">',
    ]
    if artwork:
        mime, encoded = _artwork_uri(path, dpi)
        parts.extend([
            '<g id="Artwork" inkscape:label="Artwork" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">',
            f'<image x="0" y="0" width="{aw}" height="{ah}" preserveAspectRatio="none" '
            f'xlink:href="data:{mime};base64,{encoded}"/>',
            "</g>",
        ])
    parts.append('<g id="Cut_line" inkscape:label="Cut line" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">')
    for contour in contours:
        commands = [f"M {contour[0][0]:.3f} {contour[0][1]:.3f}"]
        commands.extend(f"L {x:.3f} {y:.3f}" for x, y in contour[1:])
        commands.append("Z")
        parts.append(
            f'<path d="{" ".join(commands)}" fill="none" stroke="#FF0000" '
            f'stroke-width="{line_px:.4f}" stroke-linejoin="round" stroke-linecap="round"/>'
        )
    parts.extend(["</g>", "</svg>"])
    with open(dest, "w", encoding="utf-8") as handle:
        handle.write("\n".join(parts))


def _artwork_uri(path, dpi):
    ext = os.path.splitext(path)[1].lower()
    direct = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
    if ext in direct:
        with open(path, "rb") as handle:
            return direct[ext], base64.b64encode(handle.read()).decode("ascii")
    if ext == ".pdf":
        doc = ct.fitz.open(path)
        page = doc[0]
        pix = page.get_pixmap(matrix=ct.fitz.Matrix(max(72.0, float(dpi)) / 72, max(72.0, float(dpi)) / 72), alpha=True)
        image = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
        doc.close()
    else:
        image = ImageOps.exif_transpose(Image.open(path))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=0)
    return "image/png", base64.b64encode(buffer.getvalue()).decode("ascii")
