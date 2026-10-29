"""Impose workspace: print layout, polaroid, collage, and sticker cut line."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QIcon, QImage, QKeySequence, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from copypro_qt.engines import booklet as booklet_engine
from copypro_qt.engines import collage as collage_engine
from copypro_qt.engines import cutline as cut_engine
from copypro_qt.engines import layout as layout_engine
from copypro_qt.engines import polaroid as polaroid_engine
from copypro_qt.legacy import ct
from copypro_qt.media import expand, sources_of
from copypro_qt.paper import paper_mm, paper_names, paper_size_raw
from copypro_qt.widgets import (
    CropView, button, choose_files, choose_save, group_card, ingest_paths, labeled, muted,
    pair, pixmap_from_pil, polish, section,
)
from copypro_qt.workers import run_bg


def open_in_acrobat(path):
    """Open a PDF in Acrobat. Never hand it to Preview.app."""
    if sys.platform == "darwin":
        for app in ("Adobe Acrobat", "Adobe Acrobat Reader", "Acrobat Reader"):
            result = subprocess.run(["open", "-a", app, path], capture_output=True)
            if result.returncode == 0:
                return True
        return False
    if sys.platform.startswith("win"):
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Adobe\Acrobat DC\Acrobat\Acrobat.exe"),
            os.path.expandvars(r"%ProgramFiles%\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe"),
            os.path.expandvars(r"%ProgramFiles%\Adobe\Acrobat\Acrobat.exe"),
        ]
        exe = next((item for item in candidates if item and os.path.exists(item)), None)
        if not exe:
            return False
        subprocess.Popen([exe, path])
        return True
    return False


class _SheetBoard(QScrollArea):
    """Imposed sheets (fit grid) or Flux-style book preview (large scrollable spreads)."""

    def __init__(self, on_zoom):
        super().__init__()
        self._on_zoom = on_zoom
        self.zoom = 1.0
        self._mode = "sheets"  # sheets | book
        self._sheets = []
        self._cards = []
        self._cols = 0
        self._placed = -1
        self._width = 0
        self._busy = False
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignCenter)
        self.setWidget(self.inner)
        self.empty = QLabel("Add source files to see the final imposed sheets")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.grid.addWidget(self.empty, 0, 0)

    def set_layout_mode(self, mode):
        mode = "book" if mode == "book" else "sheets"
        if mode == self._mode:
            return
        self._mode = mode
        self._cols = 0
        self._placed = -1
        self._apply()

    def set_sheets(self, sheets):
        self._sheets = list(sheets)
        self._ensure_cards()
        self._apply()

    def set_zoom(self, zoom):
        self.zoom = max(0.5, min(3.0, zoom))
        self._apply()

    def wheelEvent(self, event):
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier) and event.angleDelta().y():
            self._on_zoom(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._busy or not self._sheets:
            return
        cols, width = self._layout_metrics()
        if cols == self._cols and abs(width - self._width) < 4:
            return
        self._apply()

    def _layout_metrics(self):
        """sheets: fit grid into view. book: large single-column scrollable spreads."""
        count = len(self._sheets)
        if count == 0:
            return 1, 48
        image = self._sheets[0][1]
        aspect = image.height() / max(1, image.width())
        margin = 28
        vp_w = max(48, self.viewport().width() - margin)
        vp_h = max(48, self.viewport().height() - margin)
        gap = 10
        row_chrome = 44
        col_chrome = 16
        if self._mode == "book":
            # ~85% of viewport width, single column; always scroll vertically.
            base = max(280, min(vp_w * 0.85, 920))
            width = max(160, int(base * self.zoom))
            return 1, width
        best_cols, best_w, best_score = 1, 48, -1.0
        for cols in range(1, min(count, 8) + 1):
            rows = (count + cols - 1) // cols
            max_w = (vp_w - gap * (cols - 1) - cols * col_chrome) / cols
            picture_budget = vp_h - rows * row_chrome - gap * max(0, rows - 1)
            max_h = picture_budget / rows
            if max_w < 24 or max_h < 24:
                continue
            card_w = min(max_w, max_h / aspect)
            score = card_w * card_w
            if score > best_score:
                best_cols, best_w, best_score = cols, card_w, score
        if best_score < 0:
            cols = min(count, 8)
            rows = (count + cols - 1) // cols
            max_w = (vp_w - gap * (cols - 1) - cols * col_chrome) / cols
            picture_budget = vp_h - rows * row_chrome - gap * max(0, rows - 1)
            max_h = max(1.0, picture_budget / rows)
            best_cols, best_w = cols, min(max_w, max_h / aspect)
        width = max(16, int(best_w * self.zoom))
        return best_cols, width

    def _ensure_cards(self):
        while len(self._cards) > len(self._sheets):
            card = self._cards.pop()
            card["frame"].setParent(None)
            card["frame"].deleteLater()
        while len(self._cards) < len(self._sheets):
            frame = QFrame()
            frame.setObjectName("card")
            frame.setAttribute(Qt.WA_StyledBackground, True)
            shadow = QGraphicsDropShadowEffect(frame)
            shadow.setBlurRadius(18)
            shadow.setOffset(0, 4)
            shadow.setColor(QColor(0, 0, 0, 90))
            frame.setGraphicsEffect(shadow)
            box = QVBoxLayout(frame)
            box.setContentsMargins(4, 4, 4, 4)
            box.setSpacing(4)
            picture = QLabel()
            picture.setAlignment(Qt.AlignCenter)
            caption = QLabel()
            caption.setAlignment(Qt.AlignCenter)
            caption.setFixedHeight(18)
            box.addWidget(picture)
            box.addWidget(caption)
            self._cards.append({"frame": frame, "picture": picture, "caption": caption})
        self.empty.setVisible(not self._sheets)

    def _apply(self):
        if self._busy:
            return
        self._busy = True
        try:
            self._ensure_cards()
            cols, width = self._layout_metrics()
            self._width = width
            for card, (label, image) in zip(self._cards, self._sheets):
                card["caption"].setText(label)
                pixmap = QPixmap.fromImage(image).scaledToWidth(width, Qt.SmoothTransformation)
                card["picture"].setPixmap(pixmap)
                card["picture"].setFixedSize(pixmap.size())
            if cols != self._cols or len(self._sheets) != self._placed:
                self._cols = cols
                self._place(cols)
                self._placed = len(self._sheets)
            book = self._mode == "book"
            zoomed = self.zoom > 1.001
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded if zoomed else Qt.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(
                Qt.ScrollBarAsNeeded if book or zoomed else Qt.ScrollBarAlwaysOff
            )
            if book or zoomed:
                self.setWidgetResizable(False)
                for card in self._cards:
                    card["frame"].show()
                    card["frame"].setFixedSize(card["frame"].sizeHint())
                self.inner.layout().activate()
                hint = self.inner.layout().totalSizeHint()
                if book:
                    # Tall stack: grow with content; at least viewport height.
                    self.inner.resize(hint.expandedTo(QSize(self.viewport().width(), hint.height())))
                else:
                    self.inner.resize(hint.expandedTo(self.viewport().size()))
            else:
                for card in self._cards:
                    card["frame"].setMinimumSize(0, 0)
                    card["frame"].setMaximumSize(16777215, 16777215)
                self.setWidgetResizable(True)
        finally:
            self._busy = False

    def _place(self, cols):
        while self.grid.count():
            self.grid.takeAt(0)
        if not self._sheets:
            self.grid.addWidget(self.empty, 0, 0)
            self.empty.show()
            return
        self.empty.hide()
        for index, card in enumerate(self._cards):
            self.grid.addWidget(card["frame"], index // cols, index % cols)


class _CropColumn(QScrollArea):
    """Vertical source thumbs. Frame height follows each image's native orientation."""

    def __init__(self, horizontal=False):
        super().__init__()
        self.horizontal = horizontal
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        if horizontal:
            self.setFixedHeight(196)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.inner = QWidget()
        self.box = QHBoxLayout(self.inner) if horizontal else QVBoxLayout(self.inner)
        # Small gutter so cards sit close to the scrollbar without clipping.
        self.box.setContentsMargins(0, 0, 0 if horizontal else 4, 0)
        self.box.setSpacing(10)
        self.box.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setWidget(self.inner)
        self.setViewportMargins(0, 0, 0, 0)
        self.cards = {}
        self._frames = {}
        self._native = {}
        self._aspect = 1.0
        self._side = 120
        self._selected = None

    @staticmethod
    def _pill_btn(text, tip):
        btn = button(text, "ghost")
        btn.setToolTip(tip)
        btn.setMinimumSize(34, 30)
        btn.setStyleSheet("QPushButton { padding: 4px 8px; min-width: 34px; min-height: 30px; }")
        return btn

    def rebuild(
        self, jobs, aspect, on_crop, on_zoom=None, side=120, on_expand=None,
        on_rotate=None, on_remove=None, on_duplicate=None,
    ):
        self._aspect = aspect
        self._side = side
        while self.box.count():
            item = self.box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.cards = {}
        self._frames = {}
        self._native = {}
        card_w = side + 12  # CropView + layout margins
        for job in jobs:
            key = job["key"]
            frame = QFrame()
            frame.setObjectName("sourceCard")
            frame.setAttribute(Qt.WA_StyledBackground, True)
            frame.setProperty("selected", "false")
            frame.setFixedWidth(card_w)
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(6)
            view = CropView()
            view.setFixedSize(side, max(48, int(side / max(0.2, aspect))))
            view.cropChanged.connect(lambda fractions, k=key: on_crop(k, fractions))
            if on_zoom is not None:
                view.previewZoom.connect(on_zoom)
            if on_expand is not None:
                view.expandRequested.connect(lambda k=key: on_expand(k))
            name = QLabel(job["label"])
            name.setWordWrap(True)
            name.setObjectName("muted")
            name.setMaximumWidth(side)
            name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            layout.addWidget(view)
            layout.addWidget(name)
            if on_rotate is not None or on_remove is not None or on_duplicate is not None:
                pill = QFrame()
                pill.setObjectName("actionPill")
                pill.setAttribute(Qt.WA_StyledBackground, True)
                actions = QHBoxLayout(pill)
                actions.setContentsMargins(2, 2, 2, 2)
                actions.setSpacing(0)
                if on_rotate is not None:
                    left = self._pill_btn("↺", "Rotate left")
                    right = self._pill_btn("↻", "Rotate right")
                    left.clicked.connect(lambda _=False, k=key: on_rotate(k, 90))
                    right.clicked.connect(lambda _=False, k=key: on_rotate(k, -90))
                    actions.addWidget(left)
                    actions.addWidget(right)
                if on_duplicate is not None:
                    dup = self._pill_btn("❐", "Duplicate image")
                    dup.clicked.connect(lambda _=False, k=key: on_duplicate(k))
                    actions.addWidget(dup)
                actions.addStretch(1)
                if on_remove is not None:
                    remove = self._pill_btn("✕", "Remove this file")
                    remove.clicked.connect(lambda _=False, k=key: on_remove(k))
                    actions.addWidget(remove)
                layout.addWidget(pill)
            self.box.addWidget(frame)
            self.cards[key] = view
            self._frames[key] = frame
        self.box.addStretch(1)
        if self._selected in self._frames:
            self.select(self._selected)

    def set_card_side(self, side, aspect):
        self._side = side
        self._aspect = aspect
        card_w = side + 12
        for key, view in self.cards.items():
            native = self._native.get(key) or aspect
            height = max(40, int(side / max(0.2, native)))
            view.setFixedSize(side, height)
            frame = self._frames.get(key)
            if frame is not None:
                frame.setFixedWidth(card_w)
                for child in frame.findChildren(QLabel):
                    child.setMaximumWidth(side)

    def show_image(self, key, image, aspect, fractions):
        view = self.cards.get(key)
        if view is None:
            return
        native = image.width() / max(1, image.height()) if not image.isNull() else aspect
        self._native[key] = native
        height = max(40, int(self._side / max(0.2, native)))
        view.setFixedSize(self._side, height)
        view.set_preview(image, aspect, fractions)

    def select(self, key):
        self._selected = key
        for item_key, frame in self._frames.items():
            frame.setProperty("selected", "true" if item_key == key else "false")
            polish(frame)


