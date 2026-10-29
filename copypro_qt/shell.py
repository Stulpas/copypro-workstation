"""Main window: icon rail and a settings card beside each tool's own work surface."""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QEasingCurve, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QCursor, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from copypro_qt.legacy import ct
from copypro_qt.theme import STYLESHEET
from copypro_qt.widgets import button, polish, section

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")
_ICON_SIZE = QSize(22, 22)


def _rail_icon(name):
    return QIcon(os.path.join(_ASSETS, f"rail-{name}.svg"))


class _Rail(QFrame):
    """Icon rail that expands on hover. Icon and name are one clickable row."""

    _NARROW = 56
    _WIDE = 168

    def __init__(self, items, on_select, on_settings):
        super().__init__()
        self._buttons = {}
        self._entries = []
        self._expanded = False
        self.setObjectName("rail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(self._NARROW)
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.setInterval(300)
        self._open_timer.timeout.connect(lambda: self._animate(True))
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.setInterval(160)
        self._close_timer.timeout.connect(self._collapse_if_left)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._apply_width)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(4)
        for key, label, icon_name in items:
            item = self._row_button(icon_name, label)
            item.clicked.connect(lambda _checked=False, name=key: on_select(name))
            layout.addWidget(item)
            self._buttons[key] = item
            self._entries.append((item, label))
        layout.addStretch(1)
        gear = self._row_button("settings", "Settings")
        gear.clicked.connect(on_settings)
        layout.addWidget(gear)
        self._entries.append((gear, "Settings"))
        self._sync_labels(False)

    def _row_button(self, icon_name, label):
        item = QPushButton()
        item.setIcon(_rail_icon(icon_name))
        item.setIconSize(_ICON_SIZE)
        item.setProperty("role", "rail")
        item.setProperty("active", "false")
        item.setCursor(Qt.PointingHandCursor)
        item.setToolTip(label)
        item.setMinimumHeight(44)
        return item

    def buttons(self):
        return self._buttons

    def enterEvent(self, _event):
        self._close_timer.stop()
        if not self._expanded:
            self._open_timer.start()

    def leaveEvent(self, _event):
        self._open_timer.stop()
        self._close_timer.start()

    def _collapse_if_left(self):
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self._animate(False)

    def _animate(self, expanded):
        if self._expanded == expanded and self._anim.state() != QVariantAnimation.Running:
            return
        self._expanded = expanded
        self._anim.stop()
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(self._WIDE if expanded else self._NARROW)
        self._anim.start()

    def _sync_labels(self, show_names):
        for button_, label in self._entries:
            button_.setText(f"  {label}" if show_names else "")
            button_.setIconSize(_ICON_SIZE)

    def _apply_width(self, value):
        width = int(value)
        self.setFixedWidth(width)
        self._sync_labels(width > 100)


