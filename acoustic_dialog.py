"""Transducer selection, measured-field calibration and future marker attachment."""
from copy import copy,deepcopy
from pathlib import Path
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets as Qt
import pyqtgraph.opengl as gl
from acoustic_field import AcousticField
from acoustic_scene import AcousticLayer


def open_acoustic_dialog(window):
    controller=window.acoustic
    draft=deepcopy(controller.placement)
    selected=copy(controller.field)
    selected.profile=deepcopy(controller.field.profile)
    dialog=Qt.QDialog(window)
    dialog.setWindowTitle('Ultrasound transducer and measured focal field')
    dialog.resize(960,720)
    outer=Qt.QVBoxLayout(dialog)
    selector=Qt.QComboBox()
    paths=sorted((Path(__file__).parent/'config').glob('transducer_*.json'))
    for path in paths:
        import json
        selector.addItem(json.loads(path.read_text(encoding='utf-8'))['name'],str(path))
    selector.setCurrentIndex(next((i for i,p in enumerate(paths) if p==selected.path),0))
    outer.addWidget(selector)
    preview_mode=Qt.QComboBox()
    preview_mode.addItems(['Device schematic','Focal contours in sample coordinates'])
    preview_mode.setCurrentIndex(1)
    outer.addWidget(preview_mode)
    row=Qt.QHBoxLayout()
    tabs=Qt.QTabWidget()
    row.addWidget(tabs,3)
    preview=gl.GLViewWidget()
    preview.setBackgroundColor('#080f1a')
    preview.setMinimumSize(270,250)
    preview.setCameraPosition(distance=210,elevation=20,azimuth=35)
    row.addWidget(preview,2)
    outer.addLayout(row,1)
    layer=None
    def spin(value,maximum=1000,suffix=' mm',minimum=0,decimals=3):
        control=Qt.QDoubleSpinBox()
        control.setRange(minimum,maximum)
        control.setDecimals(decimals)
        control.setSuffix(suffix)
        control.setValue(float(value or 0))
        return control
    def page(title):
        widget=Qt.QWidget()
        form=Qt.QFormLayout(widget)
        tabs.addTab(widget,title)
        return form
    field_form=page('Transducer / field')
    name=Qt.QLineEdit(selected.profile['name'])
    field_form.addRow('Name',name)
    frequency=spin(selected.profile['frequency_khz'],3000,' kHz')
    aperture=spin(selected.profile['aperture_mm'],200,' mm',1)
    radius=spin(selected.profile.get('radius_mm'),500)
    radius.setSpecialValueText('Unknown · schematic bowl')
    nominal=spin(selected.profile.get('nominal_focal_distance_mm'),500)
    field_form.addRow('Frequency',frequency)
    field_form.addRow('Aperture diameter',aperture)
    field_form.addRow('Radius of curvature',radius)
    field_form.addRow('Nominal focal distance',nominal)
    sample_controls={}
    for key,title in [('axial_spacing_mm','Axial sample spacing'),('lateral1_spacing_mm','Lateral 1 sample spacing'),
                      ('lateral2_spacing_mm','Lateral 2 sample spacing'),('near_offset_mm','Surface → last (near) slice')]:
        control=spin(selected.profile.get(key),100)
        field_form.addRow(title,control)
        sample_controls[key]=control
    confirmed=Qt.QCheckBox('Sampling and surface origin confirmed')
    confirmed.setChecked(selected.calibrated)
    field_form.addRow(confirmed)
    reverse_axial=Qt.QCheckBox('Reverse axial matrix and focal contours')
    reverse_axial.setChecked(selected.axial_reversed)
    field_form.addRow(reverse_axial)
    field_form.addRow(Qt.QLabel('Matrix row 1 = far field; last row = near field.\n'
        'Beam +Z points downstream from the surface.\n'
        'Unknown sampling blocks physical MRI overlays.'))
    visible={}
    for db,color in [(-3,'red'),(-6,'yellow-green'),(-12,'cyan')]:
        check=Qt.QCheckBox(f'{db} dB · {color}')
        check.setChecked(db in draft.visible_thresholds)
        visible[db]=check
        field_form.addRow(check)
    pose_form=page('MRI preview pose')
    enabled=Qt.QCheckBox('Show transducer and focal contours')
    enabled.setChecked(draft.enabled)
    pose_form.addRow(enabled)
    mode=Qt.QComboBox()
    mode.addItem('Manual MRI preview','manual')
    mode.addItem('Live marker preview (provisional attachment)','preview')
    mode.addItem('Tracked markers (requires attachment calibration)','tracked')
    mode.setCurrentIndex(mode.findData(draft.mode))
    pose_form.addRow('Placement',mode)
    positions=[];rotations=[]
    for axis in range(3):
        control=spin(draft.position_mm[axis],2000,minimum=-2000)
        positions.append(control)
        pose_form.addRow('MRI RAS '+'XYZ'[axis],control)
    for axis in range(3):
        control=spin(draft.rotation_deg[axis],360,'°',-360)
        rotations.append(control)
        pose_form.addRow('Rotation '+'XYZ'[axis],control)
    for control in positions+rotations:
        control.valueChanged.connect(lambda value:setattr(draft,'manual_initialized',True))
    center=Qt.QPushButton('Preview measured peak at MRI center')
    pose_form.addRow(center)
    crosshair=Qt.QPushButton('Preview measured peak at linked MRI crosshair')
    pose_form.addRow(crosshair)
    pose_form.addRow(Qt.QLabel('Manual mode is a placement preview, not a tracked pose.\n'
                              'Use “Advance along beam” in the main window to\n'
                              'move the transducer and its field together.'))
    marker_form=page('Marker attachment')
    body=Qt.QLineEdit(draft.marker_body)
    marker_form.addRow('Rigid-body name',body)
    marker_positions=[];marker_rotations=[]
    for axis in range(3):
        control=spin(draft.marker_translation_mm[axis],1000,minimum=-1000)
        marker_positions.append(control)
        marker_form.addRow('Surface apex offset '+'XYZ'[axis],control)
    for axis in range(3):
        control=spin(draft.marker_rotation_deg[axis],360,'°',-360)
        marker_rotations.append(control)
        marker_form.addRow('Transducer rotation '+'XYZ'[axis],control)
    attached=Qt.QCheckBox('Measured marker-to-transducer transform entered')
    attached.setChecked(draft.marker_calibrated)
    marker_form.addRow(attached)
    marker_form.addRow(Qt.QLabel('Offsets are in marker-local coordinates. Apex = surface\n'
        'center; +Z = beam direction. Leave unconfirmed until\n'
        'the marker attachment measurements are available.\n'
        'Live marker preview uses these offsets provisionally;\n'
        'zero offsets place the surface center at the marker pivot.'))
    status=Qt.QLabel()
    status.setWordWrap(True)
    outer.addWidget(status)
    def read_fields():
        selected.profile['name']=name.text().strip()
        selected.profile['frequency_khz']=frequency.value()
        selected.profile['aperture_mm']=aperture.value()
        selected.profile['radius_mm']=radius.value() or None
        selected.profile['nominal_focal_distance_mm']=nominal.value() or None
        for key,control in sample_controls.items():
            selected.profile[key]=control.value()
        selected.profile['metric_confirmed']=confirmed.isChecked()
        selected.set_axial_reversed(reverse_axial.isChecked())
        if confirmed.isChecked() and not selected.calibrated:
            raise ValueError('Confirmed sample spacings must be positive; nearest-slice offset must be nonnegative.')
        draft.enabled=enabled.isChecked()
        draft.mode=mode.currentData()
        draft.position_mm=[control.value() for control in positions]
        draft.rotation_deg=[control.value() for control in rotations]
        draft.marker_body=body.text().strip()
        draft.marker_translation_mm=[control.value() for control in marker_positions]
        draft.marker_rotation_deg=[control.value() for control in marker_rotations]
        draft.marker_calibrated=attached.isChecked()
        draft.visible_thresholds=[db for db,check in visible.items() if check.isChecked()]
        draft.validate()
    def refresh_preview():
        nonlocal layer
        if layer:
            layer.clear()
        sample_space=not selected.calibrated and preview_mode.currentIndex()==1
        preview_field=selected
        if sample_space:
            preview_field=copy(selected)
            preview_field.profile=deepcopy(selected.profile)
            preview_field.profile.update(axial_spacing_mm=1.,lateral1_spacing_mm=1.,lateral2_spacing_mm=1.,
                                         near_offset_mm=0.,metric_confirmed=True)
        layer=AcousticLayer(preview,preview_field,field_only=sample_space)
        layer.visible(True,draft.visible_thresholds)
        preview_mode.setVisible(not selected.calibrated)
        if selected.calibrated:
            center_z=selected.peak[2]/2
            preview.setCameraPosition(pos=QtGui.QVector3D(0,0,center_z),distance=max(180,selected.peak[2]*2.4))
            status.setText('Exact supplied FIG contours · Peak local XYZ (mm): '+', '.join(f'{v:.2f}' for v in selected.peak)+
                           f" · Nominal focus: {selected.profile.get('nominal_focal_distance_mm') or 'unknown'} mm"+
                           '\nMeasured geometry is retained without shifting its peak to the nominal focal distance.')
        else:
            if sample_space:
                preview.setCameraPosition(pos=QtGui.QVector3D(0,0,100),distance=230)
            else:
                preview.setCameraPosition(pos=QtGui.QVector3D(0,0,0),distance=160)
            status.setText(('FOCAL PREVIEW IN SAMPLE COORDINATES — not millimetres.' if sample_space else 'DEVICE SCHEMATIC — curvature is not yet confirmed.')+
                           '\nExact FIG colors and meshes are imported; physical MRI overlay waits for scan calibration.')
    def center_peak():
        try:
            read_fields()
            if window.mri_workspace.volume is None:
                raise ValueError('Load MRI first.')
            draft.center_focus(selected,window.mri_workspace.volume)
            for control,value in zip(positions,draft.position_mm):
                control.setValue(value)
            refresh_preview()
        except ValueError as exc:
            status.setText(str(exc))
    def crosshair_peak():
        try:
            read_fields()
            volume=window.mri_workspace.volume
            if volume is None:
                raise ValueError('Load MRI and select a crosshair point first.')
            draft.focus_at(selected,volume.physical(window.mri_workspace.indices))
            for control,value in zip(positions,draft.position_mm):
                control.setValue(value)
            refresh_preview()
        except ValueError as exc:
            status.setText(str(exc))
    def choose_profile(index):
        nonlocal selected
        selected=AcousticField(selector.itemData(index))
        name.setText(selected.profile['name']);frequency.setValue(selected.profile['frequency_khz'])
        aperture.setValue(selected.profile['aperture_mm']);radius.setValue(selected.profile.get('radius_mm') or 0)
        nominal.setValue(selected.profile.get('nominal_focal_distance_mm') or 0)
        for key,control in sample_controls.items():
            control.setValue(selected.profile.get(key) or 0)
        confirmed.setChecked(selected.calibrated)
        reverse_axial.blockSignals(True)
        reverse_axial.setChecked(selected.axial_reversed)
        reverse_axial.blockSignals(False)
        refresh_preview()
    def apply():
        try:
            read_fields()
            # Explicit pose fields become the manual origin, rather than being auto-centered later.
            if not draft.manual_initialized and selected.calibrated and window.mri_workspace.volume is not None:
                draft.center_focus(selected,window.mri_workspace.volume)
            selected.save_profile()
            draft.profile_name=selected.path.name
            draft.save(window.acoustic_path)
            controller.placement=draft
            controller.rebuild(selected)
            shift=window.mri_workspace.acoustic_shift
            shift.blockSignals(True);shift.setValue(round(draft.beam_shift_mm));shift.blockSignals(False)
            shift.setEnabled(selected.calibrated and draft.mode=='manual')
            dialog.accept()
        except (ValueError,OSError) as exc:
            status.setText(str(exc))
    center.clicked.connect(center_peak)
    crosshair.clicked.connect(crosshair_peak)
    selector.currentIndexChanged.connect(choose_profile)
    def flip_preview():
        selected.set_axial_reversed(reverse_axial.isChecked())
        refresh_preview()
    reverse_axial.toggled.connect(flip_preview)
    preview_mode.currentIndexChanged.connect(refresh_preview)
    buttons=Qt.QDialogButtonBox(Qt.QDialogButtonBox.Apply|Qt.QDialogButtonBox.Cancel)
    buttons.button(Qt.QDialogButtonBox.Apply).clicked.connect(apply)
    buttons.rejected.connect(dialog.reject)
    outer.addWidget(buttons)
    refresh_preview()
    dialog.exec()
    layer.clear()
