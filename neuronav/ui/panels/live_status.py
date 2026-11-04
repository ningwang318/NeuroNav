"""Sidebar section 02: stream health metrics and the status message."""
from PySide6 import QtWidgets as Qt

from neuronav.ui.style import label


METRICS = ['Stream', 'Frame', 'Receive rate', 'Frame age', 'Markers', 'Rigid bodies', 'Cameras']


class LiveStatusPanel(Qt.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        side = Qt.QVBoxLayout(self)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(9)
        side.addWidget(label('02  LIVE STATUS', 12, '#67dfe1'))
        stats = Qt.QGridLayout()
        self.metrics = {}
        for row, name in enumerate(METRICS):
            stats.addWidget(label(name, 12, '#8ca3bc'), row, 0)
            self.metrics[name] = label('—', 12, '#e9f2ff')
            stats.addWidget(self.metrics[name], row, 1)
        side.addLayout(stats)
        self.message = label('Connect to open the live tracking space.', 12, '#b5c7dc', wrap=True)
        self.message.setMinimumHeight(60)
        side.addWidget(self.message)

    def clear(self):
        for metric in self.metrics.values():
            metric.setText('—')