class Workstation(QMainWindow):
    RAIL = (
        ("photos", "Photos", "photos"),
        ("impose", "Impose", "impose"),
        ("scans", "Scans", "scans"),
        ("documents", "Documents", "documents"),
        ("counter", "Counter", "counter"),
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"CopyPro Tools {ct.current_version()}")
        self.resize(1280, 800)
        self._pages = {}
        self._current = None
        self._rail_buttons = {}
        self._build()
        self.setStyleSheet(STYLESHEET)
        self.show_workspace("photos")
        if ct.DATA_LOAD_ERROR:
            self.report(ct.DATA_LOAD_ERROR)
        QShortcut(QKeySequence.Open, self, self._add_files)
        QTimer.singleShot(1800, lambda: self._check_for_updates(manual=False))

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._rail())

        body = QWidget()
        columns = QVBoxLayout(body)
        columns.setContentsMargins(8, 8, 8, 8)
        columns.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.inspector_host = QWidget()
        self.inspector_host.setFixedWidth(292)
        self.inspector_layout = QVBoxLayout(self.inspector_host)
        self.inspector_layout.setContentsMargins(0, 0, 0, 0)
        self.inspector_layout.setSpacing(0)
        row.addWidget(self.inspector_host)

        self.canvas_host = QWidget()
        self.canvas_host.setObjectName("canvasStage")
        self.canvas_host.setAttribute(Qt.WA_StyledBackground, True)
        self.canvas_layout = QVBoxLayout(self.canvas_host)
        self.canvas_layout.setContentsMargins(8, 8, 8, 8)
        self.canvas_layout.setSpacing(0)
        row.addWidget(self.canvas_host, 1)

        self.source_host = QWidget()
        self.source_host.setObjectName("sourceHost")
        self.source_host.setAttribute(Qt.WA_StyledBackground, True)
        self.source_host.setFixedWidth(228)
        self.source_host.hide()
        self.source_layout = QVBoxLayout(self.source_host)
        self.source_layout.setContentsMargins(8, 8, 8, 8)
        self.source_layout.setSpacing(0)
        row.addWidget(self.source_host)
        columns.addLayout(row, 1)

        self._fit_callback = None
        self.fit_banner = QFrame()
        self.fit_banner.setObjectName("fitBanner")
        self.fit_banner.setAttribute(Qt.WA_StyledBackground, True)
        banner = QHBoxLayout(self.fit_banner)
        banner.setContentsMargins(10, 6, 8, 6)
        banner.setSpacing(8)
        self.fit_banner_msg = QLabel("")
        self.fit_banner_msg.setObjectName("fitBannerMsg")
        self.fit_banner_msg.setWordWrap(True)
        banner.addWidget(self.fit_banner_msg, 1)
        self.fit_banner_btn = button("Fit to paper", "secondary")
        self.fit_banner_btn.clicked.connect(self._run_fit_banner)
        banner.addWidget(self.fit_banner_btn)
        dismiss = button("✕", "ghost")
        dismiss.setFixedWidth(28)
        dismiss.clicked.connect(self.hide_fit_banner)
        banner.addWidget(dismiss)
        self.fit_banner.hide()
        columns.addWidget(self.fit_banner)

        self.footer_host = QWidget()
        footer = QHBoxLayout(self.footer_host)
        footer.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(180)
        self.progress.setFixedHeight(8)
        self.progress.hide()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.progress)
        self.footer_host.hide()
        columns.addWidget(self.footer_host)
        outer.addWidget(body, 1)
        self.setAcceptDrops(True)

    def _rail(self):
        rail = _Rail(self.RAIL, self.show_workspace, self._settings)
        self._rail_buttons = rail.buttons()
        return rail

    def show_workspace(self, key):
        if key not in self._pages:
            self._pages[key] = self._build_page(key)
        page = self._pages[key]
        self._current = key
        self.hide_fit_banner()
        page.inspector.setObjectName("inspector")
        page.inspector.setAttribute(Qt.WA_StyledBackground, True)
        full = getattr(page, "full_width", False)
        self.inspector_host.setVisible(not full)
        while self.inspector_layout.count():
            item = self.inspector_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        if not full:
            self.inspector_layout.addWidget(page.inspector, 1)
        while self.canvas_layout.count():
            item = self.canvas_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self.canvas_layout.addWidget(page.canvas)
        while self.source_layout.count():
            item = self.source_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        source = getattr(page, "source_panel", None)
        if source is not None and not full:
            self.source_layout.addWidget(source, 1)
            self.set_source_visible(bool(getattr(page, "source_visible", False)))
        else:
            self.source_host.hide()
        for name, button_ in self._rail_buttons.items():
            button_.setProperty("active", "true" if name == key else "false")
            polish(button_)
        for btn in page.inspector.findChildren(QPushButton):
            if btn.objectName() == "successBtn":
                polish(btn)
        page.on_show()

    def set_source_visible(self, visible):
        self.source_host.setVisible(bool(visible))
        page = self._pages.get(self._current)
        if page is not None:
            page.source_visible = bool(visible)

    def show_fit_banner(self, message, on_fit):
        self._fit_callback = on_fit
        self.fit_banner_msg.setText(message)
        self.fit_banner.show()

    def hide_fit_banner(self):
        self.fit_banner.hide()
        self._fit_callback = None

    def _run_fit_banner(self):
        callback = self._fit_callback
        if callback is not None:
            callback()

    def _build_page(self, key):
        if key == "photos":
            from copypro_qt.workspaces.photos import PhotosWorkspace
            return PhotosWorkspace(self)
        if key == "impose":
            from copypro_qt.workspaces.impose import ImposeWorkspace
            return ImposeWorkspace(self)
        if key == "scans":
            from copypro_qt.workspaces.scans import ScansWorkspace
            return ScansWorkspace(self)
        if key == "documents":
            from copypro_qt.workspaces.documents import DocumentsWorkspace
            return DocumentsWorkspace(self)
        from copypro_qt.workspaces.counter import CounterWorkspace
        return CounterWorkspace(self)

    def report(self, message, value=None, maximum=None):
        text = "" if not message or message == "Ready" else message
        self.status.setText(text)
        if maximum:
            self.progress.show()
            self.progress.setRange(0, maximum)
            self.progress.setValue(value or 0)
        else:
            self.progress.hide()
        self.footer_host.setVisible(bool(text) or bool(maximum))

    def _add_files(self):
        paths, _selected = QFileDialog.getOpenFileNames(self, "Add files", "", "All files (*.*)")
        if paths:
            self._give_files(paths)

    def _give_files(self, paths):
        page = self._pages.get(self._current)
        if page is not None and hasattr(page, "add_paths"):
            page.add_paths(paths)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._give_files(paths)

    def _settings(self):
        from PySide6.QtWidgets import QDialog, QFormLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("Duomenų failų nustatymai")
        dialog.resize(640, 220)
        layout = QVBoxLayout(dialog)
        layout.addWidget(section("Duomenų failai"))
        paths = ct._editable_paths()
        fields = {}
        form = QFormLayout()
        labels = (("codes_file", "Prekių ir paslaugų kodai"), ("paper_sizes_file", "Popieriaus dydžiai"))
        for key, title in labels:
            row = QHBoxLayout()
            edit = QLineEdit(paths.get(key, ""))
            browse = button("Pasirinkti…", "secondary")

            def pick(target=edit):
                chosen, _sel = QFileDialog.getOpenFileName(dialog, "Pasirinkti Excel failą", "", "Excel (*.xlsx *.xlsm);;CSV (*.csv)")
                if chosen:
                    target.setText(chosen)

            browse.clicked.connect(pick)
            row.addWidget(edit, 1)
            row.addWidget(browse)
            wrap = QWidget()
            wrap.setLayout(row)
            form.addRow(title, wrap)
            fields[key] = edit
        layout.addLayout(form)
        buttons = QHBoxLayout()
        save = button("Išsaugoti", "success")
        buttons.addWidget(save)
        layout.addLayout(buttons)

        def save_reload():
            data = ct.load_app_settings()
            data.update({key: edit.text().strip() for key, edit in fields.items()})
            ct.save_app_settings(data)
            try:
                result = ct.reload_editable_data()
            except Exception as exc:
                QMessageBox.critical(dialog, "Klaida", str(exc))
                return
            QMessageBox.information(
                dialog, "Išsaugota",
                "Duomenys įkelti tik iš šių Excel failų:\n\n"
                f"Kodai: {result['codes_file']}\n"
                f"Popieriaus dydžiai: {result['paper_sizes_file']}\n\n"
                f"Kodų: {result['code_count']}\n"
                f"Plataus formato eilučių: {result['wide_format_count']}\n"
                f"Popieriaus dydžių: {result['paper_size_count']}",
            )
            dialog.accept()

        save.clicked.connect(save_reload)
        open_dir = button("Atidaryti duomenų aplanką", "secondary")

        def reveal():
            folder = ct.COPYPRO_DATA_DIR
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", folder])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])

        open_dir.clicked.connect(reveal)
        buttons.insertWidget(0, open_dir)
        check = button("Check for updates", "secondary")
        check.clicked.connect(lambda: self._check_for_updates(manual=True))
        buttons.insertWidget(1, check)
        ver = QLabel(f"v{ct.current_version()}")
        ver.setObjectName("muted")
        buttons.insertWidget(2, ver)
        dialog.exec()

    def _check_for_updates(self, manual=True):
        from copypro_update_support import check_for_update_async, current_version, launch_updater

        def finished(release, error):
            def show():
                if error:
                    if manual:
                        QMessageBox.critical(
                            self, "Update check failed",
                            f"Could not check for updates.\n\n{error}",
                        )
                    return
                if not release:
                    if manual:
                        QMessageBox.information(
                            self, "Updates",
                            f"You are on the latest version ({current_version()}).",
                        )
                    return
                notes = (release.get("notes") or "").strip()
                message = (
                    f"A new CopyPro Tools version is available.\n\n"
                    f"Current: {current_version()}\n"
                    f"New: {release['version']}"
                )
                if notes:
                    message += "\n\n" + notes[:1000]
                message += "\n\nUpdate now?"
                reply = QMessageBox.question(
                    self, "CopyPro Tools update", message,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
                )
                if reply != QMessageBox.Yes:
                    return
                try:
                    launch_updater(release)
                    QTimer.singleShot(250, self.close)
                except Exception as exc:
                    QMessageBox.critical(self, "Update failed", str(exc))

            QTimer.singleShot(0, show)

        check_for_update_async(finished)
