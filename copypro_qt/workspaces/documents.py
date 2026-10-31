"""PDF process, page counter, and vector spread split."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from copypro_qt.engines import documents as docs
from copypro_qt.legacy import ct
from copypro_qt.widgets import (
    OutputOptions, button, choose_files, choose_folder, group_card, harden_value_widget,
    ingest_paths, labeled, mode_combo, muted, section,
)
from copypro_qt.workers import run_bg


class DocumentsWorkspace:
    def __init__(self, shell):
        self.shell = shell
        self.files = []
        self.results = []
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
        self.mode = mode_combo()
        self.mode.addItems(["Process", "Page counter"])
        self.mode.currentIndexChanged.connect(self._switch)
        form.addWidget(self.mode)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._process_form())
        self.stack.addWidget(self._counter_form())
        form.addWidget(self.stack)
        self.output = OutputOptions("optimised")
        self.keep = QCheckBox("Keep original names")
        form.addWidget(group_card("Output", self.output, self.keep))
        form.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        sticky = QWidget()
        actions = QVBoxLayout(sticky)
        actions.setContentsMargins(8, 8, 8, 8)
        actions.setSpacing(8)
        self.run_btn = button("Run", "success")
        self.run_btn.clicked.connect(self._run)
        actions.addWidget(self.run_btn)
        row = QHBoxLayout()
        add = button("Add files", "secondary")
        clear = button("Clear", "secondary")
        add.clicked.connect(self._add)
        clear.clicked.connect(self.clear)
        row.addWidget(add)
        row.addWidget(clear)
        actions.addLayout(row)
        root.addWidget(sticky)

        layout = QVBoxLayout(self.canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = muted("Add PDFs or images.")
        layout.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Page", "Type", "Size", "Size class"])
        self.tree.setRootIsDecorated(False)
        layout.addWidget(self.tree, 1)
        stats = QHBoxLayout()
        self.cards = {}
        for key, title in (("total", "Total"), ("colour", "Colour"), ("bw", "B&W"), ("odd", "Odd size"), ("blank", "Blank")):
            label = muted(f"{title}\n0")
            label.setAlignment(Qt.AlignCenter)
            self.cards[key] = label
            stats.addWidget(label)
        layout.addLayout(stats)

        side = QVBoxLayout(self.source_panel)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        side.addWidget(section("Source files"))
        self.file_list = QListWidget()
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._file_menu)
        side.addWidget(self.file_list, 1)

    def _process_form(self):
        page = QWidget()
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        self.dpi = harden_value_widget(QComboBox())
        self.dpi.addItems([str(v) for v in ct.DPI_OPTIONS])
        self.dpi.setCurrentText("150")
        self.quality = harden_value_widget(QSpinBox())
        self.quality.setRange(10, 100)
        self.quality.setValue(80)
        self.flatten = QCheckBox("Flatten")
        self.flatten.setChecked(True)
        self.compress = QCheckBox("Compress")
        self.compress.setChecked(True)
        self.raster = QCheckBox("Rasterise")
        self.bw = QCheckBox("Black & white")
        form.addWidget(group_card(
            "Process",
            labeled("Rasterise DPI", self.dpi),
            labeled("JPEG quality", self.quality),
            self.flatten,
            self.compress,
            self.raster,
            self.bw,
            spacing=8,
        ))
        form.addStretch(1)
        return page

    def _counter_form(self):
        page = QWidget()
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        self.tol = QLineEdit("12")
        self.size_tol = QLineEdit("1.0")
        form.addWidget(group_card(
            "Page counter",
            labeled("Colour tolerance", self.tol),
            labeled("Size tolerance mm", self.size_tol),
        ))
        form.addStretch(1)
        return page

    def _switch(self):
        self.stack.setCurrentIndex(self.mode.currentIndex())
        labels = ("Process all", "Analyse pages")
        self.run_btn.setText(labels[self.mode.currentIndex()])
        self.output.setVisible(self.mode.currentIndex() != 1)

    def on_show(self):
        self._refresh_summary()

    def on_tray(self):
        return

    def add_paths(self, paths):
        self.files.extend(ingest_paths(paths, self.files))
        self._refresh_summary()

    def clear(self):
        self.files.clear()
        self.results.clear()
        self.tree.clear()
        self.file_list.clear()
        self._refresh_summary()

    def _add(self):
        paths = choose_files(self.canvas, "Add documents", "Documents (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.webp);;All files (*.*)")
        if paths:
            self.add_paths(paths)

    def _file_menu(self, pos):
        row = self.file_list.itemAt(pos)
        if row is None:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self.file_list)
        action = menu.addAction("Remove")
        chosen = menu.exec(self.file_list.mapToGlobal(pos))
        if chosen != action:
            return
        path = row.data(Qt.UserRole)
        self.files = [item for item in self.files if item != path]
        self._refresh_summary()

    def _refresh_summary(self):
        count = len(self._jobs())
        self.summary.setText(f"{count} file{'s' if count != 1 else ''}" if count else "Add PDFs or images.")
        self.file_list.clear()
        for path in self.files:
            row = QListWidgetItem()
            row.setData(Qt.UserRole, path)
            row.setSizeHint(__import__("PySide6.QtCore", fromlist=["QSize"]).QSize(120, 32))
            self.file_list.addItem(row)
            cell = QWidget()
            row_layout = QHBoxLayout(cell)
            row_layout.setContentsMargins(4, 0, 4, 0)
            name = muted(os.path.basename(path))
            remove = button("✕", "ghost")
            remove.clicked.connect(lambda _=False, target=path: self._remove_file(target))
            row_layout.addWidget(name, 1)
            row_layout.addWidget(remove)
            self.file_list.setItemWidget(row, cell)
        self.source_visible = bool(self.files)
        self.shell.set_source_visible(self.source_visible)

    def _remove_file(self, path):
        self.files = [item for item in self.files if item != path]
        self._refresh_summary()

    def _jobs(self):
        return ct.collect_media_jobs(self.files, ct.PDF_TOOLS_EXTS, recursive=False)

    def _run(self):
        if self.mode.currentIndex() == 1:
            self._analyse()
        else:
            self._process()

    def _settings(self):
        settings = self.output.settings()
        if settings["mode"] == "choose" and not settings["folder"]:
            settings["folder"] = choose_folder(self.canvas, "Output folder")
        return settings

    def _process(self):
        jobs = self._jobs()
        if not jobs:
            QMessageBox.warning(self.canvas, "Nothing", "Add PDF files or images first.")
            return
        settings = self._settings()
        if not settings or (settings["mode"] == "choose" and not settings["folder"]):
            return
        options = {
            "dpi": int(self.dpi.currentText()),
            "quality": self.quality.value(),
            "flatten": self.flatten.isChecked(),
            "compress": self.compress.isChecked(),
            "raster": self.raster.isChecked(),
            "bw": self.bw.isChecked(),
            "keep": self.keep.isChecked() or self.bw.isChecked(),
        }
        self.run_btn.setEnabled(False)

        def work():
            done = errors = skipped = 0
            saved = 0
            last = ""
            for job in jobs:
                src = job["src"]
                ext = os.path.splitext(src)[1].lower()
                try:
                    before = os.path.getsize(src)
                    write_path, final_path = docs.output_paths(
                        job, settings["mode"], settings["folder"], settings["subfolder"],
                        options["keep"], settings["overwrite"], "_optimised",
                    )
                    if ext == ".pdf":
                        docs.process_pdf(
                            src, write_path, options["dpi"], options["quality"],
                            options["flatten"], options["compress"], options["raster"], options["bw"],
                        )
                    elif ext in ct.IMAGE_EXTS and options["bw"]:
                        docs.process_gray_image(src, write_path, options["quality"])
                    else:
                        skipped += 1
                        continue
                    if settings["overwrite"] and os.path.abspath(write_path) != os.path.abspath(final_path):
                        os.replace(write_path, final_path)
                        write_path = final_path
                    saved += max(0, before - os.path.getsize(write_path))
                    last = os.path.dirname(write_path)
                    done += 1
                except Exception:
                    errors += 1
            return done, errors, skipped, saved / 1024 / 1024, last

        def finished(result):
            self.run_btn.setEnabled(True)
            done, errors, skipped, saved_mb, last = result
            text = f"Processed {done} file(s) → {last}\nSaved about {saved_mb:.1f} MB"
            if skipped:
                text += f"\nSkipped {skipped}"
            if errors:
                text += f"\n{errors} failed"
            self.shell.report(text.split("\n")[0])
            QMessageBox.information(self.canvas, "Done", text)

        self._signals.append(run_bg(work, finished, self._fail, self.canvas))

    def _analyse(self):
        pdfs = [job["src"] for job in self._jobs() if job["src"].lower().endswith(".pdf")]
        if not pdfs:
            QMessageBox.warning(self.canvas, "Nothing", "Add at least one PDF first.")
            return
        try:
            colour = int(float(self.tol.text().replace(",", ".")))
            size_tol = float(self.size_tol.text().replace(",", "."))
        except ValueError:
            colour, size_tol = 12, 1.0
        self.run_btn.setEnabled(False)

        def work():
            return docs.analyse_pdfs(pdfs, max(0.1, size_tol), colour)

        def finished(results):
            self.run_btn.setEnabled(True)
            self.results = results
            self.tree.clear()
            counts = {"total": 0, "colour": 0, "bw": 0, "odd": 0, "blank": 0}
            for result in results:
                name = os.path.basename(result["path"])
                for page in result["pages"]:
                    counts["total"] += 1
                    counts["colour"] += int(page["colour"])
                    counts["bw"] += int(not page["colour"])
                    counts["odd"] += int(page["odd"])
                    counts["blank"] += int(page.get("blank"))
                    kind = "Blank" if page.get("blank") else ("Colour" if page["colour"] else "B&W")
                    self.tree.addTopLevelItem(QTreeWidgetItem([
                        name, str(page["page"]), kind, f"{page['w']:.1f} × {page['h']:.1f} mm",
                        "Non-dominant" if page["odd"] else "Dominant",
                    ]))
            titles = {"total": "Total", "colour": "Colour", "bw": "B&W", "odd": "Odd size", "blank": "Blank"}
            for key, label in self.cards.items():
                label.setText(f"{titles[key]}\n{counts[key]}")
            self.summary.setText(f"Finished: {counts['colour']} colour, {counts['bw']} B&W, {counts['odd']} odd-size, {counts['blank']} blank")
            self._offer_extract()

        self._signals.append(run_bg(work, finished, self._fail, self.canvas))

    def _offer_extract(self):
        if not any(result.get("pages") for result in self.results):
            return
        answer = QMessageBox.question(self.canvas, "Export pages", "Export the analysed pages, skipping blanks?")
        if answer != QMessageBox.Yes:
            return
        folder = choose_folder(self.canvas, "Output folder")
        if not folder:
            return
        sizes = {page["key"] for result in self.results for page in result.get("pages", [])}

        def work():
            return docs.extract_pages(self.results, folder, ["colour", "bw"], sizes, True)

        def finished(result):
            made, errors = result
            QMessageBox.information(self.canvas, "Done", f"Created {len(made)} PDF(s)." + (f"\n{errors[0]}" if errors else ""))

        self._signals.append(run_bg(work, finished, self._fail, self.canvas))

    def _fail(self, err):
        self.run_btn.setEnabled(True)
        QMessageBox.critical(self.canvas, "Documents", err)
