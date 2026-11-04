"""Three-marker reference-plane capture dialog (modeless).

Capture state (capture, capture_after, capture_started) and the dialog widgets
live on the main window, which forwards each refresh to advance_reference_capture.
"""
import time

import numpy as np
from PySide6 import QtWidgets as Qt

from neuronav.tracking.reference_plane import Capture, ReferencePlane
from neuronav.ui.style import label


def open_reference_dialog(window):
    if window.reference_dialog:
        window.reference_dialog.raise_()
        return
    dialog = Qt.QDialog(window)
    window.reference_dialog = dialog
    dialog.setWindowTitle('Three-marker reference plane')
    dialog.resize(600, 390)
    layout = Qt.QVBoxLayout(dialog)
    layout.addWidget(label('Keep exactly three markers visible and stationary during the two-second capture.\n\nOrigin: triangle centre. X: P1 → P2. Y: plane normal. Z: X × Y. Marker identities are determined from the unequal triangle side lengths.', 13, '#d7e4f3', wrap=True))
    layout.addWidget(label('This sets navigation coordinates. It does not recalibrate the cameras or establish MRI registration. The reference remains fixed after capture; it does not follow moving markers.', 12, '#f6bb6a', wrap=True))
    window.reference_status = label('Saved geometry available. Recalibrate to use it in this session.' if window.reference_path.exists() else 'No saved geometry yet.', 12, '#8ca3bc', wrap=True)
    layout.addWidget(window.reference_status)
    window.flip_normal = Qt.QCheckBox('Reverse plane normal (Y axis)')
    layout.addWidget(window.flip_normal)
    window.capture_progress = Qt.QProgressBar()
    window.capture_progress.setRange(0, 100)
    window.capture_progress.setValue(0)
    layout.addWidget(window.capture_progress)
    window.capture_new = Qt.QPushButton('Capture current three markers and save')
    window.capture_saved = Qt.QPushButton('Recalibrate from saved triangle')
    window.capture_saved.setEnabled(window.reference_path.exists())
    window.capture_new.clicked.connect(lambda: start_reference_capture(window, False))
    window.capture_saved.clicked.connect(lambda: start_reference_capture(window, True))
    layout.addWidget(window.capture_new)
    layout.addWidget(window.capture_saved)
    close = Qt.QPushButton('Close')
    close.clicked.connect(dialog.close)
    layout.addWidget(close)
    def closed():
        window.capture = None
        window.reference_dialog = None
    dialog.finished.connect(closed)
    dialog.show()


def start_reference_capture(window, use_saved):
    try:
        if not window.receiver:
            raise ValueError('Start tracking before capturing a reference plane.')
        snap = window.receiver.snapshot()
        frame = snap['frame']
        if not frame or time.monotonic()-frame.received >= .5 or snap['error']:
            raise ValueError('A healthy live tracking frame is required.')
        expected = ReferencePlane.load(window.reference_path) if use_saved else None
        window.capture = Capture(expected, window.flip_normal.isChecked())
        window.capture.add(frame.markers, frame.number)
        window.capture_after = frame.received
        window.capture_started = time.monotonic()
        window.capture_progress.setValue(0)
        window.reference_status.setText('Capturing… keep all three markers stationary.')
        window.capture_new.setEnabled(False)
        window.capture_saved.setEnabled(False)
    except (ValueError, OSError, KeyError) as exc:
        window.capture = None
        window.reference_status.setText(str(exc))


def advance_reference_capture(window, frame, fresh):
    if not window.capture:
        return
    try:
        if not fresh:
            raise ValueError('Tracking became stale. Reference capture cancelled; retry when tracking is live.')
        for sample in window.receiver.frames_since(window.capture_after):
            window.capture.add(sample.markers, sample.number)
            window.capture_after = sample.received
        elapsed = time.monotonic()-window.capture_started
        window.capture_progress.setValue(min(100, int(elapsed*50)))
        if elapsed < 2:
            return
        reference = window.capture.finish(dict(source=window.source.currentText(),
            calibration=window.calibration.text(), profile=window.profile.text()))
        reference.save(window.reference_path)
        window.apply_reference(reference)
        edges = np.linalg.norm(reference.markers[[0, 0, 1]]-reference.markers[[1, 2, 2]], axis=1)*1000
        window.reference_status.setText(f'Reference saved and applied. {len(window.capture.samples)} samples.\nEdges: {edges[0]:.2f}, {edges[1]:.2f}, {edges[2]:.2f} mm.\nThe main XYZ axes and grid now use this plane. Marker local Y coordinates are zero.')
        window.capture = None
    except (ValueError, OSError) as exc:
        window.capture = None
        window.reference_status.setText(f'Capture rejected: {exc}')
    if window.capture is None:
        reset_capture_buttons(window)


def cancel_reference_capture(window, message):
    window.capture = None
    if window.reference_dialog:
        window.reference_status.setText(message)
        reset_capture_buttons(window)


def reset_capture_buttons(window):
    window.capture_new.setEnabled(True)
    window.capture_saved.setEnabled(window.reference_path.exists())
