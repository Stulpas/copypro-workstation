"""Fast thumbnails and photo resize.

libvips does the pixel work. Pillow still applies bleed, crop marks, and the
final save so JPEG/PNG settings stay the same as the current exporter.
"""

from __future__ import annotations

import io
import os
import sys

from PIL import Image, ImageOps

from copypro_qt.legacy import ct


def _windows_vips_path():
    if os.name != "nt":
        return
    folders = []
    if getattr(sys, "frozen", False):
        folders.append(os.path.dirname(sys.executable))
    folders.append(os.path.dirname(os.path.abspath(__file__)))
    path = os.environ.get("PATH", "")
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(folder)
            except OSError:
                pass
        if folder not in path.split(os.pathsep):
            path = folder + os.pathsep + path
    os.environ["PATH"] = path


_windows_vips_path()

try:
    import pyvips

    HAVE_VIPS = True
    VIPS_ERROR = ""
except Exception as exc:  # pragma: no cover - depends on the machine
    pyvips = None
    HAVE_VIPS = False
    VIPS_ERROR = str(exc)


def fit_box(src_w, src_h, dst_w, dst_h):
    """Center-crop fractions matching ImageOps.fit(..., centering=(0.5, 0.5))."""
    src_w = max(1, int(src_w))
    src_h = max(1, int(src_h))
    dst_w = max(1, int(dst_w))
    dst_h = max(1, int(dst_h))
    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h
    if src_ratio > dst_ratio:
        crop_w = src_h * dst_ratio
        left = (src_w - crop_w) / 2.0
        return (left / src_w, 0.0, (left + crop_w) / src_w, 1.0)
    crop_h = src_w / dst_ratio
    top = (src_h - crop_h) / 2.0
    return (0.0, top / src_h, 1.0, (top + crop_h) / src_h)


def refit_fractions(fractions, src_w, src_h, aspect):
    """Rebuild crop size for a new aspect while keeping the previous center."""
    left, top, right, bottom = fractions
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0
    src_w = max(1.0, float(src_w))
    src_h = max(1.0, float(src_h))
    aspect = max(0.05, float(aspect))
    max_fw = max(0.05, 2.0 * min(cx, 1.0 - cx))
    max_fh = max(0.05, 2.0 * min(cy, 1.0 - cy))
    # Fraction height so pixel box matches target aspect: fh = fw * (sw/sh) / aspect
    fw = min(max_fw, max_fh * src_h * aspect / src_w, 1.0)
    fw = max(0.05, fw)
    fh = fw * src_w / (src_h * aspect)
    if fh > max_fh + 1e-9:
        fh = max(0.05, min(1.0, max_fh))
        fw = max(0.05, min(1.0, fh * src_h * aspect / src_w))
    left = cx - fw / 2.0
    top = cy - fh / 2.0
    right = cx + fw / 2.0
    bottom = cy + fh / 2.0
    if left < 0:
        right -= left
        left = 0.0
    if top < 0:
        bottom -= top
        top = 0.0
    if right > 1:
        left -= right - 1
        right = 1.0
    if bottom > 1:
        top -= bottom - 1
        bottom = 1.0
    return (
        max(0.0, min(1.0, left)),
        max(0.0, min(1.0, top)),
        max(0.0, min(1.0, right)),
        max(0.0, min(1.0, bottom)),
    )


def _crop_box(width, height, fractions):
    left, top, right, bottom = fractions
    x0 = max(0, min(width - 1, int(left * width)))
    y0 = max(0, min(height - 1, int(top * height)))
    x1 = max(x0 + 1, min(width, int(right * width)))
    y1 = max(y0 + 1, min(height, int(bottom * height)))
    return x0, y0, x1, y1


