"""Sidebar section 01: tracking source selection and its connection settings."""
from PySide6 import QtWidgets as Qt

from neuronav.ui.style import label


class TrackingSourcePanel(Qt.QWidget):
    """Motive API / NatNet forms. Field defaults come from the caller (settings.ini, local.toml)."""
    API, NATNET = 0, 1

    def __init__(self, defaults, parent=None):
        super().__init__(parent)
        side = Qt.QVBoxLayout(self)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(9)
        side.addWidget(label('01  TRACKING SOURCE', 12, '#67dfe1'))
        self.source = Qt.QComboBox()
        self.source.addItems(['Standalone · Motive API', 'Network · NatNet'])
        side.addWidget(self.source)
        self.source_forms = Qt.QStackedWidget()
        api_form_widget = Qt.QWidget()
        api_form = Qt.QFormLayout(api_form_widget)
        api_form.setContentsMargins(0, 0, 0, 0)
        self.install = Qt.QLineEdit(defaults['api_install'])
        self.calibration = Qt.QLineEdit(defaults['api_calibration'])
        self.profile = Qt.QLineEdit(defaults['api_profile'])
        api_form.addRow('Motive runtime', self.install)
        for title_text, field, pattern in [('Calibration', self.calibration, 'Calibration (*.mcal)'),
                                           ('Asset profile', self.profile, 'Profile (*.motive)')]:
            row_widget = Qt.QWidget()
            row_layout = Qt.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            row_layout.addWidget(field, 1)
            browse = Qt.QPushButton('…')
            browse.setFixedWidth(30)
            browse.clicked.connect(lambda checked=False, f=field, p=pattern: self.browse_config(f, p))
            row_layout.addWidget(browse)
            api_form.addRow(title_text, row_widget)
        self.source_forms.addWidget(api_form_widget)
        network_widget = Qt.QWidget()
        form = Qt.QFormLayout(network_widget)
        form.setContentsMargins(0, 0, 0, 0)
        self.server = Qt.QLineEdit(defaults['server'])
        self.interface = Qt.QLineEdit(defaults['interface'])
        self.group = Qt.QLineEdit(defaults['group'])
        self.port = Qt.QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(defaults['port']))
        for name, field in [('Motive server', self.server), ('Local interface', self.interface),
                             ('Multicast group', self.group), ('Data port', self.port)]:
            form.addRow(name, field)
        self.source_forms.addWidget(network_widget)
        side.addWidget(self.source_forms)
        self.source.currentIndexChanged.connect(self.source_forms.setCurrentIndex)
        self.connect_button = Qt.QPushButton('Start tracking')
        self.connect_button.setObjectName('primary')
        side.addWidget(self.connect_button)
        self.source_info = label('Direct camera access · Motive application must be closed', 11, '#8ca3bc', wrap=True)
        side.addWidget(self.source_info)

    def browse_config(self, field, pattern):
        path, _ = Qt.QFileDialog.getOpenFileName(self, 'Select tracking configuration', field.text(), pattern)
        if path:
            field.setText(path)
            field.setToolTip(path)

    def values(self):
        """Current field values, keyed like settings.ini."""
        return dict(server=self.server.text(), interface=self.interface.text(),
                    group=self.group.text(), port=self.port.value(),
                    api_install=self.install.text(), api_calibration=self.calibration.text(),
                    api_profile=self.profile.text())

    def set_connected(self, connected):
        for field in [self.server, self.interface, self.group, self.port]:
            field.setEnabled(not connected)
        self.source.setEnabled(not connected)
        self.source_forms.setEnabled(not connected)
