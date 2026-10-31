"""Dark workstation palette: slate base, blue accents."""

import os

BG = "#18181B"
SURFACE = "#1E1E24"
SURFACE2 = "#27272A"
BORDER = "#3F3F46"
ACCENT = "#3B82F6"
ACCENT2 = "#60A5FA"
SUCCESS = "#3B82F6"
WARNING = "#F59E0B"
DANGER = "#EF4444"
TEXT = "#F3F4F6"
MUTED = "#9CA3AF"
CARD = "#27272A"

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")
_CHEVRON = os.path.join(_ASSETS, "chevron.svg").replace("\\", "/")
_SWITCH_OFF = os.path.join(_ASSETS, "switch-off.svg").replace("\\", "/")
_SWITCH_ON = os.path.join(_ASSETS, "switch-on.svg").replace("\\", "/")

STYLESHEET = f"""
QMainWindow, QDialog {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QWidget {{
    color: {TEXT};
    font-size: 13px;
}}
QWidget#inspector {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QWidget#inspector > QWidget {{
    background: transparent;
}}
QWidget#sourceHost {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QWidget#canvasStage {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#groupCard {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#sourceCard {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#sourceCard[selected="true"] {{
    border: 2px solid {ACCENT};
}}
QFrame#actionPill {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
QFrame#toolbar {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#fitBanner {{
    background: #2A1A1A;
    border: 1px solid {DANGER};
    border-radius: 8px;
}}
QLabel#fitBannerMsg {{
    color: #FECACA;
    font-size: 12px;
}}
QLabel#groupTitle {{
    color: {TEXT};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#fieldLabel {{
    color: {MUTED};
    font-size: 12px;
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QLabel#section {{
    color: {MUTED};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
}}
QLabel#muted {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#title {{
    font-size: 14px;
    font-weight: 650;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget {{
    background: {BG};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox {{
    padding: 5px 28px 5px 8px;
}}
QComboBox#modeCombo {{
    border: 2px solid {ACCENT};
    font-weight: 600;
}}
QComboBox#modeCombo:focus {{
    border: 2px solid {ACCENT2};
}}
QComboBox#modeCombo::drop-down {{
    border-left: 1px solid {ACCENT};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border: none;
    border-left: 1px solid {BORDER};
    background: {SURFACE2};
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}}
QComboBox::down-arrow {{
    image: url("{_CHEVRON}");
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE2};
    color: {TEXT};
    selection-background-color: {ACCENT};
    border: 1px solid {BORDER};
}}
QCheckBox {{
    spacing: 8px;
}}
QCheckBox[role="switch"] {{
    spacing: 10px;
    color: {TEXT};
}}
QCheckBox[role="switch"]::indicator {{
    width: 40px;
    height: 22px;
    border: none;
    background: transparent;
    image: url("{_SWITCH_OFF}");
}}
QCheckBox[role="switch"]::indicator:checked {{
    image: url("{_SWITCH_ON}");
}}
QCheckBox[role="switch"]::indicator:disabled {{
    opacity: 0.4;
}}
QFrame#rail {{
    background: #141418;
    border-right: 1px solid {BORDER};
}}
QPushButton[role="rail"] {{
    background: transparent;
    color: {MUTED};
    border: none;
    border-radius: 8px;
    padding: 10px 8px;
    text-align: left;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton[role="rail"]:hover {{
    background: rgba(255, 255, 255, 0.06);
    color: {TEXT};
}}
QPushButton[role="rail"][active="true"] {{
    background: rgba(59, 130, 246, 0.22);
    color: {TEXT};
}}
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar::tab {{
    background: {SURFACE2};
    color: {MUTED};
    padding: 4px 10px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 4px;
}}
QTabBar::tab:selected {{
    background: {ACCENT};
    color: {TEXT};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    margin: -6px 0;
    background: {ACCENT};
    border-radius: 7px;
}}
QPushButton {{
    background: {ACCENT};
    color: {TEXT};
    border: none;
    border-radius: 8px;
    padding: 7px 12px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {ACCENT2};
}}
QPushButton:disabled {{
    background: {SURFACE2};
    color: {MUTED};
}}
QPushButton[role="secondary"] {{
    background: transparent;
    color: {TEXT};
    border: 1px solid {BORDER};
}}
QPushButton[role="secondary"]:hover {{
    background: {SURFACE2};
}}
QPushButton[role="secondary"]:checked {{
    background: {ACCENT};
    color: {TEXT};
    border: 1px solid {ACCENT};
}}
QPushButton[role="secondary"]:checked:hover {{
    background: {ACCENT2};
    border-color: {ACCENT2};
}}
QPushButton[role="success"],
QPushButton#successBtn {{
    background: {ACCENT};
    color: {TEXT};
    font-weight: 700;
    border: none;
}}
QPushButton[role="success"]:hover,
QPushButton#successBtn:hover {{
    background: {ACCENT2};
    color: {TEXT};
}}
QPushButton[role="success"]:disabled,
QPushButton#successBtn:disabled {{
    background: {SURFACE2};
    color: {MUTED};
}}
QPushButton[role="ghost"] {{
    background: transparent;
    color: {MUTED};
    border: none;
}}
QListWidget::item, QTreeWidget::item {{
    padding: 4px;
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
    background: {ACCENT};
    color: white;
}}
QHeaderView::section {{
    background: {SURFACE};
    color: {MUTED};
    border: none;
    padding: 4px;
}}
QProgressBar {{
    background: {SURFACE2};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: {MUTED};
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 4px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
}}
QScrollBar:horizontal {{
    height: 0;
    background: transparent;
}}
"""