class _FloatStage(QWidget):
    """Canvas host that keeps a floating action button in the bottom-right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._float = None

    def set_float_button(self, btn):
        self._float = btn
        btn.setParent(self)
        btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()

    def _place(self):
        btn = self._float
        if btn is None or not btn.isVisible():
            return
        margin = 12
        btn.adjustSize()
        btn.move(max(margin, self.width() - btn.width() - margin),
                 max(margin, self.height() - btn.height() - margin))
        btn.raise_()


class _PageList(QWidget):
    """Reorderable booklet page list (no crop)."""

    def __init__(self, on_change):
        super().__init__()
        self._on_change = on_change
        self._blank_seq = 0
        self._blank_color = (255, 255, 255)
        self._thumbs = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setIconSize(QSize(48, 36))
        self.list.setSpacing(2)
        self.list.model().rowsMoved.connect(lambda *_: self._emit())
        self.list.currentRowChanged.connect(self._sync_color_btn)
        root.addWidget(self.list, 1)

        def _tool(btn):
            btn.setMinimumHeight(32)
            btn.setMinimumWidth(36)
            return btn

        row = QHBoxLayout()
        row.setSpacing(6)
        up = _tool(button("↑", "ghost"))
        down = _tool(button("↓", "ghost"))
        blank = _tool(button("+", "secondary"))
        self.swatch = QPushButton()
        self.swatch.setFixedSize(32, 32)
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.setToolTip("Blank page color")
        remove = _tool(button("✕", "ghost"))
        up.setToolTip("Move up")
        down.setToolTip("Move down")
        blank.setToolTip("Insert a blank page")
        remove.setToolTip("Remove selected page")
        up.clicked.connect(lambda: self._nudge(-1))
        down.clicked.connect(lambda: self._nudge(1))
        blank.clicked.connect(self._add_blank)
        self.swatch.clicked.connect(self._pick_blank_color)
        remove.clicked.connect(self._remove_selected)
        row.addWidget(up)
        row.addWidget(down)
        row.addWidget(blank)
        row.addWidget(self.swatch)
        row.addStretch(1)
        row.addWidget(remove)
        root.addLayout(row)
        self._jobs = []
        self._sync_color_btn()

    def set_jobs(self, jobs, thumbs=None):
        if thumbs is not None:
            self._thumbs = dict(thumbs)
        selected = None
        row = self.list.currentRow()
        if row >= 0 and self.list.item(row):
            selected = self.list.item(row).data(Qt.UserRole)
        self._jobs = list(jobs)
        self.list.clear()
        restore = -1
        for index, job in enumerate(self._jobs):
            item = QListWidgetItem(job["label"])
            item.setData(Qt.UserRole, job["key"])
            image = self._thumbs.get(job["key"])
            if image is not None and not image.isNull():
                pix = QPixmap.fromImage(image).scaled(48, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                item.setIcon(QIcon(pix))
            elif job.get("blank"):
                rgb = tuple(job.get("color") or (255, 255, 255))
                swatch = QPixmap(48, 36)
                swatch.fill(QColor(*rgb))
                item.setIcon(QIcon(swatch))
                item.setText("Blank page")
            self.list.addItem(item)
            if selected is not None and job["key"] == selected:
                restore = index
        if restore >= 0:
            self.list.setCurrentRow(restore)
        self._sync_color_btn()

    def ordered_keys(self):
        keys = []
        for row in range(self.list.count()):
            keys.append(self.list.item(row).data(Qt.UserRole))
        return keys

    def _selected_job(self):
        row = self.list.currentRow()
        if row < 0:
            return None
        key = self.list.item(row).data(Qt.UserRole)
        return next((job for job in self._jobs if job["key"] == key), None)

    def _sync_color_btn(self, *_args):
        job = self._selected_job()
        if job and job.get("blank"):
            rgb = tuple(job.get("color") or self._blank_color)
        else:
            rgb = self._blank_color
        self.swatch.setStyleSheet(
            f"QPushButton {{ border: 1px solid #3F3F46; border-radius: 4px; "
            f"background: rgb({rgb[0]},{rgb[1]},{rgb[2]}); }}"
            f"QPushButton:hover {{ border: 1px solid #71717A; }}"
        )

    def _emit(self):
        order = self.ordered_keys()
        by_key = {job["key"]: job for job in self._jobs}
        self._jobs = [by_key[k] for k in order if k in by_key]
        self._on_change(list(self._jobs))

    def _nudge(self, delta):
        row = self.list.currentRow()
        if row < 0:
            return
        target = row + delta
        if target < 0 or target >= self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self._emit()

    def _add_blank(self):
        self._blank_seq += 1
        key = f"__blank__{self._blank_seq}"
        job = {
            "key": key, "path": None, "page": None, "label": "Blank page",
            "blank": True, "color": tuple(self._blank_color),
        }
        row = self.list.currentRow()
        insert_at = row + 1 if row >= 0 else self.list.count()
        self._jobs.insert(insert_at, job)
        self.set_jobs(self._jobs, self._thumbs)
        self.list.setCurrentRow(insert_at)
        self._on_change(list(self._jobs))

    def _apply_blank_color(self, color):
        if color is None or not color.isValid():
            return
        rgb = (color.red(), color.green(), color.blue())
        self._blank_color = rgb
        job = self._selected_job()
        if job is not None and job.get("blank"):
            job["color"] = rgb
            self.set_jobs(self._jobs, self._thumbs)
            self._on_change(list(self._jobs))
        else:
            self._sync_color_btn()

    def _pick_blank_color(self):
        job = self._selected_job()
        if job and job.get("blank"):
            current = QColor(*(job.get("color") or self._blank_color))
        else:
            current = QColor(*self._blank_color)
        chosen = QColorDialog.getColor(current, self, "Blank page color")
        if chosen.isValid():
            self._apply_blank_color(chosen)

    def _remove_selected(self):
        row = self.list.currentRow()
        if row < 0:
            return
        item = self.list.takeItem(row)
        key = item.data(Qt.UserRole)
        self._jobs = [job for job in self._jobs if job["key"] != key]
        self._on_change(list(self._jobs))
        self._sync_color_btn()


class _CropGrid(QScrollArea):
    """Multi-column crop cards for the fullscreen source dialog."""

    def __init__(self, cols=4):
        super().__init__()
        self.cols = max(1, int(cols))
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.grid.setSpacing(12)
        self.setWidget(self.inner)
        self.cards = {}

    def rebuild(self, jobs, aspect, on_crop, on_zoom=None, side=220, on_expand=None, on_rotate=None, on_remove=None):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.cards = {}
        # Uniform tile height (landscape-ish) so portraits letterbox instead of stretching the grid.
        height = max(48, int(side * 0.72))
        self._tile = (side, height)
        for index, job in enumerate(jobs):
            frame = QFrame()
            frame.setObjectName("card")
            frame.setAttribute(Qt.WA_StyledBackground, True)
            frame.setFixedWidth(side + 12)
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(6, 6, 6, 6)
            view = CropView()
            view.setFixedSize(side, height)
            view.cropChanged.connect(lambda fractions, key=job["key"]: on_crop(key, fractions))
            if on_zoom is not None:
                view.previewZoom.connect(on_zoom)
            if on_expand is not None:
                view.expandRequested.connect(lambda key=job["key"]: on_expand(key))
            title = QHBoxLayout()
            name = QLabel(job["label"])
            name.setWordWrap(True)
            name.setObjectName("muted")
            title.addWidget(name, 1)
            if on_remove is not None:
                remove = button("✕", "ghost")
                remove.setToolTip("Remove this file")
                remove.clicked.connect(lambda _=False, key=job["key"]: on_remove(key))
                title.addWidget(remove)
            layout.addWidget(view)
            layout.addLayout(title)
            if on_rotate is not None:
                row = QHBoxLayout()
                left = button("↺", "ghost")
                right = button("↻", "ghost")
                left.clicked.connect(lambda _=False, key=job["key"]: on_rotate(key, 90))
                right.clicked.connect(lambda _=False, key=job["key"]: on_rotate(key, -90))
                row.addWidget(left)
                row.addWidget(right)
                row.addStretch(1)
                layout.addLayout(row)
            self.grid.addWidget(frame, index // self.cols, index % self.cols, Qt.AlignTop | Qt.AlignLeft)
            self.cards[job["key"]] = view
        self.grid.setColumnStretch(self.cols, 1)
        self.grid.setRowStretch((len(jobs) + self.cols - 1) // self.cols, 1)

    def show_image(self, key, image, aspect, fractions):
        view = self.cards.get(key)
        if view is None:
            return
        side, height = getattr(self, "_tile", (view.width() or 220, view.height() or 160))
        view.setFixedSize(side, height)
        view.set_preview(image, aspect, fractions)


class _CollageView(QWidget):
    """One sheet. Drag pans the crop; drag onto another photo swaps them."""

    def __init__(self, on_change):
        super().__init__()
        self._on_change = on_change
        self.setMinimumSize(320, 320)
        self.image = QImage()
        self.paper = (210, 297)
        self.items = []
        self.keys = []
        self.crops = {}
        self._drag = None
        self._selected = None

    def set_sheet(self, image, paper, items, keys, crops):
        self.image = image
        self.paper = paper
        self.items = items
        self.keys = keys
        self.crops = crops
        self.update()

    def paintEvent(self, _event):
        from PySide6.QtGui import QPainter, QColor, QPen
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#12121A"))
        if self.image.isNull():
            painter.setPen(QColor("#7878A0"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Add photos to preview the collage")
            painter.end()
            return
        ox, oy, dw, dh = self._fit()
        painter.drawImage(__import__("PySide6.QtCore", fromlist=["QRect"]).QRect(ox, oy, dw, dh), self.image)
        if self._selected is not None:
            box = self._box(self._selected)
            if box:
                painter.setPen(QPen(QColor("#5B6BFF"), 2))
                painter.drawRect(*box)
        painter.end()

    def _fit(self):
        if self.image.isNull():
            return 0, 0, 1, 1
        scale = min((self.width() - 16) / self.image.width(), (self.height() - 16) / self.image.height())
        dw, dh = max(1, int(self.image.width() * scale)), max(1, int(self.image.height() * scale))
        return (self.width() - dw) // 2, (self.height() - dh) // 2, dw, dh

    def _box(self, index):
        ox, oy, dw, dh = self._fit()
        pw, ph = self.paper
        for item_index, x, y, w, h in self.items:
            if item_index == index:
                return (
                    int(ox + x / pw * dw), int(oy + y / ph * dh),
                    max(1, int(w / pw * dw)), max(1, int(h / ph * dh)),
                )
        return None

    def _hit(self, pos):
        for item_index, x, y, w, h in self.items:
            box = self._box(item_index)
            if not box:
                continue
            left, top, width, height = box
            if left <= pos.x() <= left + width and top <= pos.y() <= top + height:
                return item_index
        return None

    def mousePressEvent(self, event):
        index = self._hit(event.position().toPoint())
        self._selected = index
        if index is None or index >= len(self.keys):
            self._drag = None
            self.update()
            return
        key = self.keys[index]
        crop = dict(self.crops.get(key) or {"cx": 0.5, "cy": 0.5, "zoom": 1.0})
        self._drag = (index, event.position().toPoint(), crop, None)
        self.update()

    def mouseMoveEvent(self, event):
        if not self._drag:
            return
        index, start, crop, _swap = self._drag
        other = self._hit(event.position().toPoint())
        if other is not None and other != index and (event.position().toPoint() - start).manhattanLength() > 18:
            self._drag = (index, start, crop, other)
            return
        box = self._box(index)
        if not box:
            return
        dx = (event.position().x() - start.x()) / max(1, box[2])
        dy = (event.position().y() - start.y()) / max(1, box[3])
        moved = {
            "cx": min(1.0, max(0.0, crop["cx"] - dx)),
            "cy": min(1.0, max(0.0, crop["cy"] - dy)),
            "zoom": crop.get("zoom", 1.0),
        }
        self.crops[self.keys[index]] = moved
        self._on_change()

    def mouseReleaseEvent(self, _event):
        if self._drag and self._drag[3] is not None:
            self._on_change(self._drag[0], self._drag[3])
        self._drag = None

    def wheelEvent(self, event):
        index = self._selected if self._selected is not None else self._hit(event.position().toPoint())
        if index is None or not event.angleDelta().y() or index >= len(self.keys):
            return
        key = self.keys[index]
        crop = dict(self.crops.get(key) or {"cx": 0.5, "cy": 0.5, "zoom": 1.0})
        step = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        crop["zoom"] = min(4.0, max(1.0, float(crop.get("zoom", 1.0)) * step))
        self.crops[key] = crop
        self._selected = index
        self._on_change()
        event.accept()


_labeled = labeled


def _switch_row(text, checkbox=None):
    """Label on the left, pill switch on the right."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    label.setWordWrap(True)
    box = checkbox if checkbox is not None else QCheckBox()
    box.setText("")
    box.setProperty("role", "switch")
    polish(box)
    layout.addWidget(label, 1)
    layout.addWidget(box, 0, Qt.AlignRight | Qt.AlignVCenter)
    return row, box


