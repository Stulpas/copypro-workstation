"""Shared Qt controls for the workstation."""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from copypro_qt.imaging import fit_box, refit_fractions
from copypro_qt.theme import ACCENT, ACCENT2, TEXT


def section(text):
    label = QLabel(text.upper())
    label.setObjectName("section")
    return label


def muted(text):
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def field_label(text):
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


def polish(widget):
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


_PRIMARY_STYLE = f"""
QPushButton {{
    background: {ACCENT};
    color: {TEXT};
    font-weight: 700;
    border: none;
    border-radius: 8px;
    padding: 7px 12px;
}}
QPushButton:hover {{
    background: {ACCENT2};
    color: {TEXT};
}}
QPushButton:disabled {{
    background: #27272A;
    color: #9CA3AF;
}}
"""


def button(text, role="primary"):
    widget = QPushButton(text)
    widget.setProperty("role", role)
    if role in ("success", "primary"):
        widget.setObjectName("successBtn")
        # Inline stylesheet beats macOS native style so resting state stays accent blue.
        widget.setStyleSheet(_PRIMARY_STYLE)
    widget.setCursor(Qt.PointingHandCursor)
    polish(widget)
    return widget


def labeled(text, widget):
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(1)
    column.addWidget(field_label(text))
    column.addWidget(widget)
    wrap = QWidget()
    wrap.setLayout(column)
    return wrap


def pair(left, right):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(left, 1)
    row.addWidget(right, 1)
    wrap = QWidget()
    wrap.setLayout(row)
    return wrap


def group_card(title, *children, spacing=4):
    """Settings group: surface card with optional title and stacked controls."""
    frame = QFrame()
    frame.setObjectName("groupCard")
    frame.setAttribute(Qt.WA_StyledBackground, True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(10, 8, 10, 8)
    layout.setSpacing(spacing)
    if title:
        heading = QLabel(title)
        heading.setObjectName("groupTitle")
        layout.addWidget(heading)
    for child in children:
        if isinstance(child, QHBoxLayout) or isinstance(child, QVBoxLayout):
            layout.addLayout(child)
        else:
            layout.addWidget(child)
    return frame


class OutputOptions(QWidget):
    def __init__(self, subfolder="output", parent=None):
        super().__init__(parent)
        self._folder = ""
        self._subfolder = subfolder
        self._index = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self.mode = QComboBox()
        self.mode.addItems(["Choose folder…", "Same folder", "Subfolder"])
        self.mode.setMinimumWidth(0)
        root.addWidget(self.mode)
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        self.path = QLineEdit()
        self.path.setPlaceholderText("Output folder")
        self.path.setMinimumWidth(0)
        path_row.addWidget(self.path, 1)
        self.browse = button("Browse", "secondary")
        self.browse.clicked.connect(self._browse)
        path_row.addWidget(self.browse)
        root.addLayout(path_row)
        self.overwrite = QCheckBox("Replace originals")
        root.addWidget(self.overwrite)
        self.mode.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self, _index=None):
        previous = self._index
        index = self.mode.currentIndex()
        text = self.path.text().strip()
        if previous == 0:
            self._folder = text
        elif previous == 2 and text:
            self._subfolder = text
        self._index = index
        self.path.setVisible(index != 1)
        self.browse.setVisible(index == 0)
        self.overwrite.setVisible(index == 1)
        if index == 0:
            self.path.setText(self._folder)
            self.path.setPlaceholderText("Output folder")
        elif index == 2:
            self.path.setText(self._subfolder)
            self.path.setPlaceholderText("Subfolder name")

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Output folder")
        if folder:
            self._folder = folder
            self.path.setText(folder)

    def settings(self):
        index = self.mode.currentIndex()
        mode = ("choose", "same", "subfolder")[index]
        text = self.path.text().strip()
        return {
            "mode": mode,
            "folder": text if mode == "choose" else self._folder,
            "subfolder": (text if mode == "subfolder" else self._subfolder) or "output",
            "overwrite": self.overwrite.isChecked() and mode == "same",
        }


