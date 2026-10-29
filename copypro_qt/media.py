"""Turn the shared queue into image and PDF-page jobs."""

from __future__ import annotations

import os

from copypro_qt.legacy import ct


def expand(paths, images=True, pdfs=True, other=False):
    jobs = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf" and pdfs:
            try:
                doc = ct.fitz.open(path)
                count = doc.page_count
                doc.close()
            except Exception:
                continue
            for page in range(count):
                jobs.append({
                    "key": f"{path}#{page}",
                    "path": path,
                    "page": page,
                    "label": f"{os.path.basename(path)} — page {page + 1}",
                })
        elif ext in ct.IMAGE_EXTS and images:
            jobs.append({"key": path, "path": path, "page": None, "label": os.path.basename(path)})
        elif other:
            jobs.append({"key": path, "path": path, "page": None, "label": os.path.basename(path)})
    return jobs


def sources_of(jobs):
    """Map job key → (filesystem path, pdf page or None). Supports duplicate keys."""
    return {
        job["key"]: (job["path"], job["page"])
        for job in jobs
        if job.get("path")
    }