class ImposeWorkspace:
    _LAYOUT, _BOOKLET, _POLAROID, _COLLAGE, _CUT = 0, 1, 2, 3, 4
    # Booklet shares the print-layout sheet board canvas.
    _VIEW = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3}

    def __init__(self, shell):
        self.shell = shell
        self.files = []
        self.jobs = []
        self.rotations = {}
        self.crops = {}
        self.fractions = {}
        self.back = None
        self.cut_state = None
        self._thumbs = {}
        self._signals = []
        self.sheet_zoom = 1.0
        self.source_zoom = 1.0
        self._loading_layout = False
        self._loading_booklet = False
        self._placements = []
        self.source_visible = False
        self.inspector = QWidget()
        self.canvas = QWidget()
        self.source_panel = QWidget()
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._preview)
        self._reorient_timer = QTimer()
        self._reorient_timer.setSingleShot(True)
        self._reorient_timer.setInterval(180)
        self._reorient_timer.timeout.connect(self._reorient_sources_now)
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
        self.mode = QComboBox()
        self.mode.addItems(["Print layout", "Booklet", "Polaroid", "Collage", "Sticker outline"])
        self.mode.currentIndexChanged.connect(self._switch)
        form.addWidget(self.mode)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._layout_form())
        self.stack.addWidget(self._booklet_form())
        self.stack.addWidget(self._polaroid_form())
        self.stack.addWidget(self._collage_form())
        self.stack.addWidget(self._cut_form())
        form.addWidget(self.stack)
        form.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        sticky = QWidget()
        actions = QVBoxLayout(sticky)
        actions.setContentsMargins(8, 8, 8, 8)
        actions.setSpacing(8)
        self.export_btn = button("Export PDF", "success")
        self.export_btn.clicked.connect(self._export)
        self.preview_btn = button("Preview", "secondary")
        self.preview_btn.clicked.connect(self._open_preview)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.export_btn, 1)
        top.addWidget(self.preview_btn)
        actions.addLayout(top)
        self.jpeg_btn = button("Export JPEG", "secondary")
        self.jpeg_btn.clicked.connect(self._export_jpeg)
        actions.addWidget(self.jpeg_btn)
        row = QHBoxLayout()
        row.setSpacing(8)
        add = button("Add", "secondary")
        clear = button("Clear", "secondary")
        add.clicked.connect(self._add)
        clear.clicked.connect(self.clear)
        row.addWidget(add)
        row.addWidget(clear)
        actions.addLayout(row)
        self.note = muted("")
        self.note.hide()
        actions.addWidget(self.note)
        root.addWidget(sticky)

        canvas = QVBoxLayout(self.canvas)
        canvas.setContentsMargins(0, 0, 0, 0)
        self.views = QStackedWidget()
        self.views.addWidget(self._layout_canvas())
        self.views.addWidget(self._polaroid_canvas())
        self.views.addWidget(self._collage_canvas())
        self.views.addWidget(self._cut_canvas())
        canvas.addWidget(self.views)

        side = QVBoxLayout(self.source_panel)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        header_side = QHBoxLayout()
        header_side.addWidget(section("Source files"), 1)
        self.full_btn = button("Full screen", "secondary")
        self.full_btn.clicked.connect(self._fullscreen_crops)
        header_side.addWidget(self.full_btn)
        side.addLayout(header_side)
        self.source_stack = QStackedWidget()
        self.sources = _CropColumn(False)
        self.page_list = _PageList(self._booklet_pages_changed)
        self.source_stack.addWidget(self.sources)
        self.source_stack.addWidget(self.page_list)
        side.addWidget(self.source_stack, 1)

        self._shrink_fields()
        for sequence, direction in (("Ctrl++", 1), ("Ctrl+=", 1), ("Ctrl+-", -1)):
            QShortcut(QKeySequence(sequence), self.canvas, lambda d=direction: self._guard_zoom(d))
        QShortcut(QKeySequence("Ctrl+P"), self.canvas, self._ctrl_p)
        self._switch()

    def _set_note(self, text=""):
        text = text or ""
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def _shrink_fields(self):
        """Keep two-column rows inside the options panel. Menus must not grow a sideways scrollbar."""
        field = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.stack.setMinimumWidth(0)
        self.stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        for index in range(self.stack.count()):
            page = self.stack.widget(index)
            page.setMinimumWidth(0)
            page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        for combo in self.inspector.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(0)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(field)
            view = combo.view()
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            view.setTextElideMode(Qt.ElideRight)
        for spin in self.inspector.findChildren(QAbstractSpinBox):
            spin.setMinimumWidth(0)
            spin.setSizePolicy(field)

    def _layout_canvas(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.sheet_title = section("Final imposed sheets")
        header.addWidget(self.sheet_title)
        self.sheet_count = muted("0 sheets")
        header.addStretch(1)
        minus = button("−", "ghost")
        plus = button("+", "ghost")
        minus.clicked.connect(lambda: self._nudge_zoom(-1))
        plus.clicked.connect(lambda: self._nudge_zoom(1))
        self.zoom_label = muted("100%")
        header.addWidget(self.sheet_count)
        header.addWidget(minus)
        header.addWidget(self.zoom_label)
        header.addWidget(plus)
        layout.addLayout(header)
        stage = _FloatStage()
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        self.board = _SheetBoard(self._nudge_zoom)
        stage_layout.addWidget(self.board, 1)
        self.book_view_btn = button("Book preview", "secondary")
        self.book_view_btn.setToolTip("Toggle imposed print sheets vs folded reader spreads")
        self.book_view_btn.clicked.connect(self._toggle_booklet_preview)
        self.book_view_btn.setVisible(False)
        stage.set_float_button(self.book_view_btn)
        self.book_preview_mode = "sheets"
        layout.addWidget(stage, 1)
        self._sheet_stage = stage
        return page

    def _polaroid_canvas(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section("Sheet preview"))
        self.polaroid_preview = QLabel("Add photos to preview the first sheet")
        self.polaroid_preview.setAlignment(Qt.AlignCenter)
        self.polaroid_preview.setMinimumHeight(280)
        layout.addWidget(self.polaroid_preview, 1)
        return page

    def _collage_canvas(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.collage = _CollageView(self._collage_changed)
        layout.addWidget(self.collage, 1)
        return page

    def _cut_canvas(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.cut_preview = QLabel("Artwork preview")
        self.cut_preview.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.cut_preview, 1)
        return page

    def _paper_combo(self):
        combo = QComboBox()
        combo.addItems(paper_names())
        for preferred in ("SRA4", "A4"):
            index = combo.findText(preferred)
            if index >= 0:
                combo.setCurrentIndex(index)
                break
        combo.currentIndexChanged.connect(self._schedule)
        return combo

    def _spin(self, value, step=1, maximum=2000):
        spin = QDoubleSpinBox()
        spin.setRange(0, maximum)
        spin.setDecimals(2)
        spin.setValue(value)
        spin.setSingleStep(step)
        spin.valueChanged.connect(self._schedule)
        return spin

    def _mm_spin(self, value, maximum=2000):
        spin = QDoubleSpinBox()
        spin.setRange(0, maximum)
        spin.setDecimals(0)
        spin.setValue(value)
        spin.setSingleStep(1)
        spin.valueChanged.connect(self._schedule)
        return spin

    def _toggle(self, text):
        row, box = _switch_row(text)
        box._row = row
        return box

    def _paper_mm(self):
        name = self.lay_paper.currentText()
        pair = None
        if name == "Custom…":
            pair = (self.paper_w.value(), self.paper_h.value())
        return paper_mm(name, self.lay_orient.currentText(), pair)

    def _sync_custom_paper(self, _name=None):
        self.custom_paper_row.setVisible(self.lay_paper.currentText() == "Custom…")

    def _sync_marks(self, *_args):
        on = self.cut_marks.isChecked()
        self.cut_labels.setEnabled(on)
        self.cut_in_bleed.setEnabled(on)

    def _layout_form(self):
        page = QWidget()
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        self.print_layouts = ct.load_print_layouts()
        self.layout_preset = QComboBox()
        self.layout_preset.addItems(list(self.print_layouts))
        self.layout_preset.currentTextChanged.connect(self._load_print_layout)
        save_layout = button("Save", "success")
        save_layout.clicked.connect(self._save_print_layout)
        signature = QHBoxLayout()
        signature.setSpacing(8)
        signature.addWidget(self.layout_preset, 1)
        signature.addWidget(save_layout)
        signature_wrap = QWidget()
        signature_wrap.setLayout(signature)

        self.lay_mode = QComboBox()
        self.lay_mode.addItems(["Repeat", "Cut & stack", "In order"])
        self.lay_mode.currentIndexChanged.connect(self._schedule)
        self.lay_paper = self._paper_combo()
        self.lay_paper.addItem("Custom…")
        self.lay_paper.currentTextChanged.connect(self._sync_custom_paper)
        self.lay_orient = QComboBox()
        self.lay_orient.addItems(["Portrait", "Landscape"])
        self.lay_orient.currentIndexChanged.connect(self._schedule)

        self.custom_paper_row = QWidget()
        custom_row = QHBoxLayout(self.custom_paper_row)
        custom_row.setContentsMargins(0, 0, 0, 0)
        custom_row.setSpacing(8)
        self.paper_w = self._mm_spin(210)
        self.paper_h = self._mm_spin(297)
        custom_row.addWidget(_labeled("Width", self.paper_w), 1)
        custom_row.addWidget(_labeled("Height", self.paper_h), 1)
        self.custom_paper_row.hide()

        self._proportion_ratio = None
        self._linking_size = False
        self.item_preset = QComboBox()
        self.item_preset.addItem("Custom")
        self.item_preset.addItem("Actual size")
        self.item_preset.addItems(paper_names())
        self.item_preset.currentTextChanged.connect(self._apply_item_preset)
        self.item_w = self._mm_spin(90)
        self.item_h = self._mm_spin(50)
        self.item_w.valueChanged.connect(self._item_w_changed)
        self.item_h.valueChanged.connect(self._item_h_changed)
        self.chain_row, self.chain_btn = _switch_row("Keep proportions")
        self.chain_btn.setToolTip("Keep item proportions (match first photo)")
        self.chain_btn.toggled.connect(self._toggle_proportional)

        self.cols = QSpinBox()
        self.cols.setRange(1, 40)
        self.cols.setValue(2)
        self.rows = QSpinBox()
        self.rows.setRange(1, 40)
        self.rows.setValue(5)
        self.cols.valueChanged.connect(self._schedule)
        self.rows.valueChanged.connect(self._schedule)
        self.lay_bleed = self._mm_spin(0, 50)
        self.spacing = self._mm_spin(0, 100)

        self.lay_gray = QCheckBox("Grayscale")
        self.lay_gray.stateChanged.connect(self._schedule)
        self.brightness = QSpinBox()
        self.brightness.setRange(10, 200)
        self.brightness.setValue(100)
        self.brightness.valueChanged.connect(self._schedule)
        self.back_row, self.back_btn = _switch_row("Shared back")
        self.back_btn.setToolTip("One image on the back of every sheet.")
        self.back_btn.toggled.connect(self._pick_back)
        self.back_file = muted("")
        self.back_file.hide()

        self.cut_marks = self._toggle("Cut marks")
        self.cut_labels = self._toggle("Labels")
        self.cut_in_bleed = self._toggle("In bleed")
        self.cut_in_bleed.setToolTip(
            "Off: marks in the sheet margin. On: short marks in the bleed at the trim."
        )
        self.cut_marks.stateChanged.connect(self._schedule)
        self.cut_labels.stateChanged.connect(self._schedule)
        self.cut_in_bleed.stateChanged.connect(self._schedule)
        self.cut_marks.stateChanged.connect(self._sync_marks)
        self.cut_labels.setEnabled(False)
        self.cut_in_bleed.setEnabled(False)

        layout_fields = QWidget()
        layout_stack = QVBoxLayout(layout_fields)
        layout_stack.setContentsMargins(0, 0, 0, 0)
        layout_stack.setSpacing(2)
        layout_stack.addWidget(_labeled("Layout type", self.lay_mode))
        layout_stack.addWidget(_labeled("Signature", signature_wrap))
        form.addWidget(group_card("Layout", layout_fields))
        form.addWidget(group_card(
            "Sheet format",
            pair(_labeled("Sheet", self.lay_paper), _labeled("Orientation", self.lay_orient)),
            self.custom_paper_row,
            _labeled("Page size", self.item_preset),
            pair(_labeled("W", self.item_w), _labeled("H", self.item_h)),
            self.chain_row,
        ))
        form.addWidget(group_card(
            "Grid",
            pair(_labeled("Columns", self.cols), _labeled("Rows", self.rows)),
            pair(_labeled("Bleed", self.lay_bleed), _labeled("Gap", self.spacing)),
        ))
        form.addWidget(group_card(
            "Color",
            pair(_labeled("Color", self.lay_gray), _labeled("Bright", self.brightness)),
            self.back_row,
            self.back_file,
        ))
        form.addWidget(group_card(
            "Marks",
            self.cut_marks._row,
            self.cut_labels._row,
            self.cut_in_bleed._row,
            spacing=8,
        ))
        form.addStretch(1)
        self._load_print_layout(self.layout_preset.currentText())
        return page

    def _booklet_form(self):
        page = QWidget()
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self._loading_booklet = False

        self.book_preset = QComboBox()
        self.book_preset.addItems(list(booklet_engine.PRESETS))
        self.book_preset.currentTextChanged.connect(self._apply_booklet_preset)

        self.book_paper = self._paper_combo()
        self.book_paper.addItem("Custom…")
        self.book_paper.currentTextChanged.connect(self._sync_booklet_custom_paper)
        self.book_orient = QComboBox()
        self.book_orient.addItems(["Landscape", "Portrait"])
        self.book_orient.currentIndexChanged.connect(self._schedule)

        self.book_custom_paper_row = QWidget()
        custom_row = QHBoxLayout(self.book_custom_paper_row)
        custom_row.setContentsMargins(0, 0, 0, 0)
        custom_row.setSpacing(8)
        self.book_paper_w = self._mm_spin(450)
        self.book_paper_h = self._mm_spin(320)
        custom_row.addWidget(_labeled("Width", self.book_paper_w), 1)
        custom_row.addWidget(_labeled("Height", self.book_paper_h), 1)
        self.book_custom_paper_row.hide()

        self.book_layout = "2up"
        self.book_page_preset = QComboBox()
        self.book_page_preset.addItem("Custom")
        self.book_page_preset.addItem("Actual size")
        for name in ("A6", "A5", "A4", "A3"):
            self.book_page_preset.addItem(name)
        for name in paper_names():
            if self.book_page_preset.findText(name) < 0:
                self.book_page_preset.addItem(name)
        self.book_page_preset.currentTextChanged.connect(self._apply_booklet_page_preset)
        self.book_page_w = self._mm_spin(210)
        self.book_page_h = self._mm_spin(297)
        self.book_page_w.valueChanged.connect(self._booklet_page_changed)
        self.book_page_h.valueChanged.connect(self._booklet_page_changed)

        self.book_bleed = self._mm_spin(0, 50)
        self.book_gap = self._mm_spin(0, 100)

        self.book_gray = QCheckBox("Grayscale")
        self.book_gray.stateChanged.connect(self._schedule)
        self.book_brightness = QSpinBox()
        self.book_brightness.setRange(10, 200)
        self.book_brightness.setValue(100)
        self.book_brightness.valueChanged.connect(self._schedule)

        self.book_marks = self._toggle("Cut marks")
        self.book_labels = self._toggle("Labels")
        self.book_in_bleed = self._toggle("In bleed")
        self.book_marks.stateChanged.connect(self._schedule)
        self.book_labels.stateChanged.connect(self._schedule)
        self.book_in_bleed.stateChanged.connect(self._schedule)
        self.book_marks.stateChanged.connect(self._sync_booklet_marks)
        self.book_labels.setEnabled(False)
        self.book_in_bleed.setEnabled(False)

        split_btn = button("Split spreads…", "secondary")
        split_btn.setToolTip("Split facing PDF pages into singles, then load into this booklet.")
        split_btn.clicked.connect(self._split_spreads_dialog)

        form.addWidget(group_card("Preset", _labeled("Booklet preset", self.book_preset)))
        form.addWidget(group_card(
            "Sheet",
            pair(_labeled("Sheet", self.book_paper), _labeled("Orientation", self.book_orient)),
            self.book_custom_paper_row,
        ))
        form.addWidget(group_card(
            "Page size",
            _labeled("Page size", self.book_page_preset),
            pair(_labeled("W", self.book_page_w), _labeled("H", self.book_page_h)),
            pair(_labeled("Bleed", self.book_bleed), _labeled("Spine gap", self.book_gap)),
            muted("Bleed expands outside only — never into the spine."),
        ))
        form.addWidget(group_card(
            "Color",
            pair(_labeled("Color", self.book_gray), _labeled("Bright", self.book_brightness)),
        ))
        form.addWidget(group_card(
            "Marks",
            self.book_marks._row,
            self.book_labels._row,
            self.book_in_bleed._row,
            spacing=8,
        ))
        form.addWidget(group_card("Tools", split_btn))
        form.addStretch(1)
        self._apply_booklet_preset(self.book_preset.currentText())
        return page

    def _sync_booklet_custom_paper(self, _name=None):
        self.book_custom_paper_row.setVisible(self.book_paper.currentText() == "Custom…")
        self._schedule()

    def _sync_booklet_marks(self, *_args):
        on = self.book_marks.isChecked()
        self.book_labels.setEnabled(on)
        self.book_in_bleed.setEnabled(on)

    def _apply_booklet_preset(self, name):
        if self._loading_booklet:
            return
        preset = booklet_engine.PRESETS.get(name)
        if not preset:
            self._schedule()
            return
        self._loading_booklet = True
        try:
            self._select_text(self.book_paper, preset["sheet"])
            self.book_orient.setCurrentText(preset["sheet_orient"])
            self.book_layout = preset.get("layout", "2up")
            page = preset["page"]
            if self.book_page_preset.findText(page) < 0:
                self.book_page_preset.addItem(page)
            self.book_page_preset.setCurrentText(page)
            width, height = booklet_engine.page_size_mm(page)
            self.book_page_w.setValue(width)
            self.book_page_h.setValue(height)
            # A4 is exactly 2×A6 wide / ~2 tall — outer bleed needs SRA4 (or zero bleed).
            if preset.get("layout") == "4up" and preset.get("sheet") == "A4":
                self.book_bleed.setValue(0)
        finally:
            self._loading_booklet = False
        self._sync_booklet_custom_paper()
        self._schedule()

    def _apply_booklet_page_preset(self, name):
        if self._loading_booklet or not name or name == "Custom":
            return
        if name == "Actual size":
            size = self._actual_item_mm()
            if size is None:
                self.shell.report("Add a source file first to use actual size.")
                self._set_note("Add a source file first to use actual size.")
                self.book_page_preset.blockSignals(True)
                self.book_page_preset.setCurrentIndex(0)
                self.book_page_preset.blockSignals(False)
                return
            width, height = size
        else:
            width, height = booklet_engine.page_size_mm(name)
        self._loading_booklet = True
        self.book_page_w.setValue(width)
        self.book_page_h.setValue(height)
        self._loading_booklet = False
        self._schedule()

    def _booklet_page_changed(self, *_args):
        if self._loading_booklet:
            return
        if self.book_page_preset.currentText() != "Custom":
            self.book_page_preset.blockSignals(True)
            self.book_page_preset.setCurrentText("Custom")
            self.book_page_preset.blockSignals(False)
            if self.book_preset.currentText() != "Custom":
                self.book_preset.blockSignals(True)
                self.book_preset.setCurrentText("Custom")
                self.book_preset.blockSignals(False)
        self._schedule()

    def _split_spreads_dialog(self):
        from copypro_qt.engines import documents as docs

        paths = choose_files(self.canvas, "Split spreads", "PDF (*.pdf)")
        if not paths:
            return
        dialog = QDialog(self.canvas)
        dialog.setWindowTitle("Split spreads")
        dialog.resize(440, 320)
        box = QVBoxLayout(dialog)
        box.addWidget(muted("Each facing page in range is split into two singles."))
        from_edit = QLineEdit()
        to_edit = QLineEdit()
        from_edit.setPlaceholderText("From (blank = start)")
        to_edit.setPlaceholderText("To (blank = end)")
        box.addWidget(labeled("From page", from_edit))
        box.addWidget(labeled("To page", to_edit))
        folder_edit = QLineEdit()
        folder_edit.setPlaceholderText("Same folder as each PDF")
        pick = button("Choose folder…", "secondary")

        def choose():
            path = QFileDialog.getExistingDirectory(dialog, "Output folder")
            if path:
                folder_edit.setText(path)

        pick.clicked.connect(choose)
        folder_row = QHBoxLayout()
        folder_row.addWidget(folder_edit, 1)
        folder_row.addWidget(pick)
        box.addWidget(muted("Output folder"))
        box.addLayout(folder_row)
        protect_covers = QCheckBox("First and last pages are covers (do not split)")
        protect_covers.setChecked(True)
        protect_covers.setToolTip("Keep PDF page 1 and the last page whole; only split interior pages in range.")
        box.addWidget(protect_covers)
        load_after = QCheckBox("Load split PDF into booklet pages")
        load_after.setChecked(True)
        box.addWidget(load_after)
        actions = QHBoxLayout()
        cancel = button("Cancel", "secondary")
        run = button("Split", "success")
        cancel.clicked.connect(dialog.reject)
        actions.addWidget(cancel)
        actions.addWidget(run, 1)
        box.addLayout(actions)

        def parse(text):
            text = (text or "").strip()
            if not text:
                return None
            return max(1, int(float(text.replace(",", "."))))

        def go():
            try:
                start, end = parse(from_edit.text()), parse(to_edit.text())
            except ValueError:
                QMessageBox.warning(dialog, "Page range", "Page numbers must be whole numbers, or blank.")
                return
            if start and end and start > end:
                start, end = end, start
            out_dir = folder_edit.text().strip()
            written = []
            try:
                for src in paths:
                    dest_dir = out_dir or os.path.dirname(src)
                    os.makedirs(dest_dir, exist_ok=True)
                    base = os.path.splitext(os.path.basename(src))[0]
                    dest = os.path.join(dest_dir, f"{base}_split.pdf")
                    docs.split_pdf(src, dest, start, end, protect_covers=protect_covers.isChecked())
                    written.append(dest)
            except Exception as err:
                QMessageBox.critical(dialog, "Split failed", str(err))
                return
            dialog.accept()
            self.shell.report(f"Split {len(written)} PDF(s)")
            if load_after.isChecked() and written:
                self.add_paths(written)

        run.clicked.connect(go)
        dialog.exec()

    def _pair(self, left, right):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(left, 1)
        row.addWidget(right, 1)
        return row

    def _polaroid_form(self):
        page = QWidget()
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self.pol_preset = QComboBox()
        self.pol_preset.addItems(list(polaroid_engine.PRESETS))
        self.pol_preset.currentIndexChanged.connect(self._apply_preset)
        self.pol_paper = self._paper_combo()
        self.pol_space = self._spin(3, 0.5, 40)
        self.pol_auto = self._toggle("Auto rows and columns")
        self.pol_auto.setChecked(True)
        self.pol_auto.stateChanged.connect(self._schedule)
        self.pol_cols = QSpinBox()
        self.pol_rows = QSpinBox()
        self.pol_cols.setRange(1, 20)
        self.pol_rows.setRange(1, 20)
        self.pol_cols.setValue(2)
        self.pol_rows.setValue(2)
        self.pol_cols.valueChanged.connect(self._schedule)
        self.pol_rows.valueChanged.connect(self._schedule)
        self.pol_marks = QComboBox()
        self.pol_marks.addItems(["Outline", "Crop marks"])
        self.pol_ratio = QComboBox()
        self.pol_ratio.addItems(list(polaroid_engine.RATIOS) + ["Original"])
        self.pol_ratio.currentIndexChanged.connect(self._schedule)
        form.addWidget(group_card(
            "Polaroid",
            _labeled("Frame", self.pol_preset),
            _labeled("Photo ratio", self.pol_ratio),
            pair(_labeled("Sheet", self.pol_paper), _labeled("Spacing", self.pol_space)),
            pair(_labeled("Columns", self.pol_cols), _labeled("Rows", self.pol_rows)),
            self.pol_auto._row,
            _labeled("Marks", self.pol_marks),
        ))
        form.addStretch(1)
        return page

    def _collage_form(self):
        page = QWidget()
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self.col_paper = self._paper_combo()
        self.col_orient = QComboBox()
        self.col_orient.addItems(["Portrait", "Landscape"])
        self.col_orient.currentIndexChanged.connect(self._schedule)
        self.col_mode = QComboBox()
        self.col_mode.addItems(["Fit page", "Photo size"])
        self.col_mode.currentIndexChanged.connect(self._schedule)
        self.gap = self._spin(3, 0.5, 40)
        self.margin = self._spin(5, 0.5, 40)
        self.photo_w = self._spin(100)
        self.photo_h = self._spin(70)
        self.col_dpi = QComboBox()
        self.col_dpi.addItems(["150", "300", "600"])
        self.col_dpi.setCurrentText("300")
        self.zoom = self._spin(1, 0.1, 4)
        self.zoom.setDecimals(2)
        self.zoom.valueChanged.connect(self._store_zoom)
        form.addWidget(group_card(
            "Collage",
            pair(_labeled("Paper", self.col_paper), _labeled("Orientation", self.col_orient)),
            _labeled("Mode", self.col_mode),
            pair(_labeled("Gap", self.gap), _labeled("Margin", self.margin)),
            pair(_labeled("Photo width", self.photo_w), _labeled("Photo height", self.photo_h)),
            pair(_labeled("DPI", self.col_dpi), _labeled("Zoom", self.zoom)),
        ))
        form.addStretch(1)
        return page

    def _cut_form(self):
        page = QWidget()
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self.tolerance = self._spin(15, 1, 80)
        self.tolerance.setDecimals(0)
        self.min_area = self._spin(0.05, 0.05, 5)
        self.offset = self._spin(0, 0.1, 10)
        self.offset.setMinimum(-3)
        self.holes = self._toggle("Include internal white areas")
        self.holes.stateChanged.connect(self._schedule)
        self.cut_dpi = self._spin(300, 10, 1200)
        self.cut_dpi.setDecimals(0)
        self.line_mm = self._spin(0.25, 0.05, 5)
        self.detail = self._spin(35, 1, 100)
        self.detail.setDecimals(0)
        self.cut_artwork = self._toggle("Include artwork")
        self.cut_format = QComboBox()
        self.cut_format.addItems(["PDF", "SVG"])
        form.addWidget(group_card(
            "Sticker outline",
            pair(_labeled("White tolerance", self.tolerance), _labeled("Minimum area", self.min_area)),
            pair(_labeled("Offset", self.offset), _labeled("Source DPI", self.cut_dpi)),
            pair(_labeled("Detail", self.detail), _labeled("Red line", self.line_mm)),
            self.holes._row,
            self.cut_artwork._row,
            _labeled("Format", self.cut_format),
            spacing=8,
        ))
        form.addStretch(1)
        return page

    def _switch(self):
        index = self.mode.currentIndex()
        self.stack.setCurrentIndex(index)
        self.views.setCurrentIndex(self._VIEW.get(index, 0))
        self.jpeg_btn.setVisible(index == self._COLLAGE)
        self.preview_btn.setVisible(index in (self._LAYOUT, self._BOOKLET))
        self.export_btn.setText("Export vector cut line" if index == self._CUT else "Export PDF")
        if index not in (self._LAYOUT, self._BOOKLET):
            self.shell.hide_fit_banner()
        booklet = index == self._BOOKLET
        self.source_stack.setCurrentIndex(1 if booklet else 0)
        self.full_btn.setVisible(not booklet)
        self.book_view_btn.setVisible(booklet)
        if hasattr(self, "_sheet_stage"):
            self._sheet_stage._place()
        if booklet:
            self.sheet_title.setText(
                "Folded book preview" if self.book_preview_mode == "book" else "Imposed print sheets"
            )
            self.book_view_btn.setText("Sheets" if self.book_preview_mode == "book" else "Book preview")
            self.board.set_layout_mode(self.book_preview_mode)
        else:
            self.sheet_title.setText("Final imposed sheets")
            self.book_preview_mode = "sheets"
            self.board.set_layout_mode("sheets")
        show_sources = index != self._CUT and bool(self.jobs)
        self.source_visible = show_sources
        self.shell.set_source_visible(show_sources)
        self._fill_sources()
        self._schedule()

    def _toggle_booklet_preview(self):
        self.book_preview_mode = "book" if self.book_preview_mode == "sheets" else "sheets"
        self.book_view_btn.setText("Sheets" if self.book_preview_mode == "book" else "Book preview")
        self.sheet_title.setText(
            "Folded book preview" if self.book_preview_mode == "book" else "Imposed print sheets"
        )
        self.board.set_layout_mode(self.book_preview_mode)
        if hasattr(self, "_sheet_stage"):
            self._sheet_stage._place()
        self._schedule()

    def _booklet_pages_changed(self, jobs):
        self.jobs = list(jobs)
        self.page_list.set_jobs(self.jobs, self._thumbs)
        self._schedule()

    def _schedule(self, *_args):
        if self._loading_layout:
            return
        self._preview_timer.start()

    def _apply_preset(self):
        values = polaroid_engine.PRESETS.get(self.pol_preset.currentText())
        if not values:
            return
        _w, _h, _side, _top, _bottom, ratio = values
        if ratio in polaroid_engine.RATIOS:
            self.pol_ratio.setCurrentText(ratio)
        self._schedule()

    def _pick_back(self, checked=True):
        if not self.back_btn.isChecked():
            self.back = None
            self.back_file.setText("")
            self.back_file.hide()
            self._schedule()
            return
        paths = choose_files(self.canvas, "Shared back", "Images (*.jpg *.jpeg *.png *.tif *.tiff)")
        if paths:
            self.back = paths[0]
            self.back_file.setText(os.path.basename(self.back))
            self.back_file.show()
        else:
            self.back = None
            self.back_btn.blockSignals(True)
            self.back_btn.setChecked(False)
            self.back_btn.blockSignals(False)
            self.back_file.setText("")
            self.back_file.hide()
        self._schedule()

    def _store_zoom(self, value):
        if not self.jobs:
            return
        self.crops[self.jobs[0]["key"]] = {"cx": 0.5, "cy": 0.5, "zoom": float(value)}
        self._schedule()

    def on_show(self):
        self._schedule()

    def on_tray(self):
        return

    def add_paths(self, paths):
        self.files.extend(ingest_paths(paths, self.files))
        self._reload_jobs()

    def clear(self):
        self.files.clear()
        self.jobs.clear()
        self.fractions.clear()
        self.crops.clear()
        self.rotations.clear()
        self._thumbs.clear()
        self.sources.rebuild([], 1, self._store_fraction)
        self._fit_source_column()
        self.source_visible = False
        self.shell.set_source_visible(False)
        self.board.set_sheets([])
        self.sheet_count.setText("0 sheets")
        self._schedule()

    def _add(self):
        paths = choose_files(self.canvas, "Add files", "Images and PDF (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.heic *.pdf)")
        if paths:
            self.add_paths(paths)

    def _reload_jobs(self):
        self.jobs = expand(self.files)
        self._fill_sources()
        self._schedule()

    def _layout_aspect(self):
        return max(0.05, self.item_w.value() / max(0.1, self.item_h.value()))

    def _source_side(self):
        # Prefer the scroll viewport so thumbs stay clear of the scrollbar.
        # Card width is side + 12 (margins); leave a slim gutter for the scrollbar.
        if hasattr(self, "sources") and self.sources.viewport().width() > 40:
            width = self.sources.viewport().width()
        elif hasattr(self, "source_panel") and self.source_panel.width() > 40:
            width = self.source_panel.width() - 16
        else:
            width = 180
        return max(88, min(188, width - 16))

    def _fit_source_column(self):
        if hasattr(self, "sources"):
            # Keep native per-image aspect; do not force item (often portrait) height.
            self.sources.set_card_side(self._source_side(), self.sources._aspect)

    def _crop_target_mm(self):
        mode = self.mode.currentIndex()
        if mode == self._POLAROID and self.jobs:
            frame, _layout = self._polaroid_layout(self.jobs)
            if frame:
                return frame[4], frame[5]
        if mode == self._BOOKLET:
            return self.book_page_w.value(), self.book_page_h.value()
        return self.item_w.value(), self.item_h.value()

    def _crop_aspect(self):
        width, height = self._crop_target_mm()
        return max(0.05, width / max(0.1, height))

    def _crop_aspect_for(self, image):
        """Keep the item's shape ratio, but match the photo's landscape/portrait."""
        item = self._crop_aspect()
        if image is None or image.isNull():
            return item
        img_landscape = image.width() >= image.height()
        item_landscape = item >= 1.0
        if img_landscape == item_landscape:
            return item
        return max(0.05, 1.0 / item)

    def _refresh_source_cards(self):
        if not hasattr(self, "sources"):
            return
        side = self._source_side()
        self.sources.set_card_side(side, self.sources._aspect)
        for key, view in self.sources.cards.items():
            image = self._thumbs.get(key)
            if image is not None and not image.isNull():
                aspect = self._crop_aspect_for(image)
                view.set_preview(image, aspect, self.fractions.get(key))

    def _expand_crop(self, key, on_applied=None):
        image = self._thumbs.get(key)
        if image is None or image.isNull():
            return
        dialog = QDialog(self.canvas)
        dialog.setWindowTitle("Crop")
        dialog.resize(1040, 780)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignCenter)
        scroll.setFrameShape(QFrame.NoFrame)
        view = CropView()
        aspect = self._crop_aspect_for(image)
        view.set_preview(image, aspect, self.fractions.get(key))
        scroll.setWidget(view)

        scale = [1.25]
        zoom_label = muted("125%")

        def fit_size():
            vw = max(320, scroll.viewport().width() - 8)
            vh = max(240, scroll.viewport().height() - 8)
            iw, ih = max(1, image.width()), max(1, image.height())
            fitted = min(vw / iw, vh / ih)
            width = max(160, int(iw * fitted * scale[0]))
            height = max(120, int(ih * fitted * scale[0]))
            view.setFixedSize(width, height)

        def nudge_zoom(direction):
            step = 1.2 if direction > 0 else 1 / 1.2
            scale[0] = max(0.75, min(6.0, scale[0] * step))
            zoom_label.setText(f"{int(round(scale[0] * 100))}%")
            fit_size()

        view.previewZoom.connect(nudge_zoom)
        toolbar = QHBoxLayout()
        toolbar.addWidget(muted("Scroll wheel zooms the crop · Ctrl/⌘+wheel zooms the view"), 1)
        minus = button("−", "ghost")
        plus = button("+", "ghost")
        minus.clicked.connect(lambda: nudge_zoom(-1))
        plus.clicked.connect(lambda: nudge_zoom(1))
        toolbar.addWidget(minus)
        toolbar.addWidget(zoom_label)
        toolbar.addWidget(plus)
        layout.addLayout(toolbar)
        layout.addWidget(scroll, 1)
        done = button("Use this crop", "success")
        done.clicked.connect(dialog.accept)
        layout.addWidget(done)

        # Size after the dialog has a real viewport.
        QTimer.singleShot(0, fit_size)
        if dialog.exec() != QDialog.Accepted:
            return
        self.fractions[key] = view.fractions()
        self.sources.show_image(key, image, aspect, self.fractions[key])
        if on_applied is not None:
            on_applied(key, self.fractions[key])
        self._schedule()

    def _fullscreen_crops(self):
        if not self.jobs:
            return
        dialog = QDialog(self.canvas)
        dialog.setWindowTitle("Source crops")
        dialog.resize(1180, 820)
        box = QVBoxLayout(dialog)
        box.setSpacing(8)
        item_aspect = self._crop_aspect()
        cols = 3 if len(self.jobs) <= 6 else 4
        grid = _CropGrid(cols)
        scale = [1.0]
        base_side = 260
        zoom_label = muted("100%")

        def current_side():
            return max(160, min(720, int(base_side * scale[0])))

        def on_crop(key, fractions):
            self.fractions[key] = fractions
            thumb = self._thumbs.get(key, QImage())
            self.sources.show_image(key, thumb, self._crop_aspect_for(thumb), fractions)

        def fill_grid():
            grid.rebuild(
                list(self.jobs), item_aspect, on_crop, nudge_zoom, current_side(),
                expand_one, on_rotate, on_remove,
            )
            for key, image in self._thumbs.items():
                if image is not None and not image.isNull():
                    grid.show_image(key, image, self._crop_aspect_for(image), self.fractions.get(key))

        def nudge_zoom(direction):
            step = 1.2 if direction > 0 else 1 / 1.2
            scale[0] = max(0.7, min(3.0, scale[0] * step))
            zoom_label.setText(f"{int(round(scale[0] * 100))}%")
            fill_grid()

        def on_rotate(key, delta):
            self._rotate_source(key, delta)
            image = self._thumbs.get(key)
            if image is not None and not image.isNull():
                grid.show_image(key, image, self._crop_aspect_for(image), self.fractions.get(key))

        def on_remove(key):
            self._remove_job(key)
            dialog.reject()

        def expand_one(key):
            def applied(k, fractions):
                image = self._thumbs.get(k)
                if image is not None and not image.isNull():
                    grid.show_image(k, image, self._crop_aspect_for(image), fractions)

            self._expand_crop(key, on_applied=applied)

        toolbar = QHBoxLayout()
        toolbar.addWidget(muted("Double-click a photo for a large zoomable crop"), 1)
        minus = button("−", "ghost")
        plus = button("+", "ghost")
        minus.clicked.connect(lambda: nudge_zoom(-1))
        plus.clicked.connect(lambda: nudge_zoom(1))
        toolbar.addWidget(minus)
        toolbar.addWidget(zoom_label)
        toolbar.addWidget(plus)
        box.addLayout(toolbar)
        fill_grid()
        box.addWidget(grid, 1)
        close = button("Done", "success")
        close.clicked.connect(dialog.accept)
        box.addWidget(close)
        dialog.exec()
        self._fill_sources()
        self._schedule()

    def _remove_job(self, key):
        job = next((item for item in self.jobs if item["key"] == key), None)
        if job is None:
            return
        path = job.get("path")
        still = [item for item in self.jobs if item["key"] != key and path and item.get("path") == path]
        self.jobs = [item for item in self.jobs if item["key"] != key]
        if path and not still:
            self.files = [item for item in self.files if item != path]
        self.fractions.pop(key, None)
        self.rotations.pop(key, None)
        self.crops.pop(key, None)
        self._thumbs.pop(key, None)
        self._fill_sources()
        self._schedule()

    def _duplicate_job(self, key):
        job = next((item for item in self.jobs if item["key"] == key), None)
        if job is None or job.get("blank"):
            return
        n = 2
        new_key = f"{key}__dup{n}"
        existing = {item["key"] for item in self.jobs}
        while new_key in existing:
            n += 1
            new_key = f"{key}__dup{n}"
        clone = dict(job)
        clone["key"] = new_key
        label = job.get("label") or "Image"
        if " (copy" not in label:
            clone["label"] = f"{label} (copy)"
        index = next(i for i, item in enumerate(self.jobs) if item["key"] == key)
        self.jobs.insert(index + 1, clone)
        if key in self.fractions:
            self.fractions[new_key] = dict(self.fractions[key])
        if key in self.rotations:
            self.rotations[new_key] = self.rotations[key]
        if key in self.crops:
            self.crops[new_key] = dict(self.crops[key])
        thumb = self._thumbs.get(key)
        if thumb is not None and not thumb.isNull():
            self._thumbs[new_key] = thumb.copy()
        self._fill_sources()
        self._schedule()

    def _fill_sources(self):
        jobs = list(self.jobs)
        booklet = self.mode.currentIndex() == self._BOOKLET
        if booklet:
            self.page_list.set_jobs(jobs, self._thumbs)
        else:
            aspect = self._crop_aspect()
            real = [job for job in jobs if not job.get("blank")]
            self.sources.rebuild(
                real, aspect, self._store_fraction,
                None, self._source_side(), self._expand_crop, self._rotate_source,
                self._remove_job, self._duplicate_job,
            )
            self._fit_source_column()
        show = self.mode.currentIndex() != self._CUT and bool(jobs)
        self.source_visible = show
        self.shell.set_source_visible(show)
        generation = len(self._signals)
        for job in jobs:
            if job.get("blank"):
                continue
            self._queue_thumb(job, generation)

    def _queue_thumb(self, job, generation, auto_orient=False):
        """Source panel shows native orientation (landscape stays wide). Export still uses rotations."""
        if job.get("blank") or not job.get("path"):
            return
        target = self._crop_target_mm()
        rotation = int(self.rotations.get(job["key"], 0) or 0)

        def work():
            # Display thumb: keep file orientation. Also compute auto-matched rotation for export.
            display = oriented_thumb(job, target[0], target[1], rotation, auto_orient=False)
            if auto_orient:
                _, _, effective = oriented_thumb(job, target[0], target[1], rotation, auto_orient=True)
                return display[0], display[1], effective
            return display

        def done(result):
            key, data, effective = result
            previous = int(self.rotations.get(key, 0) or 0)
            if auto_orient:
                self.rotations[key] = effective
                if previous != effective:
                    self.fractions.pop(key, None)
            image = QImage()
            image.loadFromData(data)
            self._thumbs[key] = image
            aspect = self._crop_aspect_for(image)
            if self.mode.currentIndex() == self._BOOKLET:
                self.page_list.set_jobs(self.jobs, self._thumbs)
            else:
                self.sources.show_image(key, image, aspect, self.fractions.get(key))

        self._signals.append(run_bg(work, done, parent=self.canvas))

    def _rotate_source(self, key, delta):
        current = int(self.rotations.get(key, 0) or 0)
        self.rotations[key] = (current + int(delta)) % 360
        self.fractions.pop(key, None)
        job = next((item for item in self.jobs if item["key"] == key), None)
        if job is not None:
            self._queue_thumb(job, len(self._signals), auto_orient=False)
        self._schedule()

    def _reorient_sources(self):
        self._reorient_timer.start()

    def _reorient_sources_now(self):
        """Item size changed: refresh crop aspect; keep existing crop fractions."""
        if not self.jobs:
            return
        self._fit_source_column()
        generation = len(self._signals)
        for job in self.jobs:
            # Update export rotation to match new item aspect; sidebar display stays native.
            self._queue_thumb(job, generation, auto_orient=True)

    def _store_fraction(self, key, fractions):
        self.fractions[key] = fractions
        self._schedule()

    def _apply_item_preset(self, name):
        if not name or name == "Custom" or self._loading_layout:
            return
        if name == "Actual size":
            size = self._actual_item_mm()
            if size is None:
                self.shell.report("Add a source file first to use actual size.")
                self._set_note("Add a source file first to use actual size.")
                self.item_preset.blockSignals(True)
                self.item_preset.setCurrentIndex(0)
                self.item_preset.blockSignals(False)
                return
            width, height = size
        else:
            width, height = paper_size_raw(name)
        self._linking_size = True
        self.item_w.setValue(width)
        self.item_h.setValue(height)
        self._linking_size = False
        if self.chain_btn.isChecked():
            self._proportion_ratio = width / max(0.1, height)
        self._reorient_sources()
        self._schedule()

    def _actual_item_mm(self):
        if not self.jobs:
            return None
        job = self.jobs[0]
        dpi = 300
        try:
            if job.get("page") is not None:
                doc = ct.fitz.open(job["path"])
                try:
                    rect = doc[job["page"]].rect
                    # PDF points → mm
                    return rect.width * 25.4 / 72.0, rect.height * 25.4 / 72.0
                finally:
                    doc.close()
            from PIL import Image, ImageOps
            with Image.open(job["path"]) as opened:
                image = ImageOps.exif_transpose(opened)
                width, height = image.size
                if image is not opened:
                    image.close()
            return width * 25.4 / dpi, height * 25.4 / dpi
        except Exception:
            return None

    def _toggle_proportional(self, linked):
        if linked:
            ratio = self._first_source_ratio()
            if ratio is None:
                ratio = self.item_w.value() / max(0.1, self.item_h.value())
            self._proportion_ratio = ratio
            self._linking_size = True
            self.item_h.setValue(self.item_w.value() / max(0.05, ratio))
            self._linking_size = False
            self._reorient_sources()
            self._schedule()
        else:
            self._proportion_ratio = None

    def _first_source_ratio(self):
        if not self.jobs:
            return None
        try:
            return _aspect(self.jobs[0])
        except Exception:
            return None

    def _item_w_changed(self, value):
        if self._linking_size or self._loading_layout:
            return
        if self.chain_btn.isChecked() and self._proportion_ratio:
            self._linking_size = True
            self.item_h.setValue(value / max(0.05, self._proportion_ratio))
            self._linking_size = False
        self._reorient_sources()
        self._schedule()

    def _item_h_changed(self, value):
        if self._linking_size or self._loading_layout:
            return
        if self.chain_btn.isChecked() and self._proportion_ratio:
            self._linking_size = True
            self.item_w.setValue(value * self._proportion_ratio)
            self._linking_size = False
        self._reorient_sources()
        self._schedule()

    def _guard_zoom(self, direction):
        if self.shell._current == "impose" and self.mode.currentIndex() in (self._LAYOUT, self._BOOKLET):
            self._nudge_zoom(direction)

    def _nudge_zoom(self, direction):
        step = 1.12 if direction > 0 else 1 / 1.12
        self.sheet_zoom = max(0.5, min(3.0, self.sheet_zoom * step))
        self.zoom_label.setText(f"{int(round(self.sheet_zoom * 100))}%")
        self.board.set_zoom(self.sheet_zoom)

    def _collage_changed(self, left=None, right=None):
        if left is not None and right is not None and left < len(self.jobs) and right < len(self.jobs):
            self.jobs[left], self.jobs[right] = self.jobs[right], self.jobs[left]
            self._fill_sources()
        self._schedule()

    def _spec(self):
        paper = self._paper_mm()
        return {
            "paper": paper,
            "item": (self.item_w.value(), self.item_h.value()),
            "cols": self.cols.value(),
            "rows": self.rows.value(),
            "mode": self.lay_mode.currentText(),
            "duplex": self.lay_mode.currentText() == "Cut & stack",
            "bleed": self.lay_bleed.value(),
            "spacing": self.spacing.value(),
            "grayscale": self.lay_gray.isChecked(),
            "brightness": self.brightness.value(),
            "dpi": 300,
            "cut_marks": self.cut_marks.isChecked(),
            "cut_labels": self.cut_labels.isChecked(),
            "cut_mark_style": "in_bleed" if self.cut_in_bleed.isChecked() else "outside",
            "back": self.back,
            "sources": sources_of(self.jobs),
            "rotations": dict(self.rotations),
            "fractions": dict(self.fractions),
        }

    def _booklet_paper_mm(self):
        name = self.book_paper.currentText()
        pair = None
        if name == "Custom…":
            pair = (self.book_paper_w.value(), self.book_paper_h.value())
        return paper_mm(name, self.book_orient.currentText(), pair)

    def _booklet_spec(self):
        layout = getattr(self, "book_layout", "2up")
        if self.book_preset.currentText() != "Custom":
            preset = booklet_engine.PRESETS.get(self.book_preset.currentText()) or {}
            layout = preset.get("layout", layout)
            self.book_layout = layout
        return {
            "paper": self._booklet_paper_mm(),
            "page": (self.book_page_w.value(), self.book_page_h.value()),
            "bleed": self.book_bleed.value(),
            "gap": self.book_gap.value(),
            "row_gap": self.book_gap.value() if layout == "4up" else 0,
            "layout": layout,
            "grayscale": self.book_gray.isChecked(),
            "brightness": self.book_brightness.value(),
            "dpi": 300,
            "cut_marks": self.book_marks.isChecked(),
            "cut_labels": self.book_labels.isChecked(),
            "cut_mark_style": "in_bleed" if self.book_in_bleed.isChecked() else "outside",
            "sources": sources_of([j for j in self.jobs if not j.get("blank")]),
            "rotations": dict(self.rotations),
            "fractions": dict(self.fractions),
            "blank_colors": {
                j["key"]: tuple(j.get("color") or (255, 255, 255))
                for j in self.jobs if j.get("blank")
            },
        }

    def _booklet_keys(self):
        # Keep blank job keys so blank_colors can paint them; auto-pad still uses None.
        return [job["key"] for job in self.jobs]

    def _booklet_fit(self):
        spec_layout = getattr(self, "book_layout", "2up")
        return booklet_engine.fits(
            self._booklet_paper_mm(),
            (self.book_page_w.value(), self.book_page_h.value()),
            self.book_bleed.value(),
            self.book_gap.value(),
            spec_layout,
            self.book_gap.value() if spec_layout == "4up" else 0,
        )

    def _layout_fit(self):
        paper = self._paper_mm()
        return layout_engine.layout_fits(
            paper, (self.item_w.value(), self.item_h.value()),
            self.cols.value(), self.rows.value(), self.spacing.value(), self.lay_bleed.value(),
        )

    def _update_fit_ui(self, fits, gw, gh, pw, ph):
        if fits:
            self.shell.hide_fit_banner()
        else:
            self.shell.show_fit_banner(
                f"Does not fit: {gw:.1f} × {gh:.1f} mm on {pw:.0f} × {ph:.0f} mm",
                self._fit_to_paper,
            )

    def _fit_to_paper(self):
        paper = self._paper_mm()
        result = layout_engine.best_layout(
            paper, (self.item_w.value(), self.item_h.value()),
            self.spacing.value(), self.lay_bleed.value(), allow_rotate=True,
        )
        if result is None:
            self._set_note("Could not fit this item size on the selected paper.")
            self.shell.report("Could not fit this item size on the selected paper.")
            return
        cols, rows, gap, bleed, iw, ih = result
        self._loading_layout = True
        self._linking_size = True
        try:
            self.cols.setValue(cols)
            self.rows.setValue(rows)
            self.spacing.setValue(gap)
            self.lay_bleed.setValue(bleed)
            if abs(iw - self.item_w.value()) > 1e-6 or abs(ih - self.item_h.value()) > 1e-6:
                self.item_preset.blockSignals(True)
                self.item_preset.setCurrentText("Custom")
                self.item_preset.blockSignals(False)
                self.item_w.setValue(iw)
                self.item_h.setValue(ih)
        finally:
            self._linking_size = False
            self._loading_layout = False
        self.shell.hide_fit_banner()
        self.shell.report(f"Fitted to {cols}×{rows} · item {iw:.1f}×{ih:.1f} mm")
        self._reorient_sources()
        self._schedule()

    def _preview(self):
        mode = self.mode.currentIndex()
        if mode == self._CUT:
            self._preview_cut()
            return
        if not self.jobs:
            self.sheet_count.setText("0 sheets")
            self.board.set_sheets([])
            self.polaroid_preview.setText("Add photos to preview the first sheet")
            self.polaroid_preview.setPixmap(QPixmap())
            self.collage.set_sheet(QImage(), (1, 1), [], [], self.crops)
            self._set_note("")
            if mode == self._LAYOUT:
                fits, gw, gh, pw, ph = self._layout_fit()
                self._update_fit_ui(fits, gw, gh, pw, ph)
            elif mode == self._BOOKLET:
                fits, gw, gh, pw, ph = self._booklet_fit()
                self._update_fit_ui(fits, gw, gh, pw, ph)
            return
        jobs = list(self.jobs)
        if mode == self._LAYOUT:
            fits, gw, gh, pw, ph = self._layout_fit()
            self._update_fit_ui(fits, gw, gh, pw, ph)
            if not fits:
                self.sheet_count.setText("Layout does not fit")
                self._set_note(
                    f"The current grid is larger than the selected paper ({gw:.1f} × {gh:.1f} mm)."
                )
            else:
                self._set_note("")
            self._refresh_source_cards()
            spec = self._spec()

            def work():
                specs = layout_engine.page_specs(
                    [job["key"] for job in jobs], spec["cols"], spec["rows"], spec["mode"], spec["duplex"], spec["back"],
                )
                spec["sources"] = sources_of(jobs)
                sheets = []
                for assignments, back, label in specs:
                    sheet = layout_engine.render_sheet(spec, assignments, back, preview=True)
                    sheets.append((label, pixmap_bytes(sheet)))
                return sheets

            self._run_preview(work)
        elif mode == self._BOOKLET:
            fits, gw, gh, pw, ph = self._booklet_fit()
            self._update_fit_ui(fits, gw, gh, pw, ph)
            if not fits and self.book_preview_mode == "sheets":
                self.sheet_count.setText("Booklet does not fit")
                self._set_note(
                    f"Pages ({gw:.1f} × {gh:.1f} mm) do not fit the sheet ({pw:.0f} × {ph:.0f} mm)."
                )
            else:
                self._set_note("")
            spec = self._booklet_spec()
            keys = self._booklet_keys()
            preview_mode = self.book_preview_mode

            def work_book():
                if preview_mode == "book":
                    return booklet_engine.preview_book(spec, keys)
                return booklet_engine.preview_sheets(spec, keys)

            self._run_preview(work_book)
        elif mode == self._POLAROID:
            frame, layout = self._polaroid_layout(jobs)
            if not frame:
                return

            def work_ok():
                sheet, used = _polaroid_preview(jobs, layout, frame, self.pol_marks.currentText(), self.rotations, self.fractions)
                pages = max(1, -(-len(jobs) // max(1, layout["capacity"])))
                return f"{used} on the first sheet • {pages} sheet(s)", pixmap_bytes(sheet)

            self._run_preview(work_ok)
        else:
            paper = paper_mm(self.col_paper.currentText(), self.col_orient.currentText())
            aspects = [_aspect(job) for job in jobs]
            items = collage_engine.placements(
                len(jobs), aspects, paper, self.col_mode.currentText(),
                self.gap.value(), self.margin.value(), (self.photo_w.value(), self.photo_h.value()),
            )
            self._placements = items

            def work():
                sheet = collage_engine.render_sheet(
                    [job["key"] for job in jobs], paper, items, 48, (255, 255, 255),
                    self.crops, True, sources_of(jobs),
                )
                return paper, items, [job["key"] for job in jobs], pixmap_bytes(sheet)

            self._run_preview(work)

    def _polaroid_layout(self, jobs):
        preset = polaroid_engine.PRESETS[self.pol_preset.currentText()]
        ratio_name = self.pol_ratio.currentText()
        if ratio_name == "Original" and jobs:
            ratio = _aspect(jobs[0])
        else:
            ratio = polaroid_engine.RATIOS.get(ratio_name, 1.0)
        frame = polaroid_engine.frame_values(*preset[:5], ratio)
        paper = paper_mm(self.pol_paper.currentText(), "Portrait")
        layout = polaroid_engine.choose_layout(
            paper, frame, self.pol_space.value(), self.pol_auto.isChecked(),
            self.pol_cols.value(), self.pol_rows.value(),
        )
        return frame, layout

    def _preview_cut(self):
        if not self.jobs:
            self.cut_preview.setText("Drop artwork for a sticker outline")
            return
        job = self.jobs[0]
        settings = self._cut_settings()

        def work():
            image, dpi, page_points, info = cut_engine.load_analysis(job["path"])
            pixel_width = cut_engine.source_pixel_width(job["path"], settings["dpi"] or dpi, page_points, image.width)
            contours = cut_engine.analyse(
                image, settings["tolerance"], settings["min_area"], settings["offset"],
                settings["holes"], settings["dpi"] or dpi, pixel_width, settings["detail"],
            )
            return {
                "image": image, "dpi": dpi, "page_points": page_points, "info": info,
                "contours": contours, "path": job["path"],
            }

        def done(state):
            self.cut_state = state
            self._draw_cut(state)
            count = len(state["contours"])
            self._set_note(f"{state['info']} • {count} path{'s' if count != 1 else ''}")

        self._signals.append(run_bg(work, done, lambda err: self._set_note(err), self.canvas))

    def _draw_cut(self, state):
        image = state["image"]
        pix = pixmap_from_pil(image)
        painter_image = pix.toImage()
        # Draw contours in red on a copy.
        from PySide6.QtGui import QPainter, QPen, QColor
        frame = painter_image.copy()
        painter = QPainter(frame)
        painter.setPen(QPen(QColor("#FF0000"), 2))
        for contour in state["contours"]:
            from PySide6.QtGui import QPolygon
            from PySide6.QtCore import QPoint
            painter.drawPolygon(QPolygon([QPoint(int(x), int(y)) for x, y in contour]))
        painter.end()
        self.cut_preview.setPixmap(QPixmap.fromImage(frame).scaled(
            max(320, self.cut_preview.width() - 8), max(320, self.cut_preview.height() - 8),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        ))

    def _cut_settings(self):
        return {
            "tolerance": self.tolerance.value(),
            "min_area": self.min_area.value(),
            "offset": self.offset.value(),
            "holes": self.holes.isChecked(),
            "dpi": self.cut_dpi.value(),
            "detail": self.detail.value(),
            "line": self.line_mm.value(),
            "artwork": self.cut_artwork.isChecked(),
            "format": self.cut_format.currentText(),
        }

    def _run_preview(self, work):
        mode = self.mode.currentIndex()

        def done(result):
            if not result:
                self.sheet_count.setText("0 sheets")
                self.board.set_sheets([])
                return
            if mode in (self._LAYOUT, self._BOOKLET):
                sheets = []
                for label, data in result:
                    image = QImage()
                    image.loadFromData(data)
                    sheets.append((label, image))
                count = len(sheets)
                if mode == self._BOOKLET:
                    self.board.set_layout_mode(self.book_preview_mode)
                    if self.book_preview_mode == "book":
                        self.sheet_count.setText(f"{count} spread{'s' if count != 1 else ''}")
                    else:
                        physical = max(1, (count + 1) // 2)
                        self.sheet_count.setText(
                            f"{physical} sheet{'s' if physical != 1 else ''} · {count} side{'s' if count != 1 else ''}"
                        )
                else:
                    self.board.set_layout_mode("sheets")
                    self.sheet_count.setText(f"{count} sheet{'s' if count != 1 else ''}")
                self.board.zoom = self.sheet_zoom
                self.board.set_sheets(sheets)
                self._set_note("")
                return
            if mode == self._COLLAGE and len(result) == 4:
                paper, items, keys, data = result
                image = QImage()
                image.loadFromData(data)
                self.collage.set_sheet(image, paper, items, keys, self.crops)
                self._set_note("")
                return
            text, data = result
            image = QImage()
            image.loadFromData(data)
            self.polaroid_preview.setPixmap(QPixmap.fromImage(image).scaled(
                max(200, self.polaroid_preview.width() - 12),
                max(200, self.polaroid_preview.height() - 12),
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            ))
            self._set_note(text or "")

        self._signals.append(run_bg(work, done, lambda err: self._set_note(err), self.canvas))

    def _select_text(self, combo, text):
        text = str(text)
        index = combo.findText(text)
        if index < 0 and text:
            combo.addItem(text)
            index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _layout_dict(self):
        bleed = self.lay_bleed.value()
        return {
            "paper": self.lay_paper.currentText(),
            "orientation": self.lay_orient.currentText(),
            "item_w": self.item_w.value(),
            "item_h": self.item_h.value(),
            "cols": self.cols.value(),
            "rows": self.rows.value(),
            "mode": self.lay_mode.currentText(),
            "duplex": self.lay_mode.currentText() == "Cut & stack",
            "bleed": bleed > 0,
            "bleed_mm": bleed,
            "spacing": self.spacing.value(),
            "grayscale": self.lay_gray.isChecked(),
            "brightness": self.brightness.value(),
            "dpi": 300,
            "paper_w": self.paper_w.value(),
            "paper_h": self.paper_h.value(),
            "cut_marks": self.cut_marks.isChecked(),
            "cut_labels": self.cut_labels.isChecked(),
            "cut_mark_style": "in_bleed" if self.cut_in_bleed.isChecked() else "outside",
            "proportional": self.chain_btn.isChecked(),
            "proportion_ratio": self._proportion_ratio,
        }

    def _load_print_layout(self, name):
        data = self.print_layouts.get(name)
        if not data:
            return
        self._loading_layout = True
        try:
            self.paper_w.setValue(float(data.get("paper_w", 210)))
            self.paper_h.setValue(float(data.get("paper_h", 297)))
            self._select_text(self.lay_paper, data.get("paper", "A4"))
            self._sync_custom_paper()
            self._select_text(self.lay_orient, data.get("orientation", "Portrait"))
            self.item_preset.blockSignals(True)
            self.item_preset.setCurrentIndex(0)
            self.item_preset.blockSignals(False)
            self.item_w.setValue(float(data.get("item_w", 90)))
            self.item_h.setValue(float(data.get("item_h", 50)))
            self.cols.setValue(int(data.get("cols", 2)))
            self.rows.setValue(int(data.get("rows", 5)))
            self._select_text(self.lay_mode, data.get("mode", "Repeat"))
            bleed = 0.0 if data.get("bleed") is False else float(data.get("bleed_mm", 0) or 0)
            self.lay_bleed.setValue(bleed)
            self.spacing.setValue(float(data.get("spacing", 0) or 0))
            self.lay_gray.setChecked(bool(data.get("grayscale", False)))
            self.brightness.setValue(int(data.get("brightness", 100)))
            self.cut_marks.setChecked(bool(data.get("cut_marks", False)))
            self.cut_labels.setChecked(bool(data.get("cut_labels", False)))
            self.cut_in_bleed.setChecked(data.get("cut_mark_style") == "in_bleed")
            self._sync_marks()
            self._proportion_ratio = data.get("proportion_ratio")
            self.chain_btn.blockSignals(True)
            self.chain_btn.setChecked(bool(data.get("proportional", False)))
            self.chain_btn.blockSignals(False)
            if self.chain_btn.isChecked() and not self._proportion_ratio:
                self._proportion_ratio = self.item_w.value() / max(0.1, self.item_h.value())
        finally:
            self._loading_layout = False
        if hasattr(self, "board"):
            self._reorient_sources()
            self._schedule()

    def _save_print_layout(self):
        name, ok = QInputDialog.getText(
            self.canvas, "Save layout", "Layout name:", text=self.layout_preset.currentText(),
        )
        if not ok or not str(name).strip():
            return
        name = str(name).strip()
        self.print_layouts[name] = self._layout_dict()
        ct.save_print_layouts(self.print_layouts)
        self.layout_preset.blockSignals(True)
        self.layout_preset.clear()
        self.layout_preset.addItems(list(self.print_layouts))
        self.layout_preset.setCurrentText(name)
        self.layout_preset.blockSignals(False)
        self.shell.report(f"Saved layout: {name}")

    def _text_focus(self):
        focus = QApplication.focusWidget()
        return isinstance(focus, (QLineEdit, QAbstractSpinBox))

    def _ctrl_p(self):
        if self._text_focus():
            return
        if self.shell._current != "impose" or self.mode.currentIndex() not in (self._LAYOUT, self._BOOKLET):
            return
        self._open_preview()

    def _open_preview(self):
        mode = self.mode.currentIndex()
        if mode not in (self._LAYOUT, self._BOOKLET):
            return
        if not self.jobs:
            QMessageBox.warning(self.canvas, "Nothing to preview", "Add images first.")
            return
        if mode == self._LAYOUT:
            fits, gw, gh, pw, ph = self._layout_fit()
            self._update_fit_ui(fits, gw, gh, pw, ph)
            if not fits:
                QMessageBox.warning(
                    self.canvas, "Layout does not fit",
                    f"The centered grid ({gw:.1f} × {gh:.1f} mm) is larger than the paper ({pw:.0f} × {ph:.0f} mm).",
                )
                return
            dest = os.path.join(tempfile.gettempdir(), f"CopyPro_Print_{int(time.time())}.pdf")
            spec = self._spec()
            jobs = list(self.jobs)
            self.shell.report("Preparing preview…")

            def work():
                spec["sources"] = sources_of(jobs)
                return layout_engine.write_pdf(spec, [job["key"] for job in jobs], dest)
        else:
            fits, gw, gh, pw, ph = self._booklet_fit()
            self._update_fit_ui(fits, gw, gh, pw, ph)
            if not fits:
                QMessageBox.warning(
                    self.canvas, "Booklet does not fit",
                    f"Two pages ({gw:.1f} × {gh:.1f} mm) do not fit the sheet ({pw:.0f} × {ph:.0f} mm).",
                )
                return
            dest = os.path.join(tempfile.gettempdir(), f"CopyPro_Booklet_{int(time.time())}.pdf")
            spec = self._booklet_spec()
            keys = self._booklet_keys()
            self.shell.report("Preparing booklet preview…")

            def work():
                return booklet_engine.write_pdf(spec, keys, dest)

        def done(_count):
            if open_in_acrobat(dest):
                self.shell.report("Opened in Acrobat")
            else:
                self.shell.report("Adobe Acrobat is not installed.")

        self._signals.append(run_bg(work, done, lambda err: self.shell.report(str(err)), self.canvas))

    def _export(self):
        mode = self.mode.currentIndex()
        if mode == self._CUT:
            self._export_cut()
            return
        if not self.jobs:
            QMessageBox.warning(self.canvas, "Nothing to export", "Add images first.")
            return
        if mode == self._LAYOUT:
            fits, gw, gh, pw, ph = self._layout_fit()
            self._update_fit_ui(fits, gw, gh, pw, ph)
            if not fits:
                QMessageBox.warning(
                    self.canvas, "Layout does not fit",
                    f"The centered grid ({gw:.1f} × {gh:.1f} mm) is larger than the paper ({pw:.0f} × {ph:.0f} mm).",
                )
                return
        if mode == self._BOOKLET:
            fits, gw, gh, pw, ph = self._booklet_fit()
            self._update_fit_ui(fits, gw, gh, pw, ph)
            if not fits:
                QMessageBox.warning(
                    self.canvas, "Booklet does not fit",
                    f"Two pages ({gw:.1f} × {gh:.1f} mm) do not fit the sheet ({pw:.0f} × {ph:.0f} mm).",
                )
                return
        dest = choose_save(self.canvas, "Export", "booklet.pdf" if mode == self._BOOKLET else "sheet.pdf", "PDF (*.pdf)")
        if not dest:
            return
        self.export_btn.setEnabled(False)
        jobs = list(self.jobs)

        def work():
            if mode == self._LAYOUT:
                spec = self._spec()
                spec["sources"] = sources_of(jobs)
                return layout_engine.write_pdf(spec, [job["key"] for job in jobs], dest)
            if mode == self._BOOKLET:
                return booklet_engine.write_pdf(self._booklet_spec(), self._booklet_keys(), dest)
            if mode == self._POLAROID:
                frame, layout = self._polaroid_layout(jobs)
                return polaroid_engine.write_pdf(
                    [job["key"] for job in jobs], dest, layout, frame,
                    self.pol_marks.currentText(), self.rotations, _path_sources(jobs), 300, self.fractions,
                )
            paper = paper_mm(self.col_paper.currentText(), self.col_orient.currentText())
            aspects = [_aspect(job) for job in jobs]
            items = collage_engine.placements(
                len(jobs), aspects, paper, self.col_mode.currentText(),
                self.gap.value(), self.margin.value(), (self.photo_w.value(), self.photo_h.value()),
            )
            collage_engine.write_pdf(
                [job["key"] for job in jobs], dest, paper, items, int(self.col_dpi.currentText()),
                (255, 255, 255), self.crops, sources_of(jobs),
            )
            return 1

        def done(count):
            self.export_btn.setEnabled(True)
            self.shell.report(f"Exported {count} page(s)")
            QMessageBox.information(self.canvas, "Done", f"Saved\n{dest}")

        self._signals.append(run_bg(work, done, self._failed, self.canvas))

    def _export_jpeg(self):
        if not self.jobs:
            return
        dest = choose_save(self.canvas, "Export JPEG", "collage.jpg", "JPEG (*.jpg)")
        if not dest:
            return
        jobs = list(self.jobs)
        paper = paper_mm(self.col_paper.currentText(), self.col_orient.currentText())
        aspects = [_aspect(job) for job in jobs]
        items = collage_engine.placements(
            len(jobs), aspects, paper, self.col_mode.currentText(),
            self.gap.value(), self.margin.value(), (self.photo_w.value(), self.photo_h.value()),
        )

        def work():
            collage_engine.write_jpeg(
                [job["key"] for job in jobs], dest, paper, items, int(self.col_dpi.currentText()),
                (255, 255, 255), self.crops, 90, sources_of(jobs),
            )

        self._signals.append(run_bg(work, lambda _r: QMessageBox.information(self.canvas, "Done", dest), self._failed, self.canvas))

    def _export_cut(self):
        state = self.cut_state
        if not state or not state.get("contours"):
            QMessageBox.warning(self.canvas, "Nothing to export", "No vector outline has been detected.")
            return
        settings = self._cut_settings()
        ext = ".svg" if settings["format"] == "SVG" else ".pdf"
        dest = choose_save(self.canvas, "Export cut line", "cutline" + ext, f"{settings['format']} (*{ext})")
        if not dest:
            return
        width_mm, height_mm = cut_engine.source_size_mm(
            state["path"], settings["dpi"] or state["dpi"], state["page_points"], state["image"].size,
        )
        if settings["format"] == "SVG":
            cut_engine.export_svg(
                state["path"], dest, state["contours"], state["image"].size,
                width_mm, height_mm, settings["line"], settings["artwork"], settings["dpi"] or state["dpi"],
            )
        else:
            cut_engine.export_pdf(
                state["path"], dest, state["contours"], state["image"].size,
                width_mm, height_mm, settings["line"], settings["artwork"],
            )
        QMessageBox.information(self.canvas, "Export complete", f"Saved\n{dest}")

    def _failed(self, err):
        self.export_btn.setEnabled(True)
        QMessageBox.critical(self.canvas, "Export failed", err)


def _aspect(job):
    if job["page"] is not None:
        doc = ct.fitz.open(job["path"])
        try:
            rect = doc[job["page"]].rect
            return max(0.05, rect.width / max(1e-6, rect.height))
        finally:
            doc.close()
    with __import__("PIL").Image.open(job["path"]) as image:
        return ct.collage_photo_aspect_from_image(image)


def _path_sources(jobs):
    """Point every job key at its filesystem path (and PDF page when applicable)."""
    return sources_of(jobs)


def imaging_thumb(job):
    from copypro_qt import imaging
    return imaging.thumbnail_png(job["path"], 360, job["page"])


def oriented_thumb(job, item_w_mm, item_h_mm, rotation=0, max_dim=360, auto_orient=True):
    """EXIF + stored rotation (+ optional auto-match item aspect). Returns (key, png, effective_rotation)."""
    import io
    from PIL import Image, ImageOps

    rotation = int(rotation or 0) % 360
    if job.get("page") is not None:
        image = ct.open_pdf_page(job["path"], job["page"], dpi=72).convert("RGB")
    else:
        with Image.open(job["path"]) as opened:
            corrected = ImageOps.exif_transpose(opened)
            image = corrected.convert("RGB")
            if image is corrected:
                image = corrected.copy()
            if corrected is not opened:
                corrected.close()
    if rotation:
        turned = image.rotate(rotation, expand=True)
        if turned is not image:
            image.close()
        image = turned
    if auto_orient:
        target_portrait = float(item_h_mm) >= float(item_w_mm)
        img_portrait = image.height >= image.width
        if target_portrait != img_portrait:
            turned = image.rotate(90, expand=True)
            if turned is not image:
                image.close()
            image = turned
            rotation = (rotation + 90) % 360
    image.thumbnail((max_dim, max_dim), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    image.close()
    return job["key"], buf.getvalue(), rotation


def _polaroid_preview(jobs, layout, frame, marks, rotations, fractions=None):
    return polaroid_engine.render_sheet(
        [job["key"] for job in jobs], 0, layout, frame, 45, marks, rotations, _path_sources(jobs), True, fractions,
    )


def pixmap_bytes(image):
    import io
    buf = io.BytesIO()
    try:
        image.save(buf, "PNG")
        return buf.getvalue()
    finally:
        image.close()
        buf.close()
