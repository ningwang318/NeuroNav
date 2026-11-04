"""Sidebar section 03: scene display toggles, view buttons and utilities."""
from PySide6 import QtWidgets as Qt

from neuronav.ui.style import label


class DisplayPanel(Qt.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        side = Qt.QVBoxLayout(self)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(9)
        side.addWidget(label('03  DISPLAY', 12, '#67dfe1'))
        self.markers_check = Qt.QCheckBox('Observed markers')
        self.markers_check.setChecked(True)
        self.camera_check = Qt.QCheckBox('Trio 3 camera bar')
        self.camera_check.setChecked(True)
        self.grid_check = Qt.QCheckBox('Calibration grid and XYZ')
        self.grid_check.setChecked(False)
        for check in [self.markers_check, self.camera_check, self.grid_check]:
            side.addWidget(check)
        buttons = Qt.QHBoxLayout()
        self.fit_button = Qt.QPushButton('Fit scene')
        self.reset_button = Qt.QPushButton('Reset view')
        self.camera_view_button = Qt.QPushButton('Camera view')
        for button in [self.fit_button, self.reset_button, self.camera_view_button]:
            buttons.addWidget(button)
        side.addLayout(buttons)
        self.phantom_button = Qt.QPushButton('Head phantom settings…')
        side.addWidget(self.phantom_button)
        self.diagnostics_button = Qt.QPushButton('Save diagnostics…')
        side.addWidget(self.diagnostics_button)
        side.addWidget(label('Drag: orbit   ·   Wheel: zoom\nCtrl + drag: pan', 11, '#8ca3bc'))
        side.addStretch()
        side.addWidget(label('Camera symbols show calibrated poses. The grid is a display reference; it does not define calibrated tracking coverage.', 11, '#8ca3bc', wrap=True))

    def checks(self):
        return [self.markers_check, self.camera_check, self.grid_check]