def ingest_paths(paths, existing):
    """Add files, walking folders, without duplicates."""
    added = []
    seen = set(existing) | set(added)
    for path in paths:
        path = os.path.abspath(path)
        if os.path.isdir(path):
            for dirpath, _dirs, names in os.walk(path):
                for name in sorted(names):
                    full = os.path.join(dirpath, name)
                    if full not in seen and os.path.isfile(full):
                        seen.add(full)
                        added.append(full)
        elif os.path.isfile(path) and path not in seen:
            seen.add(path)
            added.append(path)
    return added


class CropView(QWidget):
    """Aspect-locked crop. Fractions stay resolution-independent."""

    cropChanged = Signal(tuple)
    previewZoom = Signal(int)
    expandRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(64, 48)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.WheelFocus)
        self._image = QImage()
        self._fractions = (0.0, 0.0, 1.0, 1.0)
        self._aspect = 1.0
        self._drag = None

    def set_preview(self, image, aspect, fractions=None):
        previous_aspect = self._aspect
        self._image = image if image is not None else QImage()
        new_aspect = aspect if aspect and aspect > 0 else 1.0
        aspect_changed = abs(previous_aspect - new_aspect) > 1e-6
        self._aspect = new_aspect
        if fractions:
            fr = tuple(fractions)
            if aspect_changed and not self._image.isNull():
                fr = refit_fractions(fr, self._image.width(), self._image.height(), self._aspect)
                self._fractions = fr
                self.cropChanged.emit(fr)
            else:
                self._fractions = fr
        elif not self._image.isNull():
            self._fractions = fit_box(
                self._image.width(), self._image.height(), max(1, int(self._aspect * 1000)), 1000,
            )
        self.update()

    def fractions(self):
        return self._fractions

    def _geometry(self):
        if self._image.isNull():
            return None
        iw, ih = self._image.width(), self._image.height()
        scale = min(self.width() / max(1, iw), self.height() / max(1, ih))
        dw, dh = max(1, int(iw * scale)), max(1, int(ih * scale))
        ox, oy = (self.width() - dw) // 2, (self.height() - dh) // 2
        left, top, right, bottom = self._fractions
        rect = QRect(
            int(ox + left * dw),
            int(oy + top * dh),
            max(8, int((right - left) * dw)),
            max(8, int((bottom - top) * dh)),
        )
        return ox, oy, dw, dh, rect

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#12121A"))
        geo = self._geometry()
        if not geo:
            painter.setPen(QColor("#7878A0"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Drop photos to start")
            painter.end()
            return
        ox, oy, dw, dh, rect = geo
        painter.drawImage(QRect(ox, oy, dw, dh), self._image)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))
        painter.setClipRect(rect)
        painter.drawImage(QRect(ox, oy, dw, dh), self._image)
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#5B6BFF"), 2))
        painter.drawRect(rect)
        painter.setBrush(QColor("#5B6BFF"))
        for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
            painter.drawRect(corner.x() - 4, corner.y() - 4, 8, 8)
        painter.end()

    def _hit(self, pos):
        geo = self._geometry()
        if not geo:
            return None
        _ox, _oy, _dw, _dh, rect = geo
        corners = {
            "nw": rect.topLeft(), "ne": rect.topRight(),
            "sw": rect.bottomLeft(), "se": rect.bottomRight(),
        }
        for name, point in corners.items():
            if (pos - point).manhattanLength() <= 12:
                return name
        if rect.contains(pos):
            return "move"
        return None

    def mousePressEvent(self, event):
        mode = self._hit(event.position().toPoint())
        if not mode:
            return
        geo = self._geometry()
        self._drag = (mode, event.position().toPoint(), self._fractions, geo)

    def mouseMoveEvent(self, event):
        if not self._drag:
            self.setCursor(Qt.SizeAllCursor if self._hit(event.position().toPoint()) else Qt.ArrowCursor)
            return
        mode, start, fractions, geo = self._drag
        ox, oy, dw, dh, _rect = geo
        dx = (event.position().x() - start.x()) / max(1, dw)
        dy = (event.position().y() - start.y()) / max(1, dh)
        left, top, right, bottom = fractions
        if mode == "move":
            width, height = right - left, bottom - top
            left = min(max(0.0, left + dx), 1 - width)
            top = min(max(0.0, top + dy), 1 - height)
            self._fractions = (left, top, left + width, top + height)
        else:
            self._resize(mode, left, top, right, bottom, dx, dy)
        self.cropChanged.emit(self._fractions)
        self.update()

    def _resize(self, mode, left, top, right, bottom, dx, dy):
        aspect = self._aspect
        if mode == "se":
            anchor = (left, top)
            width = max(0.05, (right - left) + dx)
        elif mode == "nw":
            anchor = (right, bottom)
            width = max(0.05, (right - left) - dx)
        elif mode == "ne":
            anchor = (left, bottom)
            width = max(0.05, (right - left) + dx)
        else:
            anchor = (right, top)
            width = max(0.05, (right - left) - dx)
        # Fraction height so the crop's pixel box matches the target aspect.
        height = width * (self._image.width() / max(1.0, self._image.height())) / aspect
        height = max(0.05, min(1.0, height))
        width = max(0.05, min(1.0, width))
        if mode == "se":
            left, top = anchor
            right, bottom = left + width, top + height
        elif mode == "nw":
            right, bottom = anchor
            left, top = right - width, bottom - height
        elif mode == "ne":
            left, bottom = anchor
            right, top = left + width, bottom - height
        else:
            right, top = anchor
            left, bottom = right - width, top + height
        if left < 0:
            right -= left
            left = 0
        if top < 0:
            bottom -= top
            top = 0
        if right > 1:
            left -= right - 1
            right = 1
        if bottom > 1:
            top -= bottom - 1
            bottom = 1
        self._fractions = (max(0, left), max(0, top), min(1, right), min(1, bottom))

    def mouseReleaseEvent(self, _event):
        self._drag = None

    def mouseDoubleClickEvent(self, event):
        if not self._image.isNull():
            self.expandRequested.emit()
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta or self._image.isNull():
            return
        direction = 1 if delta > 0 else -1
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.previewZoom.emit(direction)
        else:
            self._scale_crop(0.90 if direction > 0 else 1 / 0.90)
            self.cropChanged.emit(self._fractions)
            self.update()
        event.accept()

    def _scale_crop(self, factor):
        left, top, right, bottom = self._fractions
        cx, cy = (left + right) / 2, (top + bottom) / 2
        width = max(0.08, min(1.0, (right - left) * factor))
        ratio = self._image.width() / max(1.0, self._image.height())
        height = width * ratio / max(0.05, self._aspect)
        if height > 1:
            height = 1.0
            width = min(1.0, height * self._aspect / ratio)
        if width > 1:
            width = 1.0
            height = min(1.0, width * ratio / max(0.05, self._aspect))
        left = min(max(0.0, cx - width / 2), 1 - width)
        top = min(max(0.0, cy - height / 2), 1 - height)
        self._fractions = (left, top, left + width, top + height)


def pixmap_from_pil(image):
    rgb = image.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888).copy()
    if rgb is not image:
        rgb.close()
    return QPixmap.fromImage(qimage)


def choose_files(parent, title, pattern):
    paths, _selected = QFileDialog.getOpenFileNames(parent, title, "", pattern)
    return paths


def choose_save(parent, title, suggested, pattern):
    path, _selected = QFileDialog.getSaveFileName(parent, title, suggested, pattern)
    return path


def choose_folder(parent, title):
    return QFileDialog.getExistingDirectory(parent, title)
