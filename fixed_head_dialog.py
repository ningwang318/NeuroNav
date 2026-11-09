"""Capture a stationary crown marker without requiring a head rigid body."""
import time
import numpy as np
from PySide6 import QtCore, QtWidgets as Qt
from fixed_head import FixedHead, crown_marker, estimate_mri_crown


def open_fixed_head_dialog(window):
    dialog = Qt.QDialog(window)
    dialog.setWindowTitle('Save head placement relative to table')
    dialog.setMinimumWidth(650)
    layout = Qt.QVBoxLayout(dialog)
    note = Qt.QLabel('Keep all four markers visible and the phantom stationary.\n'
        'Crown marker centre: 7.5 mm above the highest head surface.\n'
        'Assumed orientation: upright to table, nose toward P2 (15/25 cm corner).\n'
        'Uses native MRI size and an intensity-based crown estimate; review alignment.\n'
        'After saving, the head stays fixed relative to the table markers, even after\n'
        'the crown marker is removed. Recapture if the physical phantom moves.')
    note.setWordWrap(True)
    layout.addWidget(note)
    status = Qt.QLabel('Ready for a two-second capture. This replaces the saved fixed head placement.')
    status.setWordWrap(True)
    layout.addWidget(status)
    button = Qt.QPushButton('Capture fourth marker and save fixed head')
    layout.addWidget(button)
    close = Qt.QPushButton('Close')
    layout.addWidget(close)
    timer = QtCore.QTimer(dialog)
    timer.setInterval(25)
    capture = {}

    def start():
        try:
            if window.reference is None or window.receiver is None:
                raise ValueError('Apply the three-marker reference and start tracking first.')
            volume = window.view.phantom_mri
            if volume is None or not volume.source_sha256:
                raise ValueError('Load the matching physical MRI first.')
            crown, ras = estimate_mri_crown(volume, window.view.phantom_mri_transform,
                                          window.view.phantom_mri_preview.shape)
            capture.clear()
            capture.update(start=time.monotonic(), after=0, samples=[], crown=crown, ras=ras,
                           reference=window.reference, volume=volume)
            button.setEnabled(False)
            status.setText('Capturing… keep the phantom and table markers stationary.')
            timer.start()
        except (ValueError, OSError) as exc:
            status.setText(str(exc))

    def tick():
        try:
            snap = window.receiver.snapshot() if window.receiver else None
            if not snap or snap.get('error') or snap['frame'] is None or time.monotonic()-snap['frame'].received >= .5:
                raise ValueError('Tracking is not fresh; capture rejected.')
            if window.reference is not capture['reference'] or window.view.phantom_mri is not capture['volume']:
                raise ValueError('Reference or MRI changed during capture.')
            for frame in window.receiver.frames_since(max(capture['after'], capture['start'])):
                capture['samples'].append(crown_marker(frame.markers, window.reference))
                capture['after'] = frame.received
            if time.monotonic()-capture['start'] < 2:
                return
            fixed = FixedHead.capture(capture['samples'], window.reference, capture['crown'],
                                      capture['ras'], capture['volume'])
            if window.fixed_head_path.exists():
                import shutil
                shutil.copy2(window.fixed_head_path, window.fixed_head_path.with_suffix('.previous.json'))
            fixed.save(window.fixed_head_path)
            window.view.fixed_head = fixed
            window.phantom.enabled = True
            window.phantom.marker_above_top_mm = 7.5
            window.phantom.save(window.phantom_path)
            window.refresh()
            window.fit_scene()
            position = np.asarray(fixed.data['crown_marker_reference_m'])*1000
            status.setText(f"Saved {len(capture['samples'])} samples. Crown RMS jitter: "
                f"{fixed.data['crown_jitter_rms_mm']:.3f} mm.\n"
                'Marker XYZ relative to table (mm): '+', '.join(f'{v:.2f}' for v in position)+
                '\nHead is fixed to table. The fourth marker can now be removed.\n'
                'MRI crown estimate and assumed orientation still require visual review.')
            timer.stop()
            button.setEnabled(True)
        except (ValueError, KeyError, OSError) as exc:
            timer.stop()
            button.setEnabled(True)
            status.setText('Capture rejected: '+str(exc))

    timer.timeout.connect(tick)
    button.clicked.connect(start)
    close.clicked.connect(dialog.accept)
    dialog.finished.connect(timer.stop)
    dialog.exec()
