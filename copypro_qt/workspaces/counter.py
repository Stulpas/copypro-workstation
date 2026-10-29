"""Front-desk print counter. Labels stay in the same language as the current tool."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from copypro_qt.engines import pricing
from copypro_qt.legacy import ct
from copypro_qt.widgets import button, muted, section


class _FocusEdit(QLineEdit):
    focused = Signal()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit()


class CounterWorkspace:
    full_width = True

    def __init__(self, shell):
        self.shell = shell
        self.rows = []
        self.line_discounts = {}
        self.global_discounts = []
        self.active_code = None
        self.active_row = None
        self.inspector = QWidget()
        self.canvas = QWidget()
        self._build()
        QShortcut(QKeySequence("F6"), self.canvas, self._discount_line)
        QShortcut(QKeySequence("F7"), self.canvas, self._discount_all)

    def _build(self):
        root = QVBoxLayout(self.canvas)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        tools = QHBoxLayout(bar)
        tools.setContentsMargins(8, 8, 8, 8)
        for text, slot, role in (
            ("Add row", self._add_row, "primary"),
            ("Clear all", self._clear, "secondary"),
            ("Copy summary", self._copy, "secondary"),
            ("F6  Line discount", self._discount_line, "secondary"),
            ("F7  List discount", self._discount_all, "secondary"),
        ):
            item = button(text, role)
            item.clicked.connect(slot)
            tools.addWidget(item)
        tools.addStretch(1)
        hint = muted("Enter amount*code, for example 8*710 — Enter adds the next row")
        tools.addWidget(hint)
        wide = button("Wide format pricing", "success")
        wide.clicked.connect(self._wide)
        tools.addWidget(wide)
        root.addWidget(bar)

        body = QHBoxLayout()
        left = QWidget()
        left.setFixedWidth(480)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        search_card = QFrame()
        search_card.setObjectName("card")
        search_card.setAttribute(Qt.WA_StyledBackground, True)
        search_box = QVBoxLayout(search_card)
        search_box.addWidget(section("Item search"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Ieškoti kodo ar pavadinimo")
        self.search.textChanged.connect(self._search)
        self.search.returnPressed.connect(self._use_first_result)
        search_box.addWidget(self.search)
        self.results = QListWidget()
        self.results.setMaximumHeight(150)
        self.results.itemActivated.connect(self._insert_result)
        search_box.addWidget(self.results)
        left_layout.addWidget(search_card)
        left_layout.addWidget(section("Amount × code"))
        self.row_scroll = QScrollArea()
        self.row_scroll.setWidgetResizable(True)
        self.row_scroll.setFrameShape(QFrame.NoFrame)
        self.row_host = QWidget()
        self.row_layout = QVBoxLayout(self.row_host)
        self.row_layout.setAlignment(Qt.AlignTop)
        self.row_scroll.setWidget(self.row_host)
        left_layout.addWidget(self.row_scroll, 1)
        body.addWidget(left)

        summary = QFrame()
        summary.setObjectName("card")
        summary.setAttribute(Qt.WA_StyledBackground, True)
        summary_layout = QVBoxLayout(summary)
        summary_layout.addWidget(section("Summary"))
        summary_layout.addWidget(muted("Codes auto-resolved by total quantity"))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Code", "Qty", "PLU", "Description", "Discount", "Total €"])
        self.table.cellClicked.connect(self._pick_row)
        summary_layout.addWidget(self.table, 1)
        self.total = QLabel("€0.00")
        self.total.setStyleSheet("font-size: 28px; font-weight: 700; color: #22C55E;")
        summary_layout.addWidget(self.total)
        body.addWidget(summary, 1)
        root.addLayout(body, 1)
        self._add_row()

    def on_show(self):
        try:
            ct.reload_editable_data()
        except Exception as exc:
            self.shell.report(str(exc))
        self._refresh()

    def on_tray(self):
        return

    def add_paths(self, _paths):
        return

    def _search(self, text):
        self.results.clear()
        try:
            found = pricing.search_catalog(text)
        except Exception:
            return
        for _score, code, description, price in found:
            price_text = "" if price is None else f"  €{price:.2f}"
            self.results.addItem(f"{code}  {description}{price_text}")

    def _use_first_result(self):
        if self.results.count():
            self._insert_result(self.results.item(0))

    def _target_edit(self):
        if self.active_row is not None:
            return self.active_row
        if self.rows:
            return self.rows[-1]["edit"]
        return self._add_row()

    def _insert_result(self, item):
        code = item.text().split()[0]
        edit = self._target_edit()
        current = edit.text().strip()
        if "*" in current:
            amount = current.split("*", 1)[0]
            edit.setText(f"{amount}*{code}")
        else:
            edit.setText(code)
        edit.setFocus()

    def _add_row(self, value=""):
        wrap = QFrame()
        wrap.setObjectName("card")
        wrap.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(wrap)
        row.setContentsMargins(6, 4, 6, 4)
        edit = _FocusEdit(value)
        edit.setPlaceholderText("8*710")
        edit.returnPressed.connect(lambda widget=edit: self._enter(widget))
        edit.textChanged.connect(lambda _text: self._refresh())
        edit.focused.connect(lambda widget=edit: self._focus_row(widget))
        delete = button("✕", "ghost")
        delete.clicked.connect(lambda _checked=False, frame=wrap: self._delete_row(frame))
        row.addWidget(edit, 1)
        row.addWidget(delete)
        self.row_layout.addWidget(wrap)
        record = {"frame": wrap, "edit": edit}
        self.rows.append(record)
        edit.setFocus()
        self.active_row = edit
        self._refresh()
        return edit

    def _focus_row(self, edit):
        self.active_row = edit

    def _enter(self, edit):
        index = next(i for i, record in enumerate(self.rows) if record["edit"] is edit)
        if index == len(self.rows) - 1:
            self._add_row()
        else:
            self.rows[index + 1]["edit"].setFocus()

    def _delete_row(self, frame):
        self.rows = [record for record in self.rows if record["frame"] is not frame]
        frame.setParent(None)
        frame.deleteLater()
        if not self.rows:
            self._add_row()
        else:
            self._refresh()

    def _entries(self):
        return [record["edit"].text().strip() for record in self.rows if record["edit"].text().strip()]

    def _refresh(self):
        summary = pricing.price_lines(self._entries(), self.line_discounts, self.global_discounts)
        self.table.setRowCount(len(summary["rows"]))
        for row, item in enumerate(summary["rows"]):
            values = [
                item["code"], _fmt(item["qty"]), item["output"], item["description"],
                item["discount"], "—" if item["total"] is None else f"{item['total']:.2f}",
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.total.setText(f"€{summary['grand']:.2f}")
        if summary["missing"]:
            self.shell.report(f"{summary['missing']} line(s) have no stored price")

    def _pick_row(self, row, _col):
        item = self.table.item(row, 0)
        self.active_code = item.text() if item else None

    def _discount_line(self):
        if not self.active_code:
            QMessageBox.information(self.canvas, "Select a line", "Click a line in the summary first, then press F6.")
            return
        result = _ask_discount(self.canvas, f"Discount line {self.active_code}")
        if result:
            self.line_discounts[self.active_code] = (result[0], result[1])
            self._refresh()

    def _discount_all(self):
        if not self._entries():
            return
        result = _ask_discount(self.canvas, "Discount whole list", stack=True)
        if not result:
            return
        discount = (result[0], result[1])
        if result[2] == "replace":
            self.line_discounts.clear()
            self.global_discounts = [discount]
        else:
            self.global_discounts.append(discount)
        self._refresh()

    def _clear(self):
        for record in list(self.rows):
            record["frame"].setParent(None)
            record["frame"].deleteLater()
        self.rows.clear()
        self.line_discounts.clear()
        self.global_discounts.clear()
        self.active_code = None
        self._add_row()

    def _copy(self):
        summary = pricing.price_lines(self._entries(), self.line_discounts, self.global_discounts)
        if not summary["rows"]:
            return
        lines = ["Input code\tQty\tOutput PLU\tDescription\tDiscount\tFinal EUR"]
        for item in summary["rows"]:
            total = "—" if item["total"] is None else f"{item['total']:.2f}"
            lines.append(f"{item['code']}\t{_fmt(item['qty'])}\t{item['output']}\t{item['description']}\t{item['discount']}\t{total}")
        lines.append(f"GRAND TOTAL\t\t\t\t\t{summary['grand']:.2f} EUR")
        self.canvas.clipboard = None
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(lines))
        QMessageBox.information(self.canvas, "Copied", "Priced summary copied to clipboard.")

    def _wide(self):
        dialog = QDialog(self.canvas)
        dialog.setWindowTitle("Wide format quote")
        dialog.resize(640, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(section("Wide format price list"))
        row = QHBoxLayout()
        amount = QDoubleSpinBox()
        amount.setRange(0.01, 10000)
        amount.setValue(4.3)
        coverage = QComboBox()
        coverage.addItem("Not full — tekstas+pav.", "partial")
        coverage.addItem("Full — paveikslas", "full")
        row.addWidget(amount)
        row.addWidget(coverage)
        layout.addLayout(row)
        text = QTextEdit()
        text.setReadOnly(True)
        layout.addWidget(text)

        def refresh():
            try:
                lines = pricing.wide_format_lines(amount.value(), coverage.currentData())
            except Exception as exc:
                lines = [str(exc)]
            text.setPlainText("\n".join(lines))

        amount.valueChanged.connect(lambda _v: refresh())
        coverage.currentIndexChanged.connect(lambda _i: refresh())
        copy = button("Copy price list", "success")

        def copy_lines():
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text.toPlainText())

        copy.clicked.connect(copy_lines)
        layout.addWidget(copy)
        refresh()
        dialog.exec()


def _fmt(value):
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _ask_discount(parent, title, stack=False):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(title))
    kind = QComboBox()
    kind.addItem("Percent %", "percent")
    kind.addItem("Fixed EUR", "eur")
    value = QDoubleSpinBox()
    value.setRange(0, 100000)
    value.setDecimals(2)
    layout.addWidget(kind)
    layout.addWidget(value)
    mode = QComboBox()
    if stack:
        mode.addItem("Replace existing discounts", "replace")
        mode.addItem("Stack with existing discounts", "stack")
        layout.addWidget(mode)
    buttons = QHBoxLayout()
    ok = QPushButton("Apply")
    cancel = QPushButton("Cancel")
    ok.setProperty("role", "success")
    cancel.setProperty("role", "secondary")
    buttons.addWidget(cancel)
    buttons.addWidget(ok)
    layout.addLayout(buttons)
    chosen = {}

    def accept():
        if kind.currentData() == "percent" and value.value() > 100:
            return
        chosen["value"] = (kind.currentData(), value.value(), mode.currentData() if stack else "replace")
        dialog.accept()

    ok.clicked.connect(accept)
    cancel.clicked.connect(dialog.reject)
    if dialog.exec():
        return chosen.get("value")
    return None
