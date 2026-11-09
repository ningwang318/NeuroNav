"""Numeric controls with compact decimal formatting."""
from PySide6 import QtWidgets as Qt


class CompactNumber(Qt.QDoubleSpinBox):
    def textFromValue(self, value):
        text = f'{value:.{self.decimals()}f}'
        if self.decimals():
            text = text.rstrip('0').rstrip('.')
        return text.replace('.', self.locale().decimalPoint())


class TypedNumber(CompactNumber):
    """Decimal input without spin buttons, wheel changes or key stepping."""
    def __init__(self):
        super().__init__()
        self.setButtonSymbols(Qt.QAbstractSpinBox.NoButtons)

    def wheelEvent(self, event):
        event.ignore()

    def stepBy(self, steps):
        pass
