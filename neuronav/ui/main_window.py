"""NeuroNav main window: wires tracking sources, the live scene, MRI and acoustic overlays."""
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as Qt

from neuronav.acoustics.field import AcousticField, AcousticPlacement
from neuronav.core.config import ROOT, CONFIG_DIR
from neuronav.core.diagnostics import save_diagnostics
from neuronav.core.frames import reference_data
from neuronav.core.head_phantom import PhantomSettings
from neuronav.core.table_layout import TableLayout
from neuronav.tracking.motive_api import MotiveTracking
from neuronav.tracking.natnet import NatNet, Models
from neuronav.tracking.reference_plane import ReferencePlane
from neuronav.ui.acoustic_scene import AcousticController
from neuronav.ui.dialogs import reference_plane_dialog
from neuronav.ui.mri_workspace import MRIWorkspace
from neuronav.ui.panels.display import DisplayPanel
from neuronav.ui.panels.live_status import LiveStatusPanel
from neuronav.ui.panels.tracking_source import TrackingSourcePanel
from neuronav.ui.style import label
from neuronav.ui.workspace_view import Workspace


class MainWindow(Qt.QMainWindow):
    def __init__(self, auto_connect=True):
        super().__init__()
        self.setWindowTitle('NeuroNav — OptiTrack Tracking Workspace')
        screen=Qt.QApplication.primaryScreen().availableGeometry()
        self.resize(min(1600,screen.width()-40),min(980,screen.height()-40))
        self.receiver = None
        self.last_frame = -1
        self.last_snapshot = None
        self.last_rate_time = time.monotonic()
        self.last_rate_count = 0
        self.rate = 0
        self.fitted = False
        self.camera_closeup_pending = False
        self.reference = None
        self.capture = None
        self.reference_dialog = None
        self.reference_path = CONFIG_DIR/'reference_plane.json'
        self.phantom_path=CONFIG_DIR/'head_phantom.json'
        self.table_layout_path=CONFIG_DIR/'table_layout.json'
        try:
            self.table_layout=TableLayout.load(self.table_layout_path)
        except (ValueError,TypeError,KeyError,OSError):
            self.table_layout=TableLayout()
        try:
            self.phantom=PhantomSettings.load(self.phantom_path)
        except (ValueError,TypeError,OSError):
            self.phantom=PhantomSettings()
        self.settings = QtCore.QSettings(str(ROOT / 'settings.ini'), QtCore.QSettings.IniFormat)
        self.build_menus()
        root = Qt.QWidget()
        self.setCentralWidget(root)
        outer = Qt.QVBoxLayout(root)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(14)
        header = Qt.QHBoxLayout()
        title = Qt.QVBoxLayout()
        title.addWidget(label('NEURONAV', 25, '#e9f2ff'))
        title.addWidget(label('Standalone optical tracking  /  v0.2', 12, '#8ca3bc'))
        header.addLayout(title, 1)
        self.badge = label('DISCONNECTED', 12, '#f6bb6a')
        self.badge.setObjectName('badge')
        header.addWidget(self.badge)
        outer.addLayout(header)
        split = Qt.QSplitter()
        outer.addWidget(split, 1)
        split.addWidget(self.build_sidebar())
        split.addWidget(self.build_main_area())
        split.setStretchFactor(1, 1)
        split.setSizes([330, 1000])
        outer.addWidget(label('RESEARCH PROTOTYPE  ·  Robot/tripod layout approximate; robot pose static  ·  No robot or ultrasound control', 11, '#8ca3bc'))
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(33)
        if auto_connect:
            QtCore.QTimer.singleShot(150, self.toggle_connection)

    # ----------------------------------------------------------------- layout
    def build_menus(self):
        calibration_menu = self.menuBar().addMenu('Reference plane')
        calibration_menu.addAction('Capture / recalibrate three-marker plane…', self.open_reference_dialog)
        calibration_menu.addAction('Use saved XYZ coordinates', self.use_saved_reference)
        calibration_menu.addAction('Use original tracker coordinates', self.clear_reference)
        self.saved_geometry_action = calibration_menu.addAction('Show saved triangle and P1–P3 labels')
        self.saved_geometry_action.setCheckable(True)
        self.saved_geometry_action.toggled.connect(self.toggle_saved_geometry)
        self.hide_reference_action = calibration_menu.addAction('Hide stationary reference markers')
        self.hide_reference_action.setCheckable(True)
        self.hide_reference_action.setChecked(True)
        self.hide_reference_action.setToolTip('Display only: hides up to three observations within 2 mm of the saved reference positions.')
        self.hide_reference_action.toggled.connect(self.toggle_reference_markers)
        view_menu = self.menuBar().addMenu('View')
        view_menu.addAction('Table size and placement…',self.open_table_layout_dialog)
        phantom_menu=self.menuBar().addMenu('Head phantom')
        phantom_menu.addAction('Marker offset and phantom size…',self.open_phantom_dialog)
        self.lab_setup_action = view_menu.addAction('Approximate robot, tripod and breadboard')
        self.lab_setup_action.setCheckable(True)
        self.lab_setup_action.setChecked(True)
        self.lab_setup_action.toggled.connect(self.toggle_lab_setup)

    def tracking_defaults(self):
        value = self.settings.value
        return dict(api_install=value('api_install', r'C:\Program Files\OptiTrack\Motive'),
                    api_calibration=value('api_calibration', str(CONFIG_DIR/'System Calibration.mcal')),
                    api_profile=value('api_profile', str(CONFIG_DIR/'MotiveProfile.motive')),
                    server=value('server', '127.0.0.1'), interface=value('interface', '127.0.0.1'),
                    group=value('group', '239.255.42.99'), port=value('port', 1511))

    def build_sidebar(self):
        sidebar = Qt.QWidget()
        sidebar.setMaximumWidth(390)
        sidebar.setMinimumWidth(300)
        side = Qt.QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 16, 0)
        side.setSpacing(9)
        self.source_panel = TrackingSourcePanel(self.tracking_defaults())
        self.status_panel = LiveStatusPanel()
        self.display_panel = DisplayPanel()
        side.addWidget(self.source_panel)
        side.addWidget(self.status_panel)
        side.addWidget(self.display_panel, 1)
        # Existing attribute names, used by dialogs and tests.
        panel = self.source_panel
        self.source, self.source_forms, self.source_info = panel.source, panel.source_forms, panel.source_info
        self.install, self.calibration, self.profile = panel.install, panel.calibration, panel.profile
        self.server, self.interface, self.group, self.port = panel.server, panel.interface, panel.group, panel.port
        self.connect_button = panel.connect_button
        self.metrics, self.message = self.status_panel.metrics, self.status_panel.message
        display = self.display_panel
        self.markers_check, self.camera_check, self.grid_check = display.markers_check, display.camera_check, display.grid_check
        self.connect_button.clicked.connect(self.toggle_connection)
        for check in display.checks():
            check.toggled.connect(self.display_changed)
        display.fit_button.clicked.connect(self.fit_scene)
        display.reset_button.clicked.connect(self.reset_view)
        display.camera_view_button.clicked.connect(self.focus_camera_bar)
        display.phantom_button.clicked.connect(self.open_phantom_dialog)
        display.diagnostics_button.clicked.connect(self.save_diagnostics)
        return sidebar

    def build_main_area(self):
        right = Qt.QWidget()
        layout = Qt.QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toolbar = Qt.QHBoxLayout()
        toolbar.addWidget(label('NAVIGATION AND MRI', 13, '#e9f2ff'))
        toolbar.addStretch()
        self.coordinate_label = label('Tracker coordinates · Y up · millimetres', 11, '#8ca3bc')
        toolbar.addWidget(self.coordinate_label)
        layout.addLayout(toolbar)
        self.view = Workspace()
        self.view.table_layout=self.table_layout
        self.view.phantom=self.phantom
        self.mri_workspace = MRIWorkspace(self.view)
        self.acoustic_path=CONFIG_DIR/'acoustic_placement.json'
        acoustic_placement=AcousticPlacement.load(self.acoustic_path)
        self.acoustic=AcousticController(self,AcousticField(CONFIG_DIR/acoustic_placement.profile_name),acoustic_placement)
        self.mri_workspace.transducer_button.clicked.connect(self.open_acoustic_dialog)
        self.mri_workspace.acoustic_shift.setValue(round(self.acoustic.placement.beam_shift_mm))
        self.mri_workspace.acoustic_shift.valueChanged.connect(self.move_acoustic_beam)
        self.acoustic.update()
        self.mri_workspace.volume_changed.connect(self.phantom_mri_changed)
        layout.addWidget(self.mri_workspace, 1)
        self.scene_hint = label('Waiting for live tracking · lab equipment is an approximate static illustration.', 12, '#8ca3bc')
        layout.addWidget(self.scene_hint)
        self.phantom_status=label('Head phantom: waiting for a tracked Head rigid body · assumed dimensions / offset',11,'#d6a96a',wrap=True)
        layout.addWidget(self.phantom_status)
        layout.addWidget(label('RIGID BODIES', 12, '#67dfe1'))
        self.table = Qt.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['Body', 'Status', 'X (mm)', 'Y (mm)', 'Z (mm)', 'Fit error (mm)', 'Quaternion (x, y, z, w)'])
        self.table.horizontalHeader().setSectionResizeMode(Qt.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setMinimumSectionSize(85)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(Qt.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(Qt.QAbstractItemView.SelectRows)
        self.table.setMaximumHeight(140)
        self.table.setMinimumHeight(105)
        layout.addWidget(self.table)
        return right

    # ---------------------------------------------------------------- dialogs
    def browse_config(self, field, pattern):
        self.source_panel.browse_config(field, pattern)

    def open_phantom_dialog(self):
        from neuronav.ui.dialogs.phantom_dialog import open_phantom_dialog
        open_phantom_dialog(self)

    def open_table_layout_dialog(self):
        from neuronav.ui.dialogs.table_layout_dialog import open_table_layout_dialog
        open_table_layout_dialog(self)

    def open_acoustic_dialog(self):
        from neuronav.ui.dialogs.acoustic_dialog import open_acoustic_dialog
        open_acoustic_dialog(self)

    def open_reference_dialog(self):
        reference_plane_dialog.open_reference_dialog(self)

    def start_reference_capture(self, use_saved):
        reference_plane_dialog.start_reference_capture(self, use_saved)

    def advance_reference_capture(self, frame, fresh):
        reference_plane_dialog.advance_reference_capture(self, frame, fresh)

    # --------------------------------------------------------- state changes
    def phantom_mri_changed(self,volume):
        if volume is not None and self.phantom.use_mri_size and self.view.phantom_mri_dimensions is not None:
            self.phantom.adopt_mri_size(self.view.phantom_mri_dimensions)
            self.phantom.save(self.phantom_path)
        self.acoustic.update()

    def move_acoustic_beam(self,value):
        self.acoustic.placement.beam_shift_mm=float(value)
        self.acoustic.update()
        self.acoustic.placement.save(self.acoustic_path)

    def clear_reference(self):
        self.reference = None
        self.view.set_reference(None)
        self.coordinate_label.setText('Tracker coordinates · Y up · millimetres')
        self.fit_scene()

    def toggle_saved_geometry(self, checked):
        self.view.show_reference_geometry = checked
        self.view.set_reference(self.reference)

    def toggle_reference_markers(self, checked):
        self.view.hide_reference_markers = checked

    def toggle_lab_setup(self, checked):
        self.view.show_setup = checked

    def use_saved_reference(self):
        try:
            self.apply_reference(ReferencePlane.load(self.reference_path))
        except (ValueError, OSError, KeyError) as exc:
            Qt.QMessageBox.warning(self, 'Saved reference unavailable', str(exc))

    def apply_reference(self, reference):
        self.reference = reference
        self.view.set_reference(reference)
        self.coordinate_label.setText('Three-marker reference · Y normal · millimetres')

    def display_changed(self):
        self.view.show_markers = self.markers_check.isChecked()
        self.view.show_cameras = self.camera_check.isChecked()
        self.view.set_calibration_overlay(self.grid_check.isChecked())
        self.last_frame = -1

    # -------------------------------------------------------------- tracking
    def toggle_connection(self):
        if self.receiver:
            if self.capture:
                reference_plane_dialog.cancel_reference_capture(self, 'Capture cancelled because tracking stopped.')
            self.receiver.stop()
            self.receiver = None
            self.badge.setText('DISCONNECTED')
            self.connect_button.setText('Start tracking')
            self.message.setText('Disconnected. Live observations have been cleared.')
            self.view.points.setData(pos=np.empty((0, 3)))
            self.view.clear_dynamic()
            self.view.update_phantom(None,Models())
            self.acoustic.update(None,None,False)
            self.phantom_status.setText('Head phantom hidden: tracking disconnected')
            self.table.setRowCount(0)
            self.scene_hint.setText('Disconnected. Camera poses remain as reference geometry.')
            self.status_panel.clear()
        else:
            if self.source.currentIndex() == TrackingSourcePanel.API:
                self.receiver = MotiveTracking(self.install.text().strip(),
                    self.calibration.text().strip(), self.profile.text().strip())
                self.source_info.setText('Direct Motive API · local process pipe\nMotive application must be closed')
            else:
                self.receiver = NatNet(self.server.text().strip(), self.interface.text().strip(),
                                       self.group.text().strip(), self.port.value())
            self.receiver.start()
            self.last_frame = -1
            self.fitted = False
            self.last_rate_time = time.monotonic()
            self.last_rate_count = 0
            self.rate = 0
            self.connect_button.setText('Stop tracking')
            for name, value in self.source_panel.values().items():
                self.settings.setValue(name, value)
            self.settings.sync()
        self.source_panel.set_connected(self.receiver is not None)

    def refresh(self):
        if not self.receiver:
            return
        snap = self.receiver.snapshot()
        self.last_snapshot = snap
        frame, models = snap['frame'], snap['models']
        now = time.monotonic()
        age = now - frame.received if frame else None
        fresh = frame is not None and age < .5 and not snap['error']
        self.advance_reference_capture(frame, fresh)
        tracked = sum(b.valid for b in frame.bodies) if fresh else 0
        elapsed = now - self.last_rate_time
        if elapsed >= 1:
            self.rate = (snap['frames'] - self.last_rate_count) / elapsed
            self.last_rate_time, self.last_rate_count = now, snap['frames']
        self.metrics['Stream'].setText('LIVE' if fresh else ('STALE' if frame else 'WAITING'))
        self.metrics['Frame'].setText(str(frame.number) if frame else '—')
        self.metrics['Receive rate'].setText(f'{self.rate:.1f} frames/s')
        self.metrics['Frame age'].setText(f'{age*1000:.0f} ms' if age is not None else '—')
        self.metrics['Markers'].setText(str(len(frame.markers)) if fresh else '—')
        self.metrics['Rigid bodies'].setText(f'{tracked} / {len(models.names)} tracked')
        self.metrics['Cameras'].setText(str(len(models.cameras)))
        if snap['version']:
            ver = '.'.join(map(str, snap['version'][:2]))
            self.source_info.setText(f'{snap["server"]}\nNatNet {ver} · multicast · local receive age')
        elif snap.get('source') == 'api':
            self.source_info.setText(f'{snap["server"]}\nDirect cameras · no NatNet connection')
        self.badge.setText('LIVE TRACKING' if fresh and tracked else 'LIVE · NO VALID BODIES' if fresh else 'STREAM STALE' if frame else 'CONNECTING')
        if snap['error']:
            self.badge.setText('TRACKING ERROR')
            self.metrics['Stream'].setText('ERROR')
            self.message.setText(f'{snap["error"]}\nDecode errors: {snap["parse_errors"]}')
        elif not fresh and snap.get('source') == 'api':
            self.message.setText(snap.get('status', 'Starting tracking engine') if not frame else
                                 'No fresh API frames. Check camera connection and service status.')
        elif not fresh:
            self.message.setText('No fresh frames. In Motive: Settings → Streaming → NatNet enabled, loopback interface, Multicast. Keep Motive in Live mode.')
        elif tracked:
            self.message.setText('Live poses received. The head phantom follows its marker rigid body; other bodies use colored axes.')
        else:
            self.message.setText('Tracking is running. No valid rigid-body poses. Place the configured marker clusters in view of the Trio.')
        display_frame, display_models = reference_data(self.reference, frame, models)
        if fresh and self.phantom.enabled and not self.phantom.configured and self.reference and models.cameras:
            head_body=self.phantom.body(frame,models)
            if head_body is not None:
                self.phantom.align_upright(head_body,self.reference,models.cameras)
                try:
                    self.phantom.save(self.phantom_path)
                except OSError as exc:
                    self.phantom_status.setText('Phantom placement is active but could not be saved: '+str(exc))
        self.view.render_frame(frame, fresh, models)
        self.acoustic.update(frame,models,fresh)
        self.mri_workspace.acoustic_shift.setEnabled(self.acoustic.field.calibrated and self.acoustic.placement.mode=='manual'
                                                     and self.mri_workspace.volume is not None)
        self.update_phantom_status()
        if not self.fitted and models.cameras:
            self.fit_scene()
            self.fitted = True
        if self.camera_closeup_pending and models.cameras:
            self.focus_camera_bar()
            self.camera_closeup_pending = False
        if fresh:
            hidden = self.view.hidden_reference_count
            self.metrics['Markers'].setText(f'{len(frame.markers)} detected / {hidden} hidden')
            self.scene_hint.setText(f'{len(frame.markers)-hidden} visible markers  ·  {hidden} reference markers hidden  ·  {tracked} tracked bodies  ·  {len(models.cameras)} calibrated cameras')
        else:
            self.scene_hint.setText('Live observations hidden until fresh frames arrive.' if frame else 'Waiting for live tracking · lab equipment is an approximate static illustration.')
        self.update_table(display_frame, fresh, display_models)

    def update_phantom_status(self):
        if not self.phantom.enabled:
            self.phantom_status.setText('Head phantom hidden · Head phantom menu: marker offset and size')
        elif self.view.phantom_mri is None:
            self.phantom_status.setText('Head phantom: load the matching MRI to show its tracked 3D head model')
        elif self.view.phantom_pose is not None:
            bottom=self.view.phantom_pose[0]
            if self.reference:
                bottom=self.reference.points([bottom])[0]
            self.phantom_status.setText('MRI head model · tracked with assumed offset · Bottom center (mm): '+
                ', '.join(f'{v*1000:.1f}' for v in bottom)+' · Head phantom menu: edit offset / size')
        else:
            self.phantom_status.setText('Head phantom hidden: '+('waiting for fresh '+self.phantom.body_name+' pose' if self.phantom.configured else
                'needs table reference and live '+self.phantom.body_name+' pose to initialize assumed placement'))
        self.mri_workspace.registration.setText('MRI → markers: ASSUMED OFFSET' if self.view.phantom_mri is not None and
            self.phantom.configured and self.phantom.enabled else 'MRI ↔ tracker: NOT REGISTERED')

    def update_table(self, frame, fresh, models):
        by_id = {body.id: body for body in frame.bodies} if frame else {}
        ids = sorted(set(models.names) | set(by_id))
        self.table.setRowCount(len(ids))
        for row, ident in enumerate(ids):
            body = by_id.get(ident)
            valid = bool(body and body.valid and fresh)
            status = 'Tracked' if valid else 'Stale' if frame and not fresh else 'Not tracked'
            values = [models.names.get(ident, f'Body {ident}'), status]
            values += [f'{p*1000:.2f}' for p in body.position] if valid else ['—']*3
            values += [f'{body.mean_error_m*1000:.3f}', ', '.join(f'{v:.4f}' for v in body.quaternion)] if valid else ['—', '—']
            for col, value in enumerate(values):
                item = Qt.QTableWidgetItem(value)
                item.setForeground(QtGui.QColor('#70e5bc' if valid else '#d6a96a'))
                self.table.setItem(row, col, item)

    # ------------------------------------------------------------------ views
    def fit_scene(self):
        self.view.fit_scene(self.reference, self.last_snapshot)

    def reset_view(self):
        self.view.reset_view(self.reference, self.last_snapshot)

    def focus_camera_bar(self):
        if not self.last_snapshot:
            return
        self.view.focus_cameras(self.last_snapshot['models'].cameras)

    def save_diagnostics(self):
        if not self.last_snapshot:
            return
        path, _ = Qt.QFileDialog.getSaveFileName(self, 'Save tracking diagnostics',
                                                str(ROOT / 'tracking-diagnostics.json'), 'JSON (*.json)')
        if not path:
            return
        try:
            save_diagnostics(path, self.last_snapshot, self.reference)
            self.message.setText('Diagnostics saved.')
        except OSError as exc:
            Qt.QMessageBox.warning(self, 'Cannot save diagnostics', str(exc))

    def closeEvent(self, event):
        if self.mri_workspace.is_loading():
            self.mri_workspace.finish_before_close(self.close)
            event.ignore()
            return
        self.timer.stop()
        if self.receiver:
            self.receiver.stop()
        event.accept()
