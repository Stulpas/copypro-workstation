"""Photos workspace: bulk resize and file conversion, each photo on its own crop card."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from copypro_qt import imaging
from copypro_qt.legacy import ct
from copypro_qt.paper import paper_names, resolve_dir, target_px
from copypro_qt.widgets import (
    CropView, OutputOptions, button, choose_files, group_card, harden_value_widget, ingest_paths,
    labeled, mode_combo, muted, pair, section,
)
from copypro_qt.workers import run_bg

_ZOOM_MIN = 0.75
_ZOOM_MAX = 3.0
_ZOOM_STEP = 1.12


class _Surface(QWidget):
    def __init__(self, on_drop):
        super().__init__()
        self._on_drop = on_drop
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._on_drop(paths)


class _Grid(QScrollArea):
    def __init__(self, on_resize, parent=None):
        super().__init__(parent)
        self._on_resize = on_resize
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.grid.setSpacing(12)
        self.setWidget(self.inner)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._on_resize()


class _Card(QFrame):
    def __init__(self, item, on_crop, on_rotate, on_zoom, on_size, on_remove, size_names):
        super().__init__()
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.item = item
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QHBoxLayout()
        name = QLabel(item["label"])
        name.setWordWrap(True)
        name.setObjectName("muted")
        title.addWidget(name, 1)
        remove = button("✕", "ghost")
        remove.setToolTip("Remove this file")
        remove.clicked.connect(lambda: on_remove(item))
        title.addWidget(remove)
        layout.addLayout(title)
        self.view = CropView()
        self.view.cropChanged.connect(lambda fractions: on_crop(item, fractions))
        self.view.previewZoom.connect(on_zoom)
        layout.addWidget(self.view)
        self.size = QComboBox()
        self.size.addItem("Global")
        self.size.addItems(size_names)
        override = item.get("size_name")
        if override:
            index = self.size.findText(override)
            if index >= 0:
                self.size.setCurrentIndex(index)
        def apply_size(text):
            on_size(item, None if text == "Global" else text)

        self.size.currentTextChanged.connect(apply_size)
        self.size.activated.connect(lambda _index: apply_size(self.size.currentText()))
        layout.addWidget(self.size)
        row = QHBoxLayout()
        left = button("↺", "ghost")
        right = button("↻", "ghost")
        left.clicked.connect(lambda: on_rotate(item, 90))
        right.clicked.connect(lambda: on_rotate(item, -90))
        row.addWidget(left)
        row.addWidget(right)
        layout.addLayout(row)

    def set_side(self, side):
        self.view.setFixedSize(side, side)
        # Card chrome: 8px margins each side; keep width fixed so grid cells do not stretch.
        self.setFixedWidth(side + 16)

    def show_image(self, image, aspect):
        self.view.set_preview(image, aspect, self.item.get("fractions"))


class PhotosWorkspace:
    def __init__(self, shell):
        self.shell = shell
        self.files = []
        self.items = []
        self._cards = {}
        self._signals = []
        self._generation = 0
        self.preview_scale = 1.0
        self.source_visible = False
        self.inspector = QWidget()
        self.canvas = _Surface(self.add_paths)
        self.source_panel = QWidget()
        self._build_inspector()
        self._build_canvas()
        self._build_source()
        self._toggle_mode()
        self._shrink_fields()
        for sequence, direction in (("Ctrl++", 1), ("Ctrl+=", 1), ("Ctrl+-", -1)):
            QShortcut(QKeySequence(sequence), self.canvas, lambda d=direction: self._guard_zoom(d))

    def _shrink_fields(self):
        field = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        for combo in self.inspector.findChildren(QComboBox):
            harden_value_widget(combo)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(0)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(field)
        for spin in self.inspector.findChildren(QAbstractSpinBox):
            harden_value_widget(spin)
            spin.setMinimumWidth(0)
            spin.setSizePolicy(field)
        for edit in self.inspector.findChildren(QLineEdit):
            edit.setMinimumWidth(0)
            edit.setSizePolicy(field)

    def _build_inspector(self):
        root = QVBoxLayout(self.inspector)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.mode = mode_combo()
        self.mode.addItems(["Resize", "Convert"])
        self.mode.currentIndexChanged.connect(self._toggle_mode)
        layout.addWidget(self.mode)

        self.resize_box = QWidget()
        form = QVBoxLayout(self.resize_box)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self.paper = QComboBox()
        self.paper.addItems(paper_names())
        if "A4" in paper_names():
            self.paper.setCurrentText("A4")
        self.paper.currentIndexChanged.connect(self._paper_changed)
        self.paper.activated.connect(lambda _index: self._paper_changed())
        self.orientation = QComboBox()
        self.orientation.addItems(["Portrait", "Landscape"])
        self.orientation.currentIndexChanged.connect(self._paper_changed)
        self.orientation.activated.connect(lambda _index: self._paper_changed())
        self.dpi = QComboBox()
        self.dpi.addItems([str(v) for v in ct.DPI_OPTIONS])
        self.dpi.setCurrentText("300")
        self.resize_fmt = QComboBox()
        self.resize_fmt.addItems(["JPEG", "PNG", "TIFF", "PDF"])
        self.quality_label = muted("JPEG quality: 90")
        self.quality = QSlider(Qt.Horizontal)
        self.quality.setRange(10, 100)
        self.quality.setValue(90)
        self.quality.valueChanged.connect(lambda v: self.quality_label.setText(f"JPEG quality: {v}"))
        form.addWidget(group_card(
            "Size",
            pair(labeled("Paper", self.paper), labeled("Orientation", self.orientation)),
            pair(labeled("DPI", self.dpi), labeled("Format", self.resize_fmt)),
            self.quality_label,
            self.quality,
        ))
        self.bleed = QCheckBox("Add bleed")
        self.marks = QCheckBox("Add crop marks")
        self.bleed_mm = QLineEdit("3")
        self.bleed_mode = QComboBox()
        self.bleed_mode.addItems(["Reflected", "Solid colour"])
        self.bleed_color = (255, 255, 255)
        self.color_btn = button("Bleed colour", "secondary")
        self.color_btn.clicked.connect(self._pick_color)
        self.mark_style = QComboBox()
        self.mark_style.addItems(["Outside bleed", "In bleed (at trim)"])
        self.bleed.stateChanged.connect(self._sync_mark_style)
        self.marks.stateChanged.connect(self._sync_mark_style)
        self._sync_mark_style()
        self.grayscale = QCheckBox("Grayscale")
        self.stretch = QCheckBox("Stretch to fit")
        form.addWidget(group_card(
            "Bleed",
            self.bleed,
            labeled("Bleed mm", self.bleed_mm),
            self.bleed_mode,
            self.color_btn,
            spacing=8,
        ))
        form.addWidget(group_card(
            "Crop marks",
            self.marks,
            labeled("Crop marks", self.mark_style),
            spacing=8,
        ))
        form.addWidget(group_card(
            "Image",
            self.grayscale,
            self.stretch,
            spacing=8,
        ))
        layout.addWidget(self.resize_box)

        self.convert_box = QWidget()
        conv = QVBoxLayout(self.convert_box)
        conv.setContentsMargins(0, 0, 0, 0)
        conv.setSpacing(8)
        self.conv_fmt = QComboBox()
        self.conv_fmt.addItems(ct.CONV_FORMATS)
        self.webp = QCheckBox("Lossless WebP")
        self.raster_dpi = QComboBox()
        self.raster_dpi.addItems([str(v) for v in ct.DPI_OPTIONS])
        self.raster_dpi.setCurrentText("150")
        self.bg = QLineEdit("#FFFFFF")
        self.pages_btn = button("Open Pages for iCloud", "secondary")
        self.pages_btn.clicked.connect(lambda: __import__("webbrowser").open("https://www.icloud.com/pages"))
        conv.addWidget(group_card(
            "Convert",
            labeled("Format", self.conv_fmt),
            self.webp,
            labeled("Raster DPI", self.raster_dpi),
            labeled("Background", self.bg),
            self.pages_btn,
            spacing=8,
        ))
        layout.addWidget(self.convert_box)

        self.output = OutputOptions("resized")
        layout.addWidget(group_card("Output", self.output))
        layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        sticky = QWidget()
        actions = QVBoxLayout(sticky)
        actions.setContentsMargins(8, 8, 8, 8)
        actions.setSpacing(8)
        self.export_btn = button("Export all", "success")
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
        self.status = muted("")
        actions.addWidget(self.status)
        root.addWidget(sticky)

    def _build_canvas(self):
        layout = QVBoxLayout(self.canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.drop_hint = muted("Drop images or PDFs here, or use Add")
        header.addWidget(self.drop_hint, 1)
        minus = button("−", "ghost")
        plus = button("+", "ghost")
        minus.clicked.connect(lambda: self._nudge_zoom(-1))
        plus.clicked.connect(lambda: self._nudge_zoom(1))
        self.zoom_label = muted("100%")
        header.addWidget(minus)
        header.addWidget(self.zoom_label)
        header.addWidget(plus)
        layout.addLayout(header)
        self.grid = _Grid(self._apply_scale)
        layout.addWidget(self.grid, 1)
        self.convert_hint = muted("Files appear in the source column on the right.")
        self.convert_hint.setAlignment(Qt.AlignCenter)
        self.convert_hint.hide()
        layout.addWidget(self.convert_hint, 1)
        self.zoom_buttons = (minus, plus)

    def _build_source(self):
        side = QVBoxLayout(self.source_panel)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        side.addWidget(section("Source files"))
        self.file_list = QListWidget()
        self.file_list.setViewMode(QListWidget.ListMode)
        self.file_list.setSpacing(4)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._convert_menu)
        side.addWidget(self.file_list, 1)

    def on_show(self):
        names = paper_names()
        current = self.paper.currentText()
        self.paper.blockSignals(True)
        self.paper.clear()
        self.paper.addItems(names)
        if current in names:
            self.paper.setCurrentText(current)
        self.paper.blockSignals(False)

    def on_tray(self):
        return

    def add_paths(self, paths):
        self.files.extend(ingest_paths(paths, self.files))
        self._sync()

    def clear(self):
        self.files.clear()
        self.items.clear()
        self._cards.clear()
        self._clear_grid()
        self.file_list.clear()
        self.status.setText("")
        self.source_visible = False
        self.shell.set_source_visible(False)

    def _add(self):
        paths = choose_files(
            self.canvas, "Add photos",
            "Images and documents (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.heic *.heif *.pdf *.svg *.pages);;All files (*.*)",
        )
        if paths:
            self.add_paths(paths)

    def _toggle_mode(self):
        resize = self.mode.currentIndex() == 0
        self.resize_box.setVisible(resize)
        self.convert_box.setVisible(not resize)
        if hasattr(self, "grid"):
            self.grid.setVisible(resize)
            self.convert_hint.setVisible(not resize)
            self.zoom_label.setVisible(resize)
            for widget in self.zoom_buttons:
                widget.setVisible(resize)
            self.output.path.setText("resized" if resize else "converted")
            self._sync()

    def _paper_changed(self):
        # Rematch orientation + crop aspect for the new global size.
        self._generation += 1
        generation = self._generation
        for item in self.items:
            self._queue_thumb(item, generation, match_orient=True)

    def _match_orientation(self, item, image_w, image_h):
        """Auto-rotate so the photo matches target portrait/landscape."""
        tw, th = self._item_size(item)
        target_portrait = th >= tw
        img_portrait = image_h >= image_w
        desired = 90 if target_portrait != img_portrait else 0
        if int(item.get("rotation") or 0) != desired:
            item["rotation"] = desired
            item["fractions"] = None

    def _aspect(self, size_name=None):
        tw, th, _w, _h = self._target_for(size_name)
        return tw / max(1, th)

    def _target(self):
        return self._target_for(None)

    def _target_for(self, size_name):
        paper = size_name or self.paper.currentText()
        return target_px(paper, int(self.dpi.currentText()), self.orientation.currentText())

    def _item_aspect(self, item):
        return self._aspect(item.get("size_name"))

    def _item_size(self, item):
        return self._target_for(item.get("size_name"))[:2]

    def _guard_zoom(self, direction):
        if self.shell._current == "photos" and self.mode.currentIndex() == 0:
            self._nudge_zoom(direction)

    def _nudge_zoom(self, direction):
        step = _ZOOM_STEP if direction > 0 else 1 / _ZOOM_STEP
        self.preview_scale = max(_ZOOM_MIN, min(_ZOOM_MAX, self.preview_scale * step))
        self.zoom_label.setText(f"{int(round(self.preview_scale * 100))}%")
        self._apply_scale()

    def _columns(self):
        width = max(self.grid.viewport().width(), 300)
        return 4 if width >= 760 else 3

    def _side(self):
        width = max(self.grid.viewport().width(), 300)
        cols = self._columns()
        # Cap base tile so wide windows do not stretch previews edge-to-edge.
        base = max(140, min(240, (width // cols) - 32))
        return max(80, int(round(base * self.preview_scale)))

    def _apply_scale(self):
        if not hasattr(self, "grid") or self.mode.currentIndex() != 0:
            return
        side = self._side()
        cols = self._columns()
        for col in range(max(cols + 1, self.grid.grid.columnCount())):
            self.grid.grid.setColumnStretch(col, 0)
        for index, card in enumerate(self._cards.values()):
            card.set_side(side)
            self.grid.grid.addWidget(card, index // cols, index % cols, Qt.AlignTop | Qt.AlignLeft)
        # Absorb leftover horizontal space so cells stay card-sized.
        self.grid.grid.setColumnStretch(cols, 1)

    def _apply_aspect(self):
        for card in self._cards.values():
            if not card.view._image.isNull():
                card.show_image(card.view._image, self._item_aspect(card.item))

    def _clear_grid(self):
        while self.grid.grid.count():
            item = self.grid.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _sync(self):
        if not hasattr(self, "grid"):
            return
        previous = {item["key"]: item for item in self.items}
        items = []
        for path in self.files:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".pdf" and self.mode.currentIndex() == 0:
                try:
                    doc = ct.fitz.open(path)
                    count = doc.page_count
                    doc.close()
                except Exception:
                    count = 0
                for page in range(count):
                    key = f"{path}#{page}"
                    items.append(previous.get(key) or {
                        "key": key, "path": path, "page": page,
                        "label": f"{os.path.basename(path)} — page {page + 1}",
                        "rotation": 0, "fractions": None, "size_name": None,
                    })
            elif ext in ct.IMAGE_EXTS or self.mode.currentIndex() == 1:
                items.append(previous.get(path) or {
                    "key": path, "path": path, "page": None,
                    "label": os.path.basename(path),
                    "rotation": 0, "fractions": None, "size_name": None,
                })
        self.items = items
        self._generation += 1
        generation = self._generation
        if self.mode.currentIndex() == 0:
            self._rebuild_cards(generation, match_orient=True)
            self._rebuild_list(generation)
        else:
            self._clear_grid()
            self._rebuild_list(generation)
        count = len(self.items)
        self.status.setText(f"{count} file{'s' if count != 1 else ''}" if count else "")
        self.source_visible = count > 0
        self.shell.set_source_visible(self.source_visible)

    def _rebuild_cards(self, generation, match_orient=True):
        self._clear_grid()
        self._cards = {}
        side = self._side()
        names = paper_names()
        for item in self.items:
            card = _Card(
                item, self._store_crop, self._rotate_item, self._nudge_zoom,
                self._set_item_size, self._remove_item, names,
            )
            card.set_side(side)
            self._cards[item["key"]] = card
            self._queue_thumb(item, generation, match_orient=match_orient)
        self._apply_scale()

    def _rebuild_list(self, generation):
        self.file_list.clear()
        self.file_list.setViewMode(QListWidget.ListMode)
        self.file_list.setSpacing(4)
        for item in self.items:
            row = QListWidgetItem()
            row.setData(Qt.UserRole, item["key"])
            row.setSizeHint(__import__("PySide6.QtCore", fromlist=["QSize"]).QSize(200, 48))
            self.file_list.addItem(row)
            cell = QWidget()
            row_layout = QHBoxLayout(cell)
            row_layout.setContentsMargins(4, 2, 4, 2)
            icon = QLabel()
            icon.setFixedSize(40, 40)
            icon.setObjectName(f"icon-{item['key']}")
            name = QLabel(item["label"])
            name.setWordWrap(True)
            remove = button("✕", "ghost")
            remove.clicked.connect(lambda _=False, target=item: self._remove_item(target))
            row_layout.addWidget(icon)
            row_layout.addWidget(name, 1)
            row_layout.addWidget(remove)
            self.file_list.setItemWidget(row, cell)
            self._queue_icon(item, generation)

    def _queue_thumb(self, item, generation, aspect=None, match_orient=True):
        aspect = aspect if aspect is not None else self._item_aspect(item)

        def work():
            return item["key"], imaging.thumbnail_png(item["path"], 480, item["page"])

        def done(result):
            if generation != self._generation:
                return
            key, data = result
            card = self._cards.get(key)
            if card is None:
                return
            image = QImage()
            image.loadFromData(data)
            # Rematch only when the target size changed — never wipe crops on list rebuild/remove.
            if match_orient:
                self._match_orientation(item, image.width(), image.height())
            if item["rotation"]:
                from PySide6.QtGui import QTransform
                image = image.transformed(QTransform().rotate(item["rotation"]))
            card.show_image(image, aspect if aspect is not None else self._item_aspect(item))

        self._signals.append(run_bg(work, done, parent=self.canvas))

    def _queue_icon(self, item, generation):
        def work():
            return item["key"], imaging.thumbnail_png(item["path"], 160, item["page"])

        def done(result):
            if generation != self._generation:
                return
            key, data = result
            from PySide6.QtGui import QPixmap
            image = QImage()
            image.loadFromData(data)
            for row in range(self.file_list.count()):
                widget = self.file_list.item(row)
                if widget.data(Qt.UserRole) != key:
                    continue
                cell = self.file_list.itemWidget(widget)
                if cell is None:
                    break
                icon = cell.findChild(QLabel)
                if icon is not None:
                    icon.setPixmap(QPixmap.fromImage(image).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                break

        self._signals.append(run_bg(work, done, parent=self.canvas))

    def _store_crop(self, item, fractions):
        item["fractions"] = fractions

    def _set_item_size(self, item, size_name):
        item["size_name"] = size_name
        # Reload so orientation matches the new per-item target size.
        self._queue_thumb(item, self._generation, match_orient=True)

    def _remove_item(self, item):
        key = item["key"]
        path = item["path"]
        self.items = [entry for entry in self.items if entry["key"] != key]
        if not any(entry["path"] == path for entry in self.items):
            self.files = [entry for entry in self.files if entry != path]
        # Rebuild UI but keep stored crops/rotations on remaining items.
        self._generation += 1
        generation = self._generation
        if self.mode.currentIndex() == 0:
            self._rebuild_cards(generation, match_orient=False)
            self._rebuild_list(generation)
        else:
            self._clear_grid()
            self._rebuild_list(generation)
        count = len(self.items)
        self.status.setText(f"{count} file{'s' if count != 1 else ''}" if count else "")
        self.source_visible = count > 0
        self.shell.set_source_visible(self.source_visible)

    def _convert_menu(self, pos):
        row = self.file_list.itemAt(pos)
        if row is None:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self.file_list)
        action = menu.addAction("Remove")
        chosen = menu.exec(self.file_list.mapToGlobal(pos))
        if chosen != action:
            return
        key = row.data(Qt.UserRole)
        item = next((entry for entry in self.items if entry["key"] == key), None)
        if item is not None:
            self._remove_item(item)

    def _rotate_item(self, item, delta):
        # Manual nudge without rematching target orientation.
        item["rotation"] = (int(item["rotation"]) + int(delta)) % 360
        item["fractions"] = None
        generation = self._generation

        def work():
            return item["key"], imaging.thumbnail_png(item["path"], 480, item["page"])

        def done(result):
            if generation != self._generation:
                return
            key, data = result
            target = self._cards.get(key)
            if target is None:
                return
            image = QImage()
            image.loadFromData(data)
            if item["rotation"]:
                from PySide6.QtGui import QTransform
                image = image.transformed(QTransform().rotate(item["rotation"]))
            target.show_image(image, self._item_aspect(item))

        self._signals.append(run_bg(work, done, parent=self.canvas))

    def _pick_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self.bleed_color = (color.red(), color.green(), color.blue())

    def _sync_mark_style(self):
        enabled = self.marks.isChecked() and self.bleed.isChecked()
        self.mark_style.setEnabled(enabled)
        if not self.bleed.isChecked() and self.mark_style.currentIndex() == 1:
            self.mark_style.setCurrentIndex(0)

    def _export(self):
        if not self.items and not self.files:
            QMessageBox.warning(self.canvas, "Nothing to export", "Add some files first.")
            return
        if self.mode.currentIndex() == 0:
            self._export_resize()
        else:
            self._export_convert()

    def _dest_for(self, source, settings):
        if settings["overwrite"]:
            answer = QMessageBox.question(
                self.canvas, "Replace originals?",
                "This will overwrite the original files. This cannot be undone.",
            )
            if answer != QMessageBox.Yes:
                return None
        return settings

    def _export_resize(self):
        settings = self.output.settings()
        if settings["overwrite"] and self._dest_for(None, settings) is None:
            return
        if settings["mode"] == "choose" and not settings["folder"]:
            from copypro_qt.widgets import choose_folder
            settings["folder"] = choose_folder(self.canvas, "Output folder")
            if not settings["folder"]:
                return
        snapshot = [dict(item) for item in self.items]
        options = self._resize_options()
        self.export_btn.setEnabled(False)
        self.shell.report("Exporting photos…", 0, len(snapshot))

        def work():
            return _resize_batch(snapshot, settings, options)

        def done(result):
            self.export_btn.setEnabled(True)
            done_n, errors, message = result
            self.shell.report(message, done_n, max(1, done_n + len(errors)))
            self.status.setText(message)
            if errors:
                QMessageBox.warning(self.canvas, "Export finished", message + "\n\n" + "\n".join(errors[:5]))
            elif done_n:
                QMessageBox.information(self.canvas, "Done", message)

        def failed(err):
            self.export_btn.setEnabled(True)
            QMessageBox.critical(self.canvas, "Export failed", err)

        self._signals.append(run_bg(work, done, failed, self.canvas))

    def _resize_options(self):
        try:
            bleed_mm = float(self.bleed_mm.text().replace(",", "."))
        except ValueError:
            bleed_mm = 3.0
        return {
            "dpi": int(self.dpi.currentText()),
            "size": self._target()[:2],
            "orientation": self.orientation.currentText(),
            "fmt": self.resize_fmt.currentText(),
            "quality": self.quality.value(),
            "grayscale": self.grayscale.isChecked(),
            "stretch": self.stretch.isChecked(),
            "bleed": self.bleed.isChecked(),
            "bleed_mm": bleed_mm,
            "marks": self.marks.isChecked(),
            "mark_style": "in_bleed" if self.mark_style.currentIndex() == 1 else "outside",
            "bleed_mode": "solid" if self.bleed_mode.currentIndex() else "reflected",
            "bleed_color": self.bleed_color,
        }

    def _export_convert(self):
        settings = self.output.settings()
        if settings["overwrite"] and self._dest_for(None, settings) is None:
            return
        if settings["mode"] == "choose" and not settings["folder"]:
            from copypro_qt.widgets import choose_folder
            settings["folder"] = choose_folder(self.canvas, "Output folder")
            if not settings["folder"]:
                return
        paths = list(self.files)
        options = {
            "fmt": self.conv_fmt.currentText(),
            "quality": self.quality.value(),
            "lossless": self.webp.isChecked(),
            "dpi": int(self.raster_dpi.currentText()),
            "bg": self.bg.text().strip() or "#FFFFFF",
        }
        self.export_btn.setEnabled(False)

        def work():
            return _convert_batch(paths, settings, options)

        def done(result):
            self.export_btn.setEnabled(True)
            self.shell.report(result[1])
            self.status.setText(result[1])
            QMessageBox.information(self.canvas, "Done", result[1])

        self._signals.append(run_bg(work, done, lambda err: QMessageBox.critical(self.canvas, "Convert failed", err), self.canvas))


def _bg_rgb(text):
    try:
        value = text.lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (255, 255, 255)


def _item_export_size(item, options):
    size_name = item.get("size_name")
    if not size_name:
        return options["size"]
    return target_px(size_name, options["dpi"], options.get("orientation", "Portrait"))[:2]


def _resize_batch(items, settings, options):
    fmt = options["fmt"]
    ext = "jpg" if fmt == "JPEG" else fmt.lower()
    done, errors = 0, []
    pages = []
    last_dir = ""
    for index, item in enumerate(items):
        try:
            pdf_source = (item["path"], item["page"]) if item["page"] is not None else None
            size = _item_export_size(item, options)
            image = imaging.resize_image(
                item["path"], size, item["rotation"], item["fractions"],
                options["stretch"], options["grayscale"], pdf_source, options["dpi"],
            )
            image = ct.apply_resizer_finishing(
                image, options["dpi"], options["bleed"], options["bleed_mm"], options["marks"],
                options["bleed_mode"], options["bleed_color"], options.get("mark_style", "outside"),
            )
            if fmt == "PDF":
                pages.append(image.convert("RGB"))
            else:
                folder = resolve_dir(settings["mode"], item["path"], settings["folder"], settings["subfolder"])
                last_dir = folder
                base = os.path.splitext(os.path.basename(item["path"]))[0]
                if item["page"] is not None:
                    base = f"{base}_page{item['page'] + 1}"
                suffix = "_bleed" if options["bleed"] else "_resized"
                dest = ct._unique(os.path.join(folder, f"{base}{suffix}.{ext}"))
                kwargs = {"dpi": (options["dpi"], options["dpi"])}
                if fmt == "JPEG":
                    kwargs["quality"] = options["quality"]
                image.save(dest, fmt, **kwargs)
                image.close()
            done += 1
        except Exception as exc:
            errors.append(f"{item['label']}: {exc}")
    if fmt == "PDF" and pages:
        folder = resolve_dir(settings["mode"], items[0]["path"], settings["folder"], settings["subfolder"])
        dest = ct._unique(os.path.join(folder, "resized_images.pdf"))
        pages[0].save(dest, "PDF", save_all=True, append_images=pages[1:], resolution=options["dpi"])
        for page in pages:
            page.close()
        last_dir = dest
    message = f"Exported {done} file{'s' if done != 1 else ''}"
    if last_dir:
        message += f" → {last_dir}"
    if errors:
        message += f"  ({len(errors)} failed)"
    return done, errors, message


def _convert_batch(paths, settings, options):
    fmt = options["fmt"]
    ext = "jpg" if fmt == "JPEG" else fmt.lower()
    bg = _bg_rgb(options["bg"])
    done, errors = 0, []
    pages = []
    last = ""
    for path in paths:
        try:
            source_ext = os.path.splitext(path)[1].lower()
            if fmt == "PDF":
                if source_ext == ".pdf":
                    pages.extend(img.convert("RGB") for img in ct.open_pdf_pages(path, dpi=options["dpi"]))
                elif source_ext == ".pages":
                    extracted, warning = ct.extract_pages_preview(path)
                    pages.extend(img.convert("RGB") for img in extracted)
                    errors.append(f"{os.path.basename(path)}: {warning}")
                else:
                    image = imaging.open_for_convert(path)
                    pages.append(ct._prepare_img(image, "PDF", bg).convert("RGB"))
                done += 1
                continue
            if source_ext in (".pdf", ".pages"):
                if source_ext == ".pdf":
                    images = ct.open_pdf_pages(path, dpi=options["dpi"])
                else:
                    images, warning = ct.extract_pages_preview(path)
                    errors.append(f"{os.path.basename(path)}: {warning}")
                folder = resolve_dir(settings["mode"], path, settings["folder"], settings["subfolder"])
                for index, image in enumerate(images):
                    base = os.path.splitext(os.path.basename(path))[0]
                    name = base if len(images) == 1 else f"{base}_p{index + 1:03d}"
                    dest = ct._unique(os.path.join(folder, f"{name}.{ext}"))
                    ct._save(ct._prepare_img(image, fmt, bg), dest, fmt, options["quality"], options["lossless"])
                    last = folder
                done += 1
            else:
                image = imaging.open_for_convert(path)
                folder = resolve_dir(settings["mode"], path, settings["folder"], settings["subfolder"])
                base = os.path.splitext(os.path.basename(path))[0]
                dest = path if settings["overwrite"] else ct._unique(os.path.join(folder, f"{base}.{ext}"))
                ct._save(ct._prepare_img(image, fmt, bg), dest, fmt, options["quality"], options["lossless"])
                last = folder
                done += 1
        except Exception as exc:
            errors.append(f"{os.path.basename(path)}: {exc}")
    if fmt == "PDF" and pages:
        folder = resolve_dir(settings["mode"], paths[0], settings["folder"], settings["subfolder"])
        dest = ct._unique(os.path.join(folder, "converted_files.pdf"))
        pages[0].save(dest, "PDF", save_all=True, append_images=pages[1:], resolution=options["dpi"])
        last = dest
    message = f"Converted {done} file{'s' if done != 1 else ''} → {last}"
    if errors:
        message += "\n" + "\n".join(errors[:5])
    return done, message
