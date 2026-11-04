"""Application stylesheet and small widget helpers."""
from PySide6 import QtWidgets as Qt


STYLE = '''
QWidget { background:#101c2c; color:#d7e4f3; font-family:'Segoe UI'; font-size:12px; }
QLabel { background:transparent; }
QLineEdit, QSpinBox { background:#182a3d; border:1px solid #2a4057; border-radius:5px; padding:7px; }
QLineEdit:disabled, QSpinBox:disabled { color:#7c91a6; }
QPushButton { background:#21364b; border:1px solid #304b66; border-radius:6px; padding:9px; }
QPushButton:hover { background:#2c4760; }
QPushButton#primary { background:#22767d; border-color:#3a9fa7; color:white; font-weight:600; }
QLabel#badge { border:1px solid #34536d; border-radius:12px; padding:9px 14px; }
QTableWidget { background:#101c2c; alternate-background-color:#17273a; border:1px solid #294058; gridline-color:#23374e; }
QHeaderView::section { background:#1b2e43; color:#9fb5cc; border:0; padding:8px; }
QCheckBox { spacing:8px; padding:3px; }
QSplitter::handle { background:#24364b; width:1px; }
'''


def label(text, size=None, color=None, wrap=False):
    widget = Qt.QLabel(text)
    style = ''
    if size:
        style += f'font-size:{size}px;'
    if color:
        style += f'color:{color};'
    widget.setStyleSheet(style)
    widget.setWordWrap(wrap)
    return widget
