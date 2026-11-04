"""Editable physical offset between a tracked rigid body and a head phantom."""
from copy import deepcopy
import time
from PySide6 import QtWidgets as Qt


def open_phantom_dialog(window):
    dialog=Qt.QDialog(window)
    dialog.setWindowTitle('Head phantom — marker offset and size')
    dialog.setMinimumWidth(560)
    layout=Qt.QVBoxLayout(dialog)
    note=Qt.QLabel('The tracked head is rendered from the loaded MRI. Marker alignment remains assumed until measured.\n'
        'Phantom origin: bottom center. Local +Y: up; +Z: nose.\n'
        'Offsets below are from the tracked rigid-body origin, which may differ from the marker-cluster center.\n'
        'This is an assumed MRI-to-marker placement, not a measured registration.')
    note.setWordWrap(True)
    layout.addWidget(note)
    draft=deepcopy(window.phantom)
    form=Qt.QFormLayout()
    enabled=Qt.QCheckBox('Show tracked phantom instead of Head XYZ axes')
    enabled.setChecked(draft.enabled)
    form.addRow(enabled)
    body_name=Qt.QComboBox()
    body_name.setEditable(True)
    names=list(window.last_snapshot['models'].names.values()) if window.last_snapshot else []
    body_name.addItems(sorted(set([draft.body_name,*names])))
    body_name.setCurrentText(draft.body_name)
    form.addRow('Marker rigid body',body_name)
    use_mri_size=Qt.QCheckBox('Use physical MRI dimensions (preserve MRI proportions)')
    use_mri_size.setChecked(draft.use_mri_size)
    form.addRow(use_mri_size)
    def spin(value,minimum,maximum,suffix):
        field=Qt.QDoubleSpinBox()
        field.setRange(minimum,maximum)
        field.setDecimals(2)
        field.setSuffix(suffix)
        field.setValue(value)
        return field
    dimensions=[]
    for label,attribute in [('Height','height_mm'),('Width','width_mm'),('Cranial depth','depth_mm')]:
        field=spin(getattr(draft,attribute),20,500,' mm')
        form.addRow(label,field)
        dimensions.append((attribute,field))
    def size_mode():
        for _,field in dimensions:
            field.setEnabled(not use_mri_size.isChecked())
        if use_mri_size.isChecked() and window.view.phantom_mri_dimensions is not None:
            for (_,field),value in zip(dimensions,window.view.phantom_mri_dimensions):
                field.setValue(float(value))
    use_mri_size.toggled.connect(size_mode)
    size_mode()
    above=spin(draft.marker_above_top_mm,0,500,' mm')
    form.addRow('Rigid-body origin above head top',above)
    layout.addLayout(form)
    placement=Qt.QPushButton('Use current upright placement · nose toward camera side of table')
    layout.addWidget(placement)
    group=Qt.QGroupBox('Phantom bottom center and orientation in marker rigid-body coordinates')
    grid=Qt.QGridLayout(group)
    grid.addWidget(Qt.QLabel('Axis'),0,0)
    grid.addWidget(Qt.QLabel('Bottom-center offset'),0,1)
    grid.addWidget(Qt.QLabel('Rotation (fixed X, then Y, then Z)'),0,2)
    translations=[]
    rotations=[]
    for axis in range(3):
        t=spin(draft.translation_mm[axis],-2000,2000,' mm')
        r=spin(draft.rotation_deg[axis],-360,360,'°')
        translations.append(t)
        rotations.append(r)
        grid.addWidget(Qt.QLabel('XYZ'[axis]),axis+1,0)
        grid.addWidget(t,axis+1,1)
        grid.addWidget(r,axis+1,2)
    layout.addWidget(group)
    status=Qt.QLabel('Use the current-placement button after changing dimensions or marker height,\n'
                    'or edit the body-local translation and rotation directly. Apply saves to this computer.')
    status.setWordWrap(True)
    layout.addWidget(status)
    def read_fields():
        draft.enabled=enabled.isChecked()
        draft.body_name=body_name.currentText().strip()
        draft.use_mri_size=use_mri_size.isChecked()
        for attribute,field in dimensions:
            setattr(draft,attribute,field.value())
        draft.marker_above_top_mm=above.value()
        draft.translation_mm=[field.value() for field in translations]
        draft.rotation_deg=[field.value() for field in rotations]
        draft.validate()
    def capture():
        try:
            read_fields()
            snap=window.receiver.snapshot() if window.receiver else window.last_snapshot
            frame=snap['frame'] if snap else None
            if frame is None or time.monotonic()-frame.received>=.5 or snap.get('error'):
                raise ValueError('The head-marker pose must be live before capturing placement.')
            body=draft.body(frame,snap['models'])
            if body is None:
                raise ValueError('The selected rigid body is not tracked.')
            draft.align_upright(body,window.reference,snap['models'].cameras)
            for field,value in zip(translations,draft.translation_mm):
                field.setValue(value)
            for field,value in zip(rotations,draft.rotation_deg):
                field.setValue(value)
            status.setText('Placement captured in marker coordinates. Apply to save it.\n'
                           'This follows the assumed upright placement; it is not a measured calibration.')
        except (ValueError,KeyError) as exc:
            status.setText(str(exc))
    def apply():
        try:
            read_fields()
            if draft.use_mri_size and window.view.phantom_mri_dimensions is not None:
                # Preserve the top-to-marker distance if native MRI size is selected.
                previous=deepcopy(window.phantom)
                previous.adopt_mri_size(window.view.phantom_mri_dimensions)
                if [field.value() for field in translations]==[round(v,2) for v in window.phantom.translation_mm]:
                    draft.translation_mm=previous.translation_mm
            draft.configured=True
            draft.save(window.phantom_path)
            window.phantom=draft
            window.view.phantom=draft
            window.refresh()
            dialog.accept()
        except (ValueError,OSError) as exc:
            status.setText(str(exc))
    placement.clicked.connect(capture)
    buttons=Qt.QDialogButtonBox(Qt.QDialogButtonBox.Apply|Qt.QDialogButtonBox.Cancel)
    buttons.button(Qt.QDialogButtonBox.Apply).clicked.connect(apply)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
