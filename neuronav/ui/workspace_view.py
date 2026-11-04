"""Live tracking scene: cameras, markers, rigid bodies, lab illustration, head phantom.

The scene stays in tracker space (metres) and is drawn through DISPLAY (Y-up -> Z-up).
"""
import time

import numpy as np
from PySide6 import QtGui
import pyqtgraph.opengl as gl
from pyqtgraph.opengl import shaders

from neuronav.core.frames import DISPLAY, DISPLAY4, name_matches, world, rotation
from neuronav.core.head_phantom import PhantomSettings, mri_head_preview, preview_to_tracker
from neuronav.core.table_layout import TableLayout
from neuronav.tracking.reference_plane import visible_markers
from neuronav.ui.camera_bar import camera_bar_meshes
from neuronav.ui.gl_utils import delete_gl_resources, gl_context
from neuronav.ui.lab_setup import lab_setup_meshes
from neuronav.ui.mri_3d import ClippedVolume, rgba_data


COLORS = [(0.98, .36, .38, 1), (.32, .85, .59, 1), (.35, .61, 1, 1)]

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
        self.clear_dynamic()
        points = frame.markers if frame and fresh and self.show_markers else ()
        points, self.hidden_reference_count = visible_markers(points, self.reference, self.hide_reference_markers)
        self.points.setData(pos=world(points))
        self.update_phantom(frame if fresh else None,models)
        if not frame or not fresh:
            return
        for body in frame.bodies:
            if not body.valid:
                continue
            if self.phantom.enabled and name_matches(models,body.id,self.phantom.body_name):
                continue
            if self.acoustic_body_name and name_matches(models,body.id,self.acoustic_body_name):
                continue
            pos = np.asarray(body.position)
            self.add_axes(pos, rotation(body.quaternion), .12)
            name = models.names.get(body.id, f'Body {body.id}')
            text = gl.GLTextItem(pos=world([pos + [0, .06, 0]])[0], text=name,
                                 color=(234, 240, 250, 255))
            self.addItem(text)
            self.dynamic.append(text)

    def clear_dynamic(self):
        for item in self.dynamic:
            self.removeItem(item)
        self.dynamic.clear()

    def update_phantom(self,frame,models):
        body=self.phantom.body(frame,models) if self.phantom.enabled and self.phantom.configured and self.phantom_mri is not None else None
        self.phantom_pose=None
        if body is None:
            for item in self.phantom_items:
                item.setVisible(False)
            return
        position,orientation=self.phantom.pose(body)
        self.phantom_pose=(position,orientation)
        transform=DISPLAY4 @ self.preview_to_tracker()
        matrix=QtGui.QMatrix4x4(*transform.ravel().tolist())
        for item in self.phantom_items:
            item.setTransform(matrix)
            item.setVisible(True)

    def preview_to_tracker(self):
        """Head-model preview voxel -> tracker metres; None without a tracked head and MRI."""
        if self.phantom_pose is None or self.phantom_mri is None:
            return None
        return preview_to_tracker(self.phantom_pose,self.phantom,self.phantom_mri_dimensions,
                                  self.phantom_mri_transform)

    def set_phantom_mri(self,volume,levels=None):
        if volume is self.phantom_mri and (volume is None or tuple(levels)==self.phantom_mri_levels):
            return
        if volume is not self.phantom_mri:
            valid=self.isValid()
            with gl_context(self, restore_previous=False, skip_if_current=False):
                for item in self.phantom_items:
                    if valid:
                        delete_gl_resources(item)
                    self.removeItem(item)
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
            with gl_context(self, restore_previous=False, skip_if_current=False):
                self.addItem(item)
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

    def set_calibration_overlay(self, visible):
        self.grid.setVisible(visible)
        self.show_calibration_overlay = visible
        for item in self.origin_items:
            item.setVisible(visible)

    def fit(self, points):
        points = world(points)
        if not len(points):
            points = np.array([[0, 0, 0]])
        center = (points.min(axis=0) + points.max(axis=0)) / 2
        extent = float(np.linalg.norm(np.ptp(points, axis=0)))
        self.opts['center'] = QtGui.QVector3D(*center)
        self.setCameraPosition(distance=max(1.0, extent*1.8), elevation=12, azimuth=25)

    def fit_scene(self, reference, snapshot):
        """Frame the reference origin, lab equipment, cameras and live observations."""
        points = [tuple(reference.origin) if reference else (0, 0, 0)]
        points += list(self.setup_bounds)
        if snapshot:
            points += [c[1] for c in snapshot['models'].cameras]
            frame = snapshot['frame']
            if frame and time.monotonic() - frame.received < .5:
                points += list(frame.markers)
                points += [b.position for b in frame.bodies if b.valid]
        self.fit(points)
        if reference and self.show_setup and snapshot and snapshot['models'].cameras:
            table_center,table_basis=self.table_layout.frame(reference)
            right,normal,back=table_basis[:,0],table_basis[:,1],table_basis[:,2]
            eye=world([.9*back-.55*right+.65*normal])[0]
            eye/=np.linalg.norm(eye)
            # Fit projected equipment bounds, rather than a diagonal sphere,
            # so the complete rig fills this wide viewport without cropping.
            corners=[]
            if len(self.setup_bounds)==2:
                low,high=self.setup_bounds
                corners=[(x,y,z) for x in [low[0],high[0]]
                         for y in [low[1],high[1]] for z in [low[2],high[2]]]
            projected=world(points+corners)
            # The breadboard's top centre is the navigation view's focal point.
            center=world([table_center])[0]
            self.opts['center']=QtGui.QVector3D(*center)
            horizontal=np.cross(world([normal])[0],eye)
            horizontal/=np.linalg.norm(horizontal)
            vertical=np.cross(eye,horizontal)
            offsets=projected-center
            tan_horizontal=np.tan(np.radians(self.opts['fov']/2))
            tan_vertical=tan_horizontal*self.height()/max(1,self.width())
            depth=offsets @ eye
            distance=float(max(np.max(depth+1.12*np.abs(offsets @ horizontal)/tan_horizontal),
                               np.max(depth+1.12*np.abs(offsets @ vertical)/tan_vertical)))
            self.setCameraPosition(elevation=float(np.degrees(np.arcsin(eye[2]))),
                                   azimuth=float(np.degrees(np.arctan2(eye[1],eye[0]))),
                                   distance=max(1.4,distance))

    def reset_view(self, reference, snapshot):
        if reference and self.show_setup:
            self.fit_scene(reference, snapshot)
            return
        self.opts['center'] = QtGui.QVector3D(*world([reference.origin if reference else (0,0,0)])[0])
        self.setCameraPosition(distance=3.2, elevation=12, azimuth=25)

    def focus_cameras(self, cameras):
        if not cameras:
            return
        self.fit([camera[1] for camera in cameras])
        self.setCameraPosition(distance=1.0, elevation=-15, azimuth=16)
