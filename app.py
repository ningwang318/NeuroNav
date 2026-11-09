"""NeuroNav v0.2 — tracking workspace and ultrasound generator control."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

# The temporary GUI environment may retain only cached pyqtgraph modules.
# Prefer the recovered project-local copy when available.
if (Path(__file__).parent/'.runtime'/'pyqtgraph').exists():
    sys.path.insert(0,str(Path(__file__).parent/'.runtime'))

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as Qt
import pyqtgraph.opengl as gl
from pyqtgraph.opengl import shaders

from tracking import NatNet, Frame, Body, Models
from api_tracking import MotiveTracking, quaternion_from_matrix
from reference_plane import Capture, ReferencePlane, visible_markers
from camera_bar import camera_bar_meshes
from lab_setup import lab_setup_meshes
from mri_workspace import MRIWorkspace
from head_phantom import PhantomSettings,mri_head_preview
from fixed_head import FixedHead
from holder_tracking import solve_holder
from mri_3d import ClippedVolume,rgba_data
from table_layout import TableLayout
from acoustic_field import AcousticField,AcousticPlacement
from acoustic_scene import AcousticController
from ultrasound_panel import UltrasoundPanel
from robot_panel import RobotPanel
from ui_controls import CompactNumber


ROOT = Path(__file__).resolve().parent
COLORS = [(0.98, .36, .38, 1), (.32, .85, .59, 1), (.35, .61, 1, 1)]
# Motive's right-handed Y-up -> OpenGL's right-handed Z-up, in metres.
DISPLAY = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float)

BAR_SHADER = shaders.ShaderProgram('neuroNavBar', [
    shaders.VertexShader('''
        varying vec3 normal;
        void main() {
            normal = normalize(gl_NormalMatrix * gl_Normal);
            gl_FrontColor = gl_Color;
            gl_BackColor = gl_Color;
            gl_Position = ftransform();
        }
    '''),
    shaders.FragmentShader('''
        varying vec3 normal;
        void main() {
            vec3 n = normalize(normal);
            float key = max(dot(n, normalize(vec3(0.4, 0.7, 1.0))), 0.0);
            float fill = max(dot(n, normalize(vec3(-0.8, -0.3, 0.5))), 0.0);
            float light = 0.55 + 0.35 * key + 0.15 * fill;
            gl_FragColor = vec4(gl_Color.rgb * light, gl_Color.a);
        }
    '''),
])


def world(points):
    return np.asarray(points, dtype=float).reshape(-1, 3) @ DISPLAY.T


def rotation(quaternion):
    x, y, z, w = quaternion
    norm = np.linalg.norm(quaternion)
    if norm < 1e-8:
        return np.eye(3)
    x, y, z, w = np.asarray([x, y, z, w]) / norm
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ])


def reference_data(reference, frame, models):
    if reference is None:
        return frame, models
    cameras = [(name, tuple(reference.points([pos])[0]),
                quaternion_from_matrix(reference.orientation(rotation(quat))))
               for name, pos, quat in models.cameras]
    converted = None
    if frame:
        bodies = tuple(Body(b.id, tuple(reference.points([b.position])[0]),
                            quaternion_from_matrix(reference.orientation(rotation(b.quaternion))),
                            b.mean_error_m, b.valid) for b in frame.bodies)
        converted = Frame(frame.number, frame.received,
                          tuple(map(tuple, reference.points(frame.markers))), bodies, frame.unlabeled_count)
    return converted, Models(models.names, cameras)


def label(text, size=None, color=None, wrap=False):
    widget = Qt.QLabel(text)
    style = ''
    if size:
        style += f'font-size:{size}px;'
    if color:
        style += f'color:{color};'
    widget.setStyleSheet(style)
    widget.setWordWrap(wrap)
    return widget


class Workspace(gl.GLViewWidget):
    def viewMatrix(self):
        # Keep the saved ground normal upright, independent of tracker tilt.
        reference = getattr(self, 'reference', None)
        if reference is None:
            return super().viewMatrix()
        matrix = QtGui.QMatrix4x4()
        up = QtGui.QVector3D(*world([reference.basis[:,1]])[0])
        eye, center = self.cameraPosition(), self.opts['center']
        direction = (eye-center).normalized()
        if abs(QtGui.QVector3D.dotProduct(direction,up)) > .999:
            up = QtGui.QVector3D(*world([reference.basis[:,2]])[0])
        matrix.lookAt(eye,center,up)
        return matrix

    def __init__(self):
        super().__init__()
        self.setBackgroundColor('#0c1421')
        self.setCameraPosition(distance=3.2, elevation=12, azimuth=25)
        self.grid = gl.GLGridItem()
        self.grid.setSize(4, 4)
        self.grid.setSpacing(.1, .1)
        self.grid.setColor((56, 82, 107, 128))
        self.addItem(self.grid)
        self.dynamic = []
        self.camera_items = []
        self.setup_items = []
        self.setup_signature = None
        self.setup_bounds = []
        self.show_setup = True
        self.table_layout=TableLayout()
        self.reference_items = []
        self.origin_items = []
        self.reference = None
        self.show_calibration_overlay = False
        self.grid.setVisible(False)
        self.show_reference_geometry = False
        self.hide_reference_markers = True
        self.hidden_reference_count = 0
        self.camera_signature = None
        self.show_cameras = True
        self.show_markers = True
        self.phantom=PhantomSettings()
        self.phantom_items=[]
        self.phantom_signature=None
        self.phantom_pose=None
        self.fixed_head=None
        self.holder_observations=[]
        self.holder_last_write=0
        self.holder_message=''
        self.holder_result=None
        try:
            attachment=json.loads((ROOT/'config'/'holder_aperture.json').read_text(encoding='utf-8'))
            self.holder_aperture_offset=np.asarray(attachment['aperture_offset_holder_m'],float)
            if self.holder_aperture_offset.shape!=(3,) or not np.isfinite(self.holder_aperture_offset).all():
                raise ValueError('Invalid aperture offset')
        except (OSError,ValueError,KeyError,TypeError):
            self.holder_aperture_offset=None
        self.acoustic_body_name=None
        self.phantom_mri=None
        self.phantom_mri_preview=None
        self.phantom_mri_transform=None
        self.phantom_mri_dimensions=None
        self.phantom_mri_levels=None
        self.points = gl.GLScatterPlotItem(pos=np.empty((0, 3)), size=9,
                                          color=(.24, .88, .91, 1), pxMode=True)
        self.addItem(self.points)
        self.set_reference(None)

    def line(self, points, color, width=2, target=None):
        item = gl.GLLinePlotItem(pos=world(points), color=color, width=width,
                                 antialias=True, mode='lines')
        self.addItem(item)
        if target is not None:
            target.append(item)
        return item

    def add_axes(self, pos, rot, length, persistent=False, target=None):
        target = target if target is not None else (None if persistent else self.dynamic)
        for i, color in enumerate(COLORS):
            self.line([pos, pos + rot[:, i]*length], color, target=target)

    def update_cameras(self, cameras):
        signature = repr(cameras), self.show_cameras
        if signature == self.camera_signature:
            return
        self.camera_signature = signature
        for item in self.camera_items:
            self.removeItem(item)
        self.camera_items.clear()
        if not self.show_cameras:
            return
        meshes, camera_label = camera_bar_meshes(cameras)
        for mesh in meshes:
            item = gl.GLMeshItem(vertexes=world(mesh['vertices']), faces=mesh['faces'],
                                 color=mesh['color'], smooth=mesh['smooth'],
                                 shader=BAR_SHADER, glOptions='opaque')
            self.addItem(item)
            self.camera_items.append(item)
        if camera_label:
            position, name = camera_label
            text = gl.GLTextItem(pos=world([position])[0], text=name,
                                 color=(184, 201, 222, 255), font=QtGui.QFont('Segoe UI',10))
            self.addItem(text)
            self.camera_items.append(text)

    def render_frame(self, frame, fresh, models):
        self.update_cameras(models.cameras)
        self.update_setup(models.cameras)
        for item in self.dynamic:
            self.removeItem(item)
        self.dynamic.clear()
        self.update_holder(frame if fresh else None,models)
        points = frame.markers if frame and fresh and self.show_markers else ()
        points, self.hidden_reference_count = visible_markers(points, self.reference, self.hide_reference_markers)
        if self.holder_result is not None and len(points):
            distances=np.linalg.norm(points[:,None]-self.holder_result['markers'][None],axis=2)
            points=points[np.min(distances,axis=1)>.001]
        self.points.setData(pos=world(points))
        self.update_phantom(frame if fresh else None,models)
        if not frame or not fresh:
            return
        for body in frame.bodies:
            if not body.valid:
                continue
            if self.phantom.enabled and models.names.get(body.id,'').casefold()==self.phantom.body_name.casefold():
                continue
            if self.acoustic_body_name and models.names.get(body.id,'').casefold()==self.acoustic_body_name.casefold():
                continue
            pos = np.asarray(body.position)
            self.add_axes(pos, rotation(body.quaternion), .12)
            name = models.names.get(body.id, f'Body {body.id}')
            text = gl.GLTextItem(pos=world([pos + [0, .06, 0]])[0], text=name,
                                 color=(234, 240, 250, 255))
            self.addItem(text)
            self.dynamic.append(text)

    def update_holder(self,frame,models):
        self.holder_message=''
        self.holder_result=None
        if frame is None:
            return
        result=None
        try:
            crown=None
            if self.fixed_head and self.reference:
                crown=self.reference.origin+self.reference.basis@np.asarray(self.fixed_head.data['crown_marker_reference_m'])
            result=solve_holder(frame.markers,self.reference,models.cameras,crown)
            self.holder_message='Holder centre 0 located · geometric axes; beam not calibrated'
            self.holder_result=result
            if self.holder_aperture_offset is not None:
                result['aperture_centre']=result['centre']+result['orientation']@self.holder_aperture_offset
            if not self.holder_observations or self.holder_observations[-1]['frame']!=frame.number:
                self.holder_observations.append(dict(frame=frame.number,**{k:np.asarray(v).tolist() for k,v in result.items()}))
                self.holder_observations=self.holder_observations[-300:]
        except ValueError as exc:
            self.holder_message='Holder: '+str(exc)
        if time.monotonic()-self.holder_last_write>1:
            self.holder_last_write=time.monotonic()
            output=dict(captured_utc=datetime.now(timezone.utc).isoformat(),
                frame=frame.number,markers_tracker_m=frame.markers,cameras=models.cameras,
                status=self.holder_message,observations=self.holder_observations)
            try:
                destination=ROOT/'diagnostics'/'holder-live.json'
                destination.parent.mkdir(exist_ok=True)
                temporary=destination.with_suffix('.tmp')
                temporary.write_text(json.dumps(output,allow_nan=False),encoding='utf-8')
                temporary.replace(destination)
            except OSError:
                pass

    def update_phantom(self,frame,models):
        body=self.phantom.body(frame,models) if self.phantom.enabled and self.phantom.configured and self.phantom_mri is not None else None
        fixed_pose=self.fixed_head.pose(self.reference,self.phantom_mri) if self.fixed_head and self.phantom.enabled else None
        self.phantom_pose=fixed_pose
        if body is None and fixed_pose is None:
            for item in self.phantom_items:
                item.setVisible(False)
            return
        position,orientation=fixed_pose if fixed_pose is not None else self.phantom.pose(body)
        self.phantom_pose=(position,orientation)
        transform=np.eye(4)
        transform[:3,:3]=DISPLAY @ orientation
        transform[:3,3]=DISPLAY @ position
        height,width,depth=self.phantom_mri_dimensions
        scale=np.eye(4) if fixed_pose is not None else np.diag([self.phantom.width_mm/width,self.phantom.height_mm/height,self.phantom.depth_mm/depth,1])
        transform=transform @ scale @ self.phantom_mri_transform
        matrix=QtGui.QMatrix4x4(*transform.ravel().tolist())
        for item in self.phantom_items:
            item.setTransform(matrix)
            item.setVisible(True)

    def set_phantom_mri(self,volume,levels=None):
        if volume is self.phantom_mri and (volume is None or tuple(levels)==self.phantom_mri_levels):
            return
        if volume is not self.phantom_mri:
            valid=self.isValid()
            if valid:
                self.makeCurrent()
            try:
                from OpenGL import GL
                for item in self.phantom_items:
                    if valid:
                        texture=getattr(item,'texture',None)
                        if texture is not None:
                            GL.glDeleteTextures([texture])
                        for display_list in getattr(item,'lists',{}).values():
                            GL.glDeleteLists(display_list,1)
                    self.removeItem(item)
            finally:
                if valid:
                    self.doneCurrent()
            self.phantom_items=[]
            self.phantom_pose=None
            self.phantom_mri=volume
            if volume is None:
                self.phantom_mri_preview=self.phantom_mri_transform=self.phantom_mri_dimensions=None
                return
            self.phantom_mri_preview,self.phantom_mri_transform,self.phantom_mri_dimensions=mri_head_preview(volume)
        rgba=rgba_data(self.phantom_mri_preview,levels,True)
        rgba[...,3]=np.rint(rgba[...,3].astype(float)*.7).astype(np.uint8)
        if not self.phantom_items:
            item=ClippedVolume(rgba)
            item.cut_enabled=False
            item.setDepthValue(10)  # Draw after the opaque table and robot.
            valid=self.isValid()
            if valid:
                self.makeCurrent()
            try:
                self.addItem(item)
            finally:
                if valid:
                    self.doneCurrent()
            item.setVisible(False)
            self.phantom_items=[item]
        else:
            self.phantom_items[0].setData(rgba)
        self.phantom_mri_levels=tuple(levels)

    def update_setup(self, cameras):
        signature = repr(cameras), id(self.reference), self.show_setup,repr(self.table_layout)
        if signature == self.setup_signature:
            return
        self.setup_signature = signature
        for item in self.setup_items:
            self.removeItem(item)
        self.setup_items.clear()
        self.setup_bounds = []
        if self.show_setup and self.reference:
            self.grid.setSize(.4,.4)
            self.grid.setColor((56,82,107,55))
        else:
            self.grid.setSize(4,4)
            self.grid.setColor((56,82,107,128))
        if not self.show_setup:
            return
        meshes, labels, self.setup_bounds = lab_setup_meshes(cameras, self.reference,self.table_layout)
        for mesh in meshes:
            item = gl.GLMeshItem(vertexes=world(mesh['vertices']), faces=mesh['faces'],
                                 color=mesh['color'], smooth=mesh['smooth'],
                                 shader=BAR_SHADER, glOptions='opaque')
            self.addItem(item)
            self.setup_items.append(item)
        for pos, name in labels:
            item = gl.GLTextItem(pos=world([pos])[0], text=name,
                                 color=(182,198,217,255), font=QtGui.QFont('Segoe UI',10))
            self.addItem(item)
            self.setup_items.append(item)

    def set_reference(self, reference):
        self.reference = reference
        self.setup_signature = None
        for item in self.reference_items + self.origin_items:
            self.removeItem(item)
        self.reference_items.clear()
        self.origin_items.clear()
        origin = reference.origin if reference else np.zeros(3)
        basis = reference.basis if reference else np.eye(3)
        self.add_axes(origin, basis, .25, target=self.origin_items)
        for i, (text, color) in enumerate(zip('XYZ', COLORS)):
            item = gl.GLTextItem(pos=world([origin+basis[:, i]*.28])[0], text=text,
                                color=tuple(int(v*255) for v in color))
            self.addItem(item)
            self.origin_items.append(item)
        for item in self.origin_items:
            item.setVisible(self.show_calibration_overlay)
        # The scene remains in tracker space. Move/rotate only the reference
        # triad and its grid so the change of origin is visible directly.
        matrix = np.eye(4)
        matrix[:3, :3] = DISPLAY @ basis @ DISPLAY.T
        matrix[:3, 3] = DISPLAY @ origin
        self.grid.setTransform(QtGui.QMatrix4x4(*matrix.ravel().tolist()))
        if reference is None or not self.show_reference_geometry:
            return
        points = reference.markers
        self.line([points[0], points[1], points[1], points[2], points[2], points[0]],
                  (1, .72, .27, .8), target=self.reference_items)
        for i, point in enumerate(points):
            item = gl.GLTextItem(pos=world([point])[0], text=f'P{i+1} (saved)',
                                 color=(255, 197, 98, 255), font=QtGui.QFont('Segoe UI', 10))
            self.addItem(item)
            self.reference_items.append(item)

    def fit(self, points):
        points = world(points)
        if not len(points):
            points = np.array([[0, 0, 0]])
        center = (points.min(axis=0) + points.max(axis=0)) / 2
        extent = float(np.linalg.norm(np.ptp(points, axis=0)))
        self.opts['center'] = QtGui.QVector3D(*center)
        self.setCameraPosition(distance=max(1.0, extent*1.8), elevation=12, azimuth=25)


class MainWindow(Qt.QMainWindow):
    def open_robot_panel(self):
        panel = RobotPanel(self)
        panel.exec()
        panel.deleteLater()

    def __init__(self, auto_connect=False):
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
        self.reference_path = ROOT/'config'/'reference_plane.json'
        self.phantom_path=ROOT/'config'/'head_phantom.json'
        self.table_layout_path=ROOT/'config'/'table_layout.json'
        try:
            self.table_layout=TableLayout.load(self.table_layout_path)
        except (ValueError,TypeError,KeyError,OSError):
            self.table_layout=TableLayout()
        try:
            self.phantom=PhantomSettings.load(self.phantom_path)
        except (ValueError,TypeError,OSError):
            self.phantom=PhantomSettings()
        self.settings = QtCore.QSettings(str(ROOT / 'settings.ini'), QtCore.QSettings.IniFormat)
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
        phantom_menu.addAction('Capture crown marker · save head relative to table…',self.open_fixed_head_dialog)
        tracking_menu = self.menuBar().addMenu('Tracking')
        robot_menu = self.menuBar().addMenu('Robot')
        robot_menu.addAction('xArm…', self.open_robot_panel)
        self.lab_setup_action = view_menu.addAction('Show table / breadboard')
        self.lab_setup_action.setCheckable(True)
        self.lab_setup_action.setChecked(True)
        self.lab_setup_action.toggled.connect(self.toggle_lab_setup)
        root = Qt.QWidget()
        self.setCentralWidget(root)
        outer = Qt.QVBoxLayout(root)
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(8)
        header = Qt.QHBoxLayout()
        title = Qt.QVBoxLayout()
        title.setSpacing(2)
        title.addWidget(label('NEURONAV', 25, '#e9f2ff'))
        self.sonication_banner = label('Session idle', 12, '#67dfe1', wrap=True)
        header.addLayout(title, 1)
        self.badge = label('DISCONNECTED', 12, '#f6bb6a')
        outer.addLayout(header)
        split = Qt.QSplitter()
        outer.addWidget(split, 1)
        sidebar = Qt.QWidget()
        sidebar.setMaximumWidth(390)
        sidebar.setMinimumWidth(300)
        side = Qt.QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 16, 0)
        side.setSpacing(6)
        source_settings = Qt.QWidget()
        source_layout = Qt.QVBoxLayout(source_settings)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(label('TRACKING SOURCE', 12, '#67dfe1'))
        self.source = Qt.QComboBox()
        self.source.addItems(['Standalone · Motive API', 'Network · NatNet'])
        source_layout.addWidget(self.source)
        self.source_forms = Qt.QStackedWidget()
        self.source_forms.setSizePolicy(Qt.QSizePolicy.Preferred, Qt.QSizePolicy.Maximum)
        api_form_widget = Qt.QWidget()
        api_form = Qt.QFormLayout(api_form_widget)
        api_form.setContentsMargins(0, 0, 0, 0)
        self.install = Qt.QLineEdit(self.settings.value('api_install', r'C:\Program Files\OptiTrack\Motive'))
        self.calibration = Qt.QLineEdit(self.settings.value('api_calibration', str(ROOT/'config'/'System Calibration.mcal')))
        self.profile = Qt.QLineEdit(self.settings.value('api_profile', str(ROOT/'config'/'MotiveProfile.motive')))
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
        self.server = Qt.QLineEdit(self.settings.value('server', '127.0.0.1'))
        self.interface = Qt.QLineEdit(self.settings.value('interface', '127.0.0.1'))
        self.group = Qt.QLineEdit(self.settings.value('group', '239.255.42.99'))
        self.port = Qt.QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(self.settings.value('port', 1511)))
        for name, field in [('Motive server', self.server), ('Local interface', self.interface),
                             ('Multicast group', self.group), ('Data port', self.port)]:
            form.addRow(name, field)
        self.source_forms.addWidget(network_widget)
        source_layout.addWidget(self.source_forms)
        self.source.currentIndexChanged.connect(self.source_forms.setCurrentIndex)
        self.connect_button = Qt.QCheckBox('Tracking OFF · Start tracking')
        self.connect_button.setStyleSheet('''
            QCheckBox::indicator { width:36px; height:18px; border-radius:9px;
                background:#344355; border:1px solid #607386; }
            QCheckBox::indicator:checked { background:#22767d; border-color:#67dfe1; }
        ''')
        self.connect_button.clicked.connect(self.toggle_connection)
        header.addWidget(self.connect_button)
        self.message = label('Connect to open the live tracking space.', 12, '#b5c7dc', wrap=True)
        self.source_info = label('Direct camera access · Motive application must be closed', 11, '#8ca3bc', wrap=True)
        source_layout.addWidget(self.source_info)
        self.ultrasound = UltrasoundPanel()
        self.ultrasound.shutdown_finished.connect(self.close)
        self.ultrasound.worker.session.connect(
            lambda active, message: self.sonication_banner.setText(message))
        side.addWidget(self.ultrasound)
        self.tracking_dialog = Qt.QDialog(self)
        self.tracking_dialog.setWindowTitle('Tracking settings, status and display')
        self.tracking_dialog.resize(570, 760)
        details = self.tracking_dialog
        detail_side = Qt.QVBoxLayout(details)
        detail_side.addWidget(label('Standalone optical tracking / v0.2', 12, '#8ca3bc'))
        tracking_menu.addAction('Settings, status and display…', self.open_tracking_dialog)
        detail_side.addWidget(source_settings)
        detail_side.addWidget(label('LIVE STATUS', 12, '#67dfe1'))
        stats = Qt.QGridLayout()
        self.metrics = {}
        for row, name in enumerate(['Stream', 'Frame', 'Receive rate', 'Frame age', 'Markers', 'Rigid bodies', 'Cameras']):
            stats.addWidget(label(name, 12, '#8ca3bc'), row, 0)
            self.metrics[name] = label('—', 12, '#e9f2ff')
            stats.addWidget(self.metrics[name], row, 1)
        detail_side.addLayout(stats)
        detail_side.addWidget(label('DISPLAY', 12, '#67dfe1'))
        self.markers_check = Qt.QCheckBox('Observed markers')
        self.markers_check.setChecked(True)
        self.camera_check = Qt.QCheckBox('Trio 3 camera bar')
        self.camera_check.setChecked(True)
        self.grid_check = Qt.QCheckBox('Calibration grid and XYZ')
        self.grid_check.setChecked(False)
        for check in [self.markers_check, self.camera_check, self.grid_check]:
            check.toggled.connect(self.display_changed)
            detail_side.addWidget(check)
        buttons = Qt.QHBoxLayout()
        fit = Qt.QPushButton('Fit scene')
        fit.clicked.connect(self.fit_scene)
        reset = Qt.QPushButton('Reset view')
        reset.clicked.connect(self.reset_view)
        buttons.addWidget(fit)
        buttons.addWidget(reset)
        camera_view = Qt.QPushButton('Camera view')
        camera_view.clicked.connect(self.focus_camera_bar)
        buttons.addWidget(camera_view)
        detail_side.addLayout(buttons)
        phantom_settings=Qt.QPushButton('Head phantom settings…')
        phantom_settings.clicked.connect(self.open_phantom_dialog)
        detail_side.addWidget(phantom_settings)
        save = Qt.QPushButton('Save diagnostics…')
        save.clicked.connect(self.save_diagnostics)
        detail_side.addWidget(save)
        detail_side.addWidget(label('Drag: orbit   ·   Wheel: zoom\nCtrl + drag: pan', 11, '#8ca3bc'))
        detail_side.addWidget(label('Camera symbols show calibrated poses. The grid is a display reference; it does not define calibrated tracking coverage.', 11, '#8ca3bc', wrap=True))
        close_tracking = Qt.QDialogButtonBox(Qt.QDialogButtonBox.Close)
        close_tracking.rejected.connect(details.hide)
        detail_side.addWidget(close_tracking)
        split.addWidget(sidebar)
        right = Qt.QWidget()
        layout = Qt.QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toolbar = Qt.QHBoxLayout()
        toolbar.addWidget(label('NAVIGATION AND MRI', 13, '#e9f2ff'))
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.view = Workspace()
        self.view.table_layout=self.table_layout
        self.view.phantom=self.phantom
        self.fixed_head_path=ROOT/'config'/'fixed_head.json'
        try:
            self.view.fixed_head=FixedHead.load(self.fixed_head_path)
        except (OSError,ValueError,KeyError,TypeError):
            self.view.fixed_head=None
        self.mri_workspace = MRIWorkspace(self.view)
        self.acoustic_path=ROOT/'config'/'acoustic_placement.json'
        acoustic_placement=AcousticPlacement.load(self.acoustic_path)
        self.acoustic=AcousticController(self,AcousticField(ROOT/'config'/acoustic_placement.profile_name),acoustic_placement)
        self.mri_workspace.transducer_button.clicked.connect(self.open_acoustic_dialog)
        self.acoustic.update()
        self.mri_workspace.volume_changed.connect(self.phantom_mri_changed)
        layout.addWidget(self.mri_workspace, 1)
        self.scene_hint = label('Waiting for live tracking.', 12, '#8ca3bc', wrap=True)
        self.phantom_status=label('Head phantom: waiting for a tracked Head rigid body · assumed dimensions / offset',11,'#d6a96a',wrap=True)
        messages = Qt.QWidget()
        message_layout = Qt.QVBoxLayout(messages)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.addStretch()
        message_layout.addWidget(self.ultrasound.status)
        message_layout.addWidget(self.sonication_banner)
        message_layout.addWidget(self.badge)
        message_layout.addWidget(self.message)
        message_layout.addWidget(self.mri_workspace.metadata)
        message_layout.addWidget(self.mri_workspace.acoustic_status)
        message_layout.addWidget(self.mri_workspace.skull_status)
        message_layout.addWidget(self.scene_hint)
        message_layout.addWidget(self.phantom_status)
        message_area = Qt.QScrollArea()
        message_area.setFrameShape(Qt.QFrame.NoFrame)
        message_area.setWidgetResizable(True)
        message_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        message_area.setWidget(messages)
        side.addWidget(message_area, 1)
        rigid_title=label('RIGID BODIES', 12, '#67dfe1')
        layout.addWidget(rigid_title)
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
        rigid_title.hide()
        self.table.hide()
        coordinates_action=view_menu.addAction('Show rigid-body coordinates')
        coordinates_action.setCheckable(True)
        coordinates_action.toggled.connect(rigid_title.setVisible)
        coordinates_action.toggled.connect(self.table.setVisible)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 1000])
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(33)
        if auto_connect:
            QtCore.QTimer.singleShot(150, self.toggle_connection)

    def open_tracking_dialog(self):
        self.tracking_dialog.show()
        self.tracking_dialog.raise_()
        self.tracking_dialog.activateWindow()

    def browse_config(self, field, pattern):
        path, _ = Qt.QFileDialog.getOpenFileName(self, 'Select tracking configuration', field.text(), pattern)
        if path:
            field.setText(path)
            field.setToolTip(path)

    def open_phantom_dialog(self):
        from phantom_dialog import open_phantom_dialog
        open_phantom_dialog(self)

    def open_fixed_head_dialog(self):
        from fixed_head_dialog import open_fixed_head_dialog
        open_fixed_head_dialog(self)

    def open_table_layout_dialog(self):
        from copy import deepcopy
        dialog=Qt.QDialog(self)
        dialog.setWindowTitle('Table size and placement')
        layout=Qt.QVBoxLayout(dialog)
        layout.addWidget(label('Photo-estimated illustration. Center and top height use the saved\n'
            'table-marker reference coordinates. Robot offsets use the table frame.',12,'#d6a96a',wrap=True))
        form=Qt.QFormLayout()
        fields={}
        for name,title in [('width_mm','Width'),('depth_mm','Depth'),('thickness_mm','Thickness'),
                           ('center_x_mm','Center X'),('center_z_mm','Center Z'),('top_y_mm','Top Y'),
                           ('yaw_deg','Table yaw'),('robot_x_mm','Robot base X from table center'),
                           ('robot_z_mm','Robot base Z from table center')]:
            field=CompactNumber()
            field.setRange(-3000,3000)
            field.setDecimals(2)
            field.setSuffix('°' if name=='yaw_deg' else ' mm')
            field.setValue(getattr(self.table_layout,name))
            fields[name]=field
            form.addRow(title,field)
        layout.addLayout(form)
        status=label('Apply saves this appearance geometry; tracking and head-marker offsets are unchanged.',11,wrap=True)
        layout.addWidget(status)
        buttons=Qt.QDialogButtonBox(Qt.QDialogButtonBox.Apply|Qt.QDialogButtonBox.Cancel)
        def apply():
            try:
                draft=deepcopy(self.table_layout)
                for name,field in fields.items():
                    setattr(draft,name,field.value())
                draft.save(self.table_layout_path)
                self.table_layout=self.view.table_layout=draft
                cameras=self.last_snapshot['models'].cameras if self.last_snapshot else []
                self.view.update_setup(cameras)
                self.fit_scene()
                dialog.accept()
            except (ValueError,OSError) as exc:
                status.setText(str(exc))
        buttons.button(Qt.QDialogButtonBox.Apply).clicked.connect(apply)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def phantom_mri_changed(self,volume):
        if volume is not None and self.phantom.use_mri_size and self.view.phantom_mri_dimensions is not None:
            self.phantom.adopt_mri_size(self.view.phantom_mri_dimensions)
            self.phantom.save(self.phantom_path)
        self.view.update_phantom(None,Models())
        self.acoustic.update()

    def open_acoustic_dialog(self):
        from acoustic_dialog import open_acoustic_dialog
        open_acoustic_dialog(self)

    def clear_reference(self):
        self.reference = None
        self.view.set_reference(None)
        self.view.update_phantom(None,Models())
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
        self.view.update_phantom(None,Models())

    def open_reference_dialog(self):
        if self.reference_dialog:
            self.reference_dialog.raise_()
            return
        dialog = Qt.QDialog(self)
        self.reference_dialog = dialog
        dialog.setWindowTitle('Three-marker reference plane')
        dialog.resize(600, 390)
        layout = Qt.QVBoxLayout(dialog)
        layout.addWidget(label('Keep exactly three markers visible and stationary during the two-second capture.\n\nOrigin: triangle centre. X: P1 → P2. Y: plane normal. Z: X × Y. Marker identities are determined from the unequal triangle side lengths.', 13, '#d7e4f3', wrap=True))
        layout.addWidget(label('This sets navigation coordinates. It does not recalibrate the cameras or establish MRI registration. The reference remains fixed after capture; it does not follow moving markers.', 12, '#f6bb6a', wrap=True))
        self.reference_status = label('Saved geometry available. Recalibrate to use it in this session.' if self.reference_path.exists() else 'No saved geometry yet.', 12, '#8ca3bc', wrap=True)
        layout.addWidget(self.reference_status)
        self.flip_normal = Qt.QCheckBox('Reverse plane normal (Y axis)')
        layout.addWidget(self.flip_normal)
        self.capture_progress = Qt.QProgressBar()
        self.capture_progress.setRange(0, 100)
        self.capture_progress.setValue(0)
        layout.addWidget(self.capture_progress)
        self.capture_new = Qt.QPushButton('Capture current three markers and save')
        self.capture_saved = Qt.QPushButton('Recalibrate from saved triangle')
        self.capture_saved.setEnabled(self.reference_path.exists())
        self.capture_new.clicked.connect(lambda: self.start_reference_capture(False))
        self.capture_saved.clicked.connect(lambda: self.start_reference_capture(True))
        layout.addWidget(self.capture_new)
        layout.addWidget(self.capture_saved)
        close = Qt.QPushButton('Close')
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        def closed():
            self.capture = None
            self.reference_dialog = None
        dialog.finished.connect(closed)
        dialog.show()

    def start_reference_capture(self, use_saved):
        try:
            if not self.receiver:
                raise ValueError('Start tracking before capturing a reference plane.')
            snap = self.receiver.snapshot()
            frame = snap['frame']
            if not frame or time.monotonic()-frame.received >= .5 or snap['error']:
                raise ValueError('A healthy live tracking frame is required.')
            expected = ReferencePlane.load(self.reference_path) if use_saved else None
            self.capture = Capture(expected, self.flip_normal.isChecked())
            self.capture.add(frame.markers, frame.number)
            self.capture_after = frame.received
            self.capture_started = time.monotonic()
            self.capture_progress.setValue(0)
            self.reference_status.setText('Capturing… keep all three markers stationary.')
            self.capture_new.setEnabled(False)
            self.capture_saved.setEnabled(False)
        except (ValueError, OSError, KeyError) as exc:
            self.capture = None
            self.reference_status.setText(str(exc))

    def advance_reference_capture(self, frame, fresh):
        if not self.capture:
            return
        try:
            if not fresh:
                raise ValueError('Tracking became stale. Reference capture cancelled; retry when tracking is live.')
            for sample in self.receiver.frames_since(self.capture_after):
                self.capture.add(sample.markers, sample.number)
                self.capture_after = sample.received
            elapsed = time.monotonic()-self.capture_started
            self.capture_progress.setValue(min(100, int(elapsed*50)))
            if elapsed < 2:
                return
            reference = self.capture.finish(dict(source=self.source.currentText(),
                calibration=self.calibration.text(), profile=self.profile.text()))
            reference.save(self.reference_path)
            self.apply_reference(reference)
            edges = np.linalg.norm(reference.markers[[0, 0, 1]]-reference.markers[[1, 2, 2]], axis=1)*1000
            self.reference_status.setText(f'Reference saved and applied. {len(self.capture.samples)} samples.\nEdges: {edges[0]:.2f}, {edges[1]:.2f}, {edges[2]:.2f} mm.\nThe main XYZ axes and grid now use this plane. Marker local Y coordinates are zero.')
            self.capture = None
        except (ValueError, OSError) as exc:
            self.capture = None
            self.reference_status.setText(f'Capture rejected: {exc}')
        if self.capture is None:
            self.capture_new.setEnabled(True)
            self.capture_saved.setEnabled(self.reference_path.exists())

    def display_changed(self):
        self.view.show_markers = self.markers_check.isChecked()
        self.view.show_cameras = self.camera_check.isChecked()
        self.view.grid.setVisible(self.grid_check.isChecked())
        self.view.show_calibration_overlay = self.grid_check.isChecked()
        for item in self.view.origin_items:
            item.setVisible(self.view.show_calibration_overlay)
        self.last_frame = -1

    def toggle_connection(self):
        if self.receiver:
            if self.capture:
                self.capture = None
                if self.reference_dialog:
                    self.reference_status.setText('Capture cancelled because tracking stopped.')
                    self.capture_new.setEnabled(True)
                    self.capture_saved.setEnabled(self.reference_path.exists())
            self.receiver.stop()
            self.receiver = None
            self.badge.setText('DISCONNECTED')
            self.message.setText('Disconnected. Live observations have been cleared.')
            self.view.points.setData(pos=np.empty((0, 3)))
            for item in self.view.dynamic:
                self.view.removeItem(item)
            self.view.dynamic.clear()
            self.view.update_phantom(None,Models())
            self.acoustic.update(None,None,False)
            self.phantom_status.setText('Head phantom fixed to saved table reference · tracking disconnected' if self.view.phantom_pose is not None else 'Head phantom hidden: tracking disconnected')
            self.table.setRowCount(0)
            self.scene_hint.setText('Disconnected. Camera poses remain as reference geometry.')
            for metric in self.metrics.values():
                metric.setText('—')
        else:
            if self.source.currentIndex() == 0:
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
            for name, value in [('server', self.server.text()), ('interface', self.interface.text()),
                                ('group', self.group.text()), ('port', self.port.value()),
                                ('api_install', self.install.text()), ('api_calibration', self.calibration.text()),
                                ('api_profile', self.profile.text())]:
                self.settings.setValue(name, value)
            self.settings.sync()
        self.connect_button.setChecked(self.receiver is not None)
        self.connect_button.setText('Tracking ON · Stop tracking' if self.receiver else 'Tracking OFF · Start tracking')
        for field in [self.server, self.interface, self.group, self.port]:
            field.setEnabled(self.receiver is None)
        self.source.setEnabled(self.receiver is None)
        self.source_forms.setEnabled(self.receiver is None)

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
        if not self.phantom.enabled:
            self.phantom_status.setText('Head phantom hidden · Head phantom menu: marker offset and size')
        elif self.view.phantom_mri is None:
            self.phantom_status.setText('Head phantom: load the matching MRI to show its tracked 3D head model')
        elif self.view.phantom_pose is not None:
            bottom=self.view.phantom_pose[0]
            if self.reference:
                bottom=self.reference.points([bottom])[0]
            mode='FIXED to table · crown marker may be removed · assumed orientation / estimated MRI crown' if self.view.fixed_head else 'tracked with assumed offset'
            self.phantom_status.setText('MRI head model · '+mode+' · Bottom center (mm): '+
                ', '.join(f'{v*1000:.1f}' for v in bottom)+' · Head phantom menu: edit offset / size')
        else:
            self.phantom_status.setText('Head phantom hidden: '+('waiting for fresh '+self.phantom.body_name+' pose' if self.phantom.configured else
                'needs table reference and live '+self.phantom.body_name+' pose to initialize assumed placement'))
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
            self.scene_hint.setText('Live observations hidden until fresh frames arrive.' if frame else 'Waiting for live tracking.')
        self.update_table(display_frame, fresh, display_models)

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

    def fit_scene(self):
        points = [tuple(self.reference.origin) if self.reference else (0, 0, 0)]
        points += list(self.view.setup_bounds)
        if self.last_snapshot:
            points += [c[1] for c in self.last_snapshot['models'].cameras]
            frame = self.last_snapshot['frame']
            if frame and time.monotonic() - frame.received < .5:
                points += list(frame.markers)
                points += [b.position for b in frame.bodies if b.valid]
        self.view.fit(points)
        if self.reference and self.view.show_setup and self.last_snapshot and self.last_snapshot['models'].cameras:
            table_center,table_basis=self.view.table_layout.frame(self.reference)
            right,normal,back=table_basis[:,0],table_basis[:,1],table_basis[:,2]
            eye=world([.9*back-.55*right+.65*normal])[0]
            eye/=np.linalg.norm(eye)
            # Fit projected equipment bounds, rather than a diagonal sphere,
            # so the complete rig fills this wide viewport without cropping.
            corners=[]
            if len(self.view.setup_bounds)==2:
                low,high=self.view.setup_bounds
                corners=[(x,y,z) for x in [low[0],high[0]]
                         for y in [low[1],high[1]] for z in [low[2],high[2]]]
            projected=world(points+corners)
            # The breadboard's top centre is the navigation view's focal point.
            center=world([table_center])[0]
            self.view.opts['center']=QtGui.QVector3D(*center)
            horizontal=np.cross(world([normal])[0],eye)
            horizontal/=np.linalg.norm(horizontal)
            vertical=np.cross(eye,horizontal)
            offsets=projected-center
            tan_horizontal=np.tan(np.radians(self.view.opts['fov']/2))
            tan_vertical=tan_horizontal*self.view.height()/max(1,self.view.width())
            depth=offsets @ eye
            distance=float(max(np.max(depth+1.12*np.abs(offsets @ horizontal)/tan_horizontal),
                               np.max(depth+1.12*np.abs(offsets @ vertical)/tan_vertical)))
            self.view.setCameraPosition(elevation=float(np.degrees(np.arcsin(eye[2]))),
                                        azimuth=float(np.degrees(np.arctan2(eye[1],eye[0]))),
                                        distance=max(1.4,distance))

    def reset_view(self):
        if self.reference and self.view.show_setup:
            self.fit_scene()
            return
        self.view.opts['center'] = QtGui.QVector3D(*world([self.reference.origin if self.reference else (0,0,0)])[0])
        self.view.setCameraPosition(distance=3.2, elevation=12, azimuth=25)

    def focus_camera_bar(self):
        if not self.last_snapshot or not self.last_snapshot['models'].cameras:
            return
        cameras = self.last_snapshot['models'].cameras
        self.view.fit([camera[1] for camera in cameras])
        self.view.setCameraPosition(distance=1.0, elevation=-15, azimuth=16)

    def save_diagnostics(self):
        if not self.last_snapshot:
            return
        path, _ = Qt.QFileDialog.getSaveFileName(self, 'Save tracking diagnostics',
                                                str(ROOT / 'tracking-diagnostics.json'), 'JSON (*.json)')
        if not path:
            return
        snap = self.last_snapshot
        frame = snap['frame']
        data = dict(server=snap['server'], version=snap['version'], frames_received=snap['frames'],
                    decode_errors=snap['parse_errors'], error=snap['error'],
                    body_names=snap['models'].names, cameras=snap['models'].cameras,
                    frame_number=frame.number if frame else None,
                    frame_age_ms=(time.monotonic()-frame.received)*1000 if frame else None,
                    observed_markers=len(frame.markers) if frame else 0,
                    units='metres; quaternion xyzw; Motive Y-up')
        data['display_reference'] = ('three-marker plane' if self.reference else 'tracker')
        if self.reference:
            data['tracker_to_reference'] = dict(origin_m=self.reference.origin.tolist(),
                                                basis=self.reference.basis.tolist())
        try:
            Path(path).write_text(json.dumps(data, indent=2), encoding='utf-8')
            self.message.setText('Diagnostics saved.')
        except OSError as exc:
            Qt.QMessageBox.warning(self, 'Cannot save diagnostics', str(exc))

    def closeEvent(self, event):
        # Stop sonication before waiting for unrelated MRI loading to finish.
        if not self.ultrasound.shutdown():
            event.ignore()
            return
        if self.mri_workspace.is_loading():
            self.mri_workspace.finish_before_close(self.close)
            event.ignore()
            return
        self.timer.stop()
        if self.receiver:
            self.receiver.stop()
        event.accept()


STYLE = '''
QWidget { background:#101c2c; color:#d7e4f3; font-family:'Segoe UI'; font-size:12px; }
QLabel { background:transparent; }
QLineEdit, QSpinBox, QDoubleSpinBox { background:#182a3d; border:1px solid #2a4057; border-radius:5px; padding:7px; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color:#7c91a6; }
QPushButton { background:#21364b; border:1px solid #304b66; border-radius:6px; padding:9px; }
QPushButton:hover { background:#2c4760; }
QPushButton#primary { background:#22767d; border-color:#3a9fa7; color:white; font-weight:600; }
QLabel#badge { border:1px solid #34536d; border-radius:12px; padding:9px 14px; }
QTableWidget { background:#101c2c; alternate-background-color:#17273a; border:1px solid #294058; gridline-color:#23374e; }
QHeaderView::section { background:#1b2e43; color:#9fb5cc; border:0; padding:8px; }
QCheckBox { spacing:8px; padding:3px; }
QSplitter::handle { background:#24364b; width:1px; }
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true', help='Open without tracking (the default)')
    parser.add_argument('--start-tracking', action='store_true', help='Start the configured tracking service on opening')
    parser.add_argument('--use-saved-reference', action='store_true', help='Explicitly apply the saved XYZ reference on opening')
    parser.add_argument('--camera-close-up', action='store_true', help='Open a close-up view of the camera bar')
    parser.add_argument('--mri-file', help='Load a local NIfTI or FreeSurfer MGH/MGZ volume')
    parser.add_argument('--mri-demo', action='store_true', help='Show a clearly labelled synthetic MRI phantom')
    parser.add_argument('--smoke-seconds', type=float, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    # pyqtgraph caches shader programs globally. Share resources with the
    # separate transducer-preview dialog as well as the main scene widgets.
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts)
    app = Qt.QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = MainWindow(auto_connect=args.start_tracking)
    window.camera_closeup_pending = args.camera_close_up
    if args.use_saved_reference or window.view.fixed_head is not None:
        window.use_saved_reference()
    if args.mri_file:
        window.mri_workspace.load_path(args.mri_file)
    elif args.mri_demo:
        from mri_volume import demo_volume
        window.mri_workspace.set_volume(demo_volume())
    elif window.view.fixed_head is not None:
        window.mri_workspace.load_path(window.view.fixed_head.data['mri_source'])
    window.showMaximized()
    if args.smoke_seconds:
        QtCore.QTimer.singleShot(int(args.smoke_seconds * 1000), window.close)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
