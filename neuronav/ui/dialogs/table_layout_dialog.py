"""Editable breadboard/robot illustration geometry (appearance only)."""
from copy import deepcopy

from PySide6 import QtWidgets as Qt

from neuronav.ui.style import label


FIELDS = [('width_mm','Width'),('depth_mm','Depth'),('thickness_mm','Thickness'),
          ('center_x_mm','Center X'),('center_z_mm','Center Z'),('top_y_mm','Top Y'),
          ('yaw_deg','Table yaw'),('robot_x_mm','Robot base X from table center'),
          ('robot_z_mm','Robot base Z from table center')]


def open_table_layout_dialog(window):
    dialog=Qt.QDialog(window)
    dialog.setWindowTitle('Table size and placement')
    layout=Qt.QVBoxLayout(dialog)
    layout.addWidget(label('Photo-estimated illustration. Center and top height use the saved\n'
        'table-marker reference coordinates. Robot offsets use the table frame.',12,'#d6a96a',wrap=True))
    form=Qt.QFormLayout()
    fields={}
    for name,title in FIELDS:
        field=Qt.QDoubleSpinBox()
        field.setRange(-3000,3000)
        field.setDecimals(2)
        field.setSuffix('°' if name=='yaw_deg' else ' mm')
        field.setValue(getattr(window.table_layout,name))
        fields[name]=field
        form.addRow(title,field)
    layout.addLayout(form)
    status=label('Apply saves this appearance geometry; tracking and head-marker offsets are unchanged.',11,wrap=True)
    layout.addWidget(status)
    buttons=Qt.QDialogButtonBox(Qt.QDialogButtonBox.Apply|Qt.QDialogButtonBox.Cancel)
    def apply():
        try:
            draft=deepcopy(window.table_layout)
            for name,field in fields.items():
                setattr(draft,name,field.value())
            draft.save(window.table_layout_path)
            window.table_layout=window.view.table_layout=draft
            cameras=window.last_snapshot['models'].cameras if window.last_snapshot else []
            window.view.update_setup(cameras)
            window.fit_scene()
            dialog.accept()
        except (ValueError,OSError) as exc:
            status.setText(str(exc))
    buttons.button(Qt.QDialogButtonBox.Apply).clicked.connect(apply)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