def resize_pillow(image, size, fractions=None, stretch=False):
    """Reference resize. fractions=None is exactly ImageOps.fit."""
    tw, th = max(1, int(size[0])), max(1, int(size[1]))
    if fractions is None and not stretch:
        return ImageOps.fit(image, (tw, th), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    iw, ih = image.size
    box = (0, 0, iw, ih) if fractions is None else _crop_box(iw, ih, fractions)
    cropped = image.crop(box)
    try:
        if stretch:
            return cropped.resize((tw, th), Image.Resampling.LANCZOS)
        return ImageOps.fit(cropped, (tw, th), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    finally:
        if cropped is not image:
            cropped.close()


def _rotate_pil(image, degrees):
    degrees = int(degrees or 0) % 360
    if not degrees:
        return image
    rotated = image.rotate(degrees, expand=True)
    if rotated is not image:
        image.close()
    return rotated


def _vips_autorot(image):
    fields = image.get_fields()
    if "orientation" in fields:
        return image.autorot()
    return image


def _vips_rotate(image, degrees):
    degrees = int(degrees or 0) % 360
    if degrees == 90:
        return image.rot90()
    if degrees == 180:
        return image.rot180()
    if degrees == 270:
        return image.rot270()
    return image


def _vips_rgb(image):
    if image.hasalpha():
        image = image.flatten(background=[255, 255, 255])
    if image.interpretation != "srgb":
        image = image.colourspace("srgb")
    if image.bands > 3:
        image = image[:3]
    return image


def _vips_to_pil(image):
    image = _vips_rgb(image)
    memory = image.write_to_memory()
    return Image.frombytes("RGB", (image.width, image.height), memory, "raw", "RGB")


def _resize_vips(path, size, rotation, fractions, stretch, grayscale):
    image = pyvips.Image.new_from_file(path, access="sequential")
    image = _vips_autorot(image)
    image = _vips_rotate(image, rotation)
    image = _vips_rgb(image)
    tw, th = max(1, int(size[0])), max(1, int(size[1]))
    if fractions is not None:
        x0, y0, x1, y1 = _crop_box(image.width, image.height, fractions)
        image = image.crop(x0, y0, x1 - x0, y1 - y0)
    if stretch:
        image = image.thumbnail_image(tw, height=th, size="force")
    else:
        image = image.thumbnail_image(tw, height=th, size="both", crop="centre")
    if grayscale:
        image = image.colourspace("b-w").colourspace("srgb")
    if image.width != tw or image.height != th:
        image = image.thumbnail_image(tw, height=th, size="force")
    return _vips_to_pil(image)


def load_export_image(path, pdf_source, dpi, rotation):
    if pdf_source:
        pdf_path, page_no = pdf_source
        image = ct.open_pdf_page(pdf_path, page_no, dpi=dpi).convert("RGB")
    else:
        image = ct.load_raster_source(path, {}, dpi=dpi).convert("RGB")
    return _rotate_pil(image, rotation)


def resize_image(path, size, rotation=0, fractions=None, stretch=False, grayscale=False, pdf_source=None, dpi=300):
    """Return an RGB PIL image at exactly size, ready for bleed and saving."""
    tw, th = max(1, int(size[0])), max(1, int(size[1]))
    if pdf_source is None and HAVE_VIPS:
        try:
            image = _resize_vips(path, (tw, th), rotation, fractions, stretch, grayscale)
            if image.size == (tw, th):
                return image
            image.close()
        except Exception:
            pass
    image = load_export_image(path, pdf_source, dpi, rotation)
    try:
        fitted = resize_pillow(image, (tw, th), fractions, stretch)
    finally:
        if image is not None:
            image.close()
    if grayscale:
        gray = ImageOps.grayscale(fitted).convert("RGB")
        fitted.close()
        fitted = gray
    return fitted


def thumbnail_png(path, max_dim=360, pdf_page=None):
    """Small PNG bytes for the grid. Does not decode a full print-resolution image."""
    max_dim = max(32, int(max_dim))
    if pdf_page is not None:
        doc = ct.fitz.open(path)
        try:
            page = doc[int(pdf_page)]
            long_side = max(page.rect.width, page.rect.height, 1)
            scale = max_dim / long_side
            pix = page.get_pixmap(matrix=ct.fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            doc.close()
        buf = io.BytesIO()
        image.save(buf, "PNG")
        image.close()
        return buf.getvalue()
    if HAVE_VIPS:
        try:
            image = pyvips.Image.thumbnail(path, max_dim, height=max_dim, size="down")
            return image.write_to_buffer(".png")
        except Exception:
            pass
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        image.thumbnail((max_dim, max_dim), Image.Resampling.BILINEAR)
        rgb = image.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, "PNG")
        if rgb is not image:
            rgb.close()
        if image is not opened:
            image.close()
        return buf.getvalue()


def open_for_convert(path):
    """Decode a photo once. HEIC and large JPEG files go through libvips."""
    if HAVE_VIPS:
        try:
            image = pyvips.Image.new_from_file(path, access="sequential")
            image = _vips_autorot(image)
            return _vips_to_pil(image)
        except Exception:
            pass
    return ct.load_raster_source(path, {}, dpi=300)


def finish_and_save(image, dest, fmt, dpi, quality, bleed, bleed_mm, marks, bleed_mode, bleed_color, mark_style="outside"):
    finished = ct.apply_resizer_finishing(
        image, dpi, bleed, bleed_mm, marks, bleed_mode, bleed_color, mark_style,
    )
    try:
        kwargs = {}
        if fmt == "JPEG":
            kwargs["quality"] = int(quality)
            kwargs["dpi"] = (dpi, dpi)
        elif fmt in ("PNG", "TIFF"):
            kwargs["dpi"] = (dpi, dpi)
        finished.save(dest, fmt, **kwargs)
    finally:
        if finished is not image:
            finished.close()
