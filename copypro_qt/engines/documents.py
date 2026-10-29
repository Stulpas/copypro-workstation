"""Scan detection and PDF tools, calling the existing OpenCV and PyMuPDF routines."""

from __future__ import annotations

import os

import numpy as np

from copypro_qt.legacy import ct


def _order_points(points):
    pts = np.asarray(points, dtype=np.float32)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).ravel()
    return np.array([
        pts[np.argmin(sums)],
        pts[np.argmin(diffs)],
        pts[np.argmax(sums)],
        pts[np.argmax(diffs)],
    ], dtype=np.float32)


def _scan_host():
    host = ct.ScanCroppingTab.__new__(ct.ScanCroppingTab)
    host._order_points = _order_points
    return host


def detect_photos(image, sensitivity, min_area):
    return ct.ScanCroppingTab._detect_image(_scan_host(), image, sensitivity, min_area)


def crop_photo(image, points, straighten):
    return ct.ScanCroppingTab._crop_photo(_scan_host(), image, points, straighten)


def native_dpi(page):
    return ct.ScanCroppingTab._native_dpi(page)


def output_paths(job, mode, chosen_dir, subfolder, keep_names, replace, suffix):
    return ct.PdfToolsTab._output_path(None, job, mode, chosen_dir, subfolder, keep_names, replace, suffix)


def process_pdf(src, dest, dpi, quality, flatten, compress, rasterise, bw):
    if bw:
        ct.PdfToolsTab._write_gray_pdf(None, src, dest, dpi, quality, compress)
    else:
        ct.PdfToolsTab._write_pdf_ops(None, src, dest, dpi, quality, flatten, compress, rasterise)


def process_gray_image(src, dest, quality):
    ct.PdfToolsTab._save_gray_image(None, src, dest, quality)


def split_pdf(src, dest, page_from, page_to, protect_covers=False):
    """Bisect pages in [page_from, page_to]. When protect_covers, PDF page 1 and N stay whole."""
    doc = ct.fitz.open(src)
    out = ct.fitz.open()
    try:
        n = doc.page_count
        start = 1 if page_from is None else max(1, int(page_from))
        end = n if page_to is None else int(page_to)
        for i in range(n):
            page_no = i + 1
            keep_whole = (
                page_no < start
                or page_no > end
                or (protect_covers and (page_no == 1 or page_no == n))
            )
            if keep_whole:
                out.insert_pdf(doc, from_page=i, to_page=i)
                continue
            page = doc[i]
            r = page.rect
            if r.width >= r.height:
                clips = [
                    ct.fitz.Rect(r.x0, r.y0, r.x0 + r.width / 2, r.y1),
                    ct.fitz.Rect(r.x0 + r.width / 2, r.y0, r.x1, r.y1),
                ]
            else:
                clips = [
                    ct.fitz.Rect(r.x0, r.y0, r.x1, r.y0 + r.height / 2),
                    ct.fitz.Rect(r.x0, r.y0 + r.height / 2, r.x1, r.y1),
                ]
            for clip in clips:
                new_page = out.new_page(width=clip.width, height=clip.height)
                new_page.show_pdf_page(new_page.rect, doc, i, clip=clip)
        out.save(dest, garbage=4, deflate=True)
    finally:
        out.close()
        doc.close()


def analyse_pdfs(paths, size_tol, colour_tolerance, progress=None):
    results = []
    total = 0
    for path in paths:
        try:
            with ct.fitz.open(path) as doc:
                total += doc.page_count
        except Exception:
            pass
    done = 0
    for path in paths:
        doc = None
        try:
            doc = ct.fitz.open(path)
            pages, counts = [], {}
            for index, page in enumerate(doc):
                width, height = ct.PageCounterTab._size_mm(page)
                key = ct.PageCounterTab._size_key(width, height, size_tol)
                counts[key] = counts.get(key, 0) + 1
                pages.append({
                    "index": index,
                    "page": index + 1,
                    "colour": ct.PageCounterTab._has_colour(page, colour_tolerance),
                    "blank": ct.PageCounterTab._is_blank(page),
                    "w": width,
                    "h": height,
                    "key": key,
                })
                done += 1
                if progress:
                    progress(done, total, os.path.basename(path))
            dominant = max(counts, key=counts.get) if counts else None
            for item in pages:
                item["odd"] = item["key"] != dominant
            results.append({"path": path, "pages": pages, "dominant": dominant})
        except Exception as exc:
            results.append({"path": path, "pages": [], "dominant": None, "error": str(exc)})
        finally:
            if doc:
                doc.close()
    return results


def extract_pages(results, dest_dir, types, chosen_sizes, remove_blank):
    made, errors = [], []
    for result in results:
        path = result["path"]
        base = os.path.splitext(os.path.basename(path))[0]
        try:
            src = ct.fitz.open(path)
            indices = []
            for page in result.get("pages", []):
                type_ok = ("colour" in types and page["colour"]) or ("bw" in types and not page["colour"])
                if type_ok and page["key"] in chosen_sizes and not (remove_blank and page.get("blank")):
                    indices.append(page["index"])
            if indices:
                dest = ct.fitz.open()
                for index in indices:
                    dest.insert_pdf(src, from_page=index, to_page=index)
                suffix = []
                if set(types) == {"colour"}:
                    suffix.append("colour")
                elif set(types) == {"bw"}:
                    suffix.append("bw")
                else:
                    suffix.append("selected")
                if len(chosen_sizes) == 1:
                    key = next(iter(chosen_sizes))
                    suffix.append(f"{key[0]:g}x{key[1]:g}mm")
                if remove_blank:
                    suffix.append("no_blanks")
                out_path = ct._unique(os.path.join(dest_dir, f"{base}_{'_'.join(suffix)}.pdf"))
                dest.save(out_path, garbage=4, deflate=True)
                dest.close()
                made.append(out_path)
            src.close()
        except Exception as exc:
            errors.append(f"{os.path.basename(path)}: {exc}")
    return made, errors
