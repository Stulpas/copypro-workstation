"""Scanned-PDF photo detection."""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from copypro_qt.engines import documents as doc_engine
from copypro_qt.legacy import ct
from copypro_qt.widgets import (
    button, choose_files, choose_save, group_card, harden_value_widget, ingest_paths, labeled,
    muted, section,
)
from copypro_qt.workers import run_bg


class ScanView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 320)
        self.page = None
        self.selected = -1

    def set_page(self, page):
        self.page = page
        self.selected = 0 if page and page.get("crops") else -1
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#12121A"))
        if not self.page:
            painter.setPen(QColor("#7878A0"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Drop scanned PDFs")
            painter.end()
            return
        image = self.page["qimage"]
        ox, oy, dw, dh = self._fit()
        painter.drawImage(QRect_safe(ox, oy, dw, dh), image)
        painter.setPen(QPen(QColor("#5B6BFF"), 2))
        for index, crop in enumerate(self.page.get("crops") or []):
            points = [QPoint(int(ox + x * dw), int(oy + y * dh)) for x, y in crop]
            if index == self.selected:
                painter.setPen(QPen(QColor("#F59E0B"), 2))
            else:
                painter.setPen(QPen(QColor("#5B6BFF"), 2))
            for start, end in zip(points, points[1:] + points[:1]):
                painter.drawLine(start, end)
        painter.end()

    def _fit(self):
        image = self.page["qimage"]
        scale = min((self.width() - 24) / max(1, image.width()), (self.height() - 24) / max(1, image.height()))
        dw, dh = max(1, int(image.width() * scale)), max(1, int(image.height() * scale))
        return (self.width() - dw) // 2, (self.height() - dh) // 2, dw, dh

    def mousePressEvent(self, event):
        if not self.page or not self.page.get("crops"):
            return
        ox, oy, dw, dh = self._fit()
        pos = event.position()
        best = None
        for crop_index, crop in enumerate(self.page["crops"]):
            for point_index, (x, y) in enumerate(crop):
                distance = abs(ox + x * dw - pos.x()) + abs(oy + y * dh - pos.y())
                if distance <= 18 and (best is None or distance < best[0]):
                    best = (distance, crop_index, point_index)
        if best:
            self.selected = best[1]
            self._drag = (best[1], best[2])
            self.update()

    def mouseMoveEvent(self, event):
        if not getattr(self, "_drag", None) or not self.page:
            return
        crop_index, point_index = self._drag
        ox, oy, dw, dh = self._fit()
        x = min(1.0, max(0.0, (event.position().x() - ox) / max(1, dw)))
        y = min(1.0, max(0.0, (event.position().y() - oy) / max(1, dh)))
        crop = [tuple(point) for point in self.page["crops"][crop_index]]
        crop[point_index] = (x, y)
        self.page["crops"][crop_index] = crop
        self.update()

    def mouseReleaseEvent(self, _event):
        self._drag = None


def QRect_safe(x, y, w, h):
    from PySide6.QtCore import QRect
    return QRect(x, y, w, h)


class ScansWorkspace:
    def __init__(self, shell):
        self.shell = shell
        self.files = []
        self.pages = []
        self._signals = []
        self.source_visible = False
        self.inspector = QWidget()
        self.canvas = QWidget()
        self.source_panel = QWidget()
        self._build()

    def _build(self):
        root = QVBoxLayout(self.inspector)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        form = QVBoxLayout(body)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(8)
        self.sensitivity = QSlider(Qt.Horizontal)
        self.sensitivity.setRange(5, 70)
        self.sensitivity.setValue(22)
        self.sense_label = muted("Detection sensitivity: 22")
        self.sensitivity.valueChanged.connect(lambda v: self.sense_label.setText(f"Detection sensitivity: {v}"))
        self.min_area = harden_value_widget(QDoubleSpinBox())
        self.min_area.setRange(0.1, 20)
        self.min_area.setValue(0.8)
        self.straighten = QCheckBox("Straighten detected photos")
        self.straighten.setChecked(True)
        form.addWidget(group_card(
            "Detection",
            self.sense_label,
            self.sensitivity,
            labeled("Minimum area %", self.min_area),
            self.straighten,
        ))
        form.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        sticky = QWidget()
        actions = QVBoxLayout(sticky)
        actions.setContentsMargins(8, 8, 8, 8)
        actions.setSpacing(8)
        self.detect_btn = button("Detect current page", "success")
        self.detect_btn.clicked.connect(self._detect)
        actions.addWidget(self.detect_btn)
        self.export_btn = button("Export cropped photos", "success")
        self.export_btn.clicked.connect(self._export)
        actions.addWidget(self.export_btn)
        row = QHBoxLayout()
        add = button("Add", "secondary")
        clear = button("Clear", "secondary")
        add.clicked.connect(self._add)
        clear.clicked.connect(self.clear)
        row.addWidget(add)
        row.addWidget(clear)
        actions.addLayout(row)
        root.addWidget(sticky)

        layout = QVBoxLayout(self.canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = ScanView()
        layout.addWidget(self.view, 1)
        self.status = muted("Add one or more scanned PDF files.")
        layout.addWidget(self.status)

        side = QVBoxLayout(self.source_panel)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        side.addWidget(section("Pages"))
        self.page_list = QListWidget()
        self.page_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.page_list.currentRowChanged.connect(self._select)
        self.page_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.page_list.customContextMenuRequested.connect(self._page_menu)
        side.addWidget(self.page_list, 1)

    def on_show(self):
        return

    def on_tray(self):
        return

    def add_paths(self, paths):
        pdfs = [path for path in ingest_paths(paths, self.files) if path.lower().endswith(".pdf")]
        if not pdfs:
            return
        self.files.extend(pdfs)
        self._load_new(pdfs)

    def clear(self):
        self.files.clear()
        self.pages.clear()
        self._rebuild_page_list()
        self.view.set_page(None)
        self.status.setText("Add one or more scanned PDF files.")
        self.source_visible = False
        self.shell.set_source_visible(False)

    def _add(self):
        paths = choose_files(self.canvas, "Add scanned PDFs", "PDF (*.pdf)")
        if paths:
            self.add_paths(paths)

    def _page_menu(self, pos):
        row = self.page_list.itemAt(pos)
        if row is None:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self.page_list)
        action = menu.addAction("Remove page")
        chosen = menu.exec(self.page_list.mapToGlobal(pos))
        if chosen != action:
            return
        index = self.page_list.row(row)
        self._remove_page(index)

    def _remove_page(self, index):
        if index < 0 or index >= len(self.pages):
            return
        page = self.pages.pop(index)
        if not any(entry["source"] == page["source"] for entry in self.pages):
            self.files = [path for path in self.files if path != page["source"]]
        self._rebuild_page_list(select=min(index, max(0, len(self.pages) - 1)))
        if not self.pages:
            self.view.set_page(None)
            self.status.setText("Add one or more scanned PDF files.")

    def _rebuild_page_list(self, select=0):
        self.page_list.blockSignals(True)
        self.page_list.clear()
        for index, page in enumerate(self.pages):
            row = QListWidgetItem()
            row.setSizeHint(__import__("PySide6.QtCore", fromlist=["QSize"]).QSize(120, 32))
            self.page_list.addItem(row)
            cell = QWidget()
            box = QHBoxLayout(cell)
            box.setContentsMargins(4, 0, 4, 0)
            box.addWidget(muted(page["label"]), 1)
            remove = button("✕", "ghost")
            remove.clicked.connect(lambda _=False, i=index: self._remove_page(i))
            box.addWidget(remove)
            self.page_list.setItemWidget(row, cell)
        self.page_list.blockSignals(False)
        if self.pages:
            self.page_list.setCurrentRow(max(0, min(select, len(self.pages) - 1)))
        self.source_visible = bool(self.pages)
        self.shell.set_source_visible(self.source_visible)

    def _load_new(self, pdfs):
        known = {(page["source"], page["page_no"]) for page in self.pages}
        added = False
        for path in pdfs:
            if any(page["source"] == path for page in self.pages):
                continue
            try:
                doc = ct.fitz.open(path)
            except Exception:
                continue
            try:
                from PIL import Image
                for index, page in enumerate(doc):
                    if (path, index) in known:
                        continue
                    scale = 900 / max(page.rect.width, page.rect.height, 1)
                    pix = page.get_pixmap(matrix=ct.fitz.Matrix(scale, scale), alpha=False)
                    preview = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    qimage = QImage()
                    buffer = __import__("io").BytesIO()
                    preview.save(buffer, "PNG")
                    qimage.loadFromData(buffer.getvalue())
                    self.pages.append({
                        "source": path, "page_no": index, "qimage": qimage, "preview": preview,
                        "crops": [], "label": f"{os.path.basename(path)} — page {index + 1}",
                    })
                    added = True
            finally:
                doc.close()
        if added or not self.page_list.count():
            self._rebuild_page_list()

    def _select(self, row):
        self.view.set_page(self.pages[row] if 0 <= row < len(self.pages) else None)
        if self.view.page:
            count = len(self.view.page["crops"])
            self.status.setText(f"{count} crop{'s' if count != 1 else ''}")

    def _detect(self):
        if not self.pages:
            return
        previews = [page["preview"].copy() for page in self.pages]
        sensitivity = self.sensitivity.value()
        min_area = self.min_area.value()
        self.status.setText("Detecting…")

        def work():
            found = []
            for image in previews:
                try:
                    found.append(doc_engine.detect_photos(image, sensitivity, min_area))
                finally:
                    image.close()
            return found

        def done(found):
            for page, crops in zip(self.pages, found):
                page["crops"] = crops
            self.page_list.clear()
            for page in self.pages:
                self.page_list.addItem(f"{page['label']} ({len(page['crops'])})")
            if self.pages:
                self.page_list.setCurrentRow(max(0, self.page_list.currentRow()))
            total = sum(len(page["crops"]) for page in self.pages)
            self.status.setText(f"Detected {total} photo(s).")
            self.shell.report(self.status.text())

        self._signals.append(run_bg(work, done, lambda err: QMessageBox.critical(self.canvas, "Detection failed", err), self.canvas))

    def _export(self):
        total = sum(len(page["crops"]) for page in self.pages)
        if not total:
            QMessageBox.warning(self.canvas, "Nothing to export", "Detect at least one photo first.")
            return
        dest = choose_save(self.canvas, "Export cropped photos", "scanned_photos.pdf", "PDF (*.pdf)")
        if not dest:
            return
        snapshot = [(page["source"], page["page_no"], [list(crop) for crop in page["crops"]]) for page in self.pages]
        straighten = self.straighten.isChecked()
        self.status.setText("Exporting full-resolution photos…")

        def work():
            import gc
            import io
            from PIL import Image
            pdf = ct.fitz.open()
            count = 0
            try:
                for source, page_no, crops in snapshot:
                    doc = ct.fitz.open(source)
                    try:
                        page = doc[page_no]
                        dpi = doc_engine.native_dpi(page)
                        pix = page.get_pixmap(matrix=ct.fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
                        full = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    finally:
                        doc.close()
                    try:
                        for points in crops:
                            photo = doc_engine.crop_photo(full, points, straighten)
                            buffer = io.BytesIO()
                            try:
                                photo.save(buffer, "PNG", compress_level=3)
                                output = pdf.new_page(width=photo.width * 72 / dpi, height=photo.height * 72 / dpi)
                                output.insert_image(output.rect, stream=buffer.getvalue())
                                count += 1
                            finally:
                                buffer.close()
                                photo.close()
                    finally:
                        full.close()
                    gc.collect(0)
                pdf.save(dest, garbage=3, deflate=True)
            finally:
                pdf.close()
            return count

        def done(count):
            self.status.setText(f"Exported {count} photo(s).")
            QMessageBox.information(self.canvas, "Done", f"Saved {count} photo(s) to\n{dest}")

        self._signals.append(run_bg(work, done, lambda err: QMessageBox.critical(self.canvas, "Export failed", err), self.canvas))
