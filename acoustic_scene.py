"""Shared transducer/focal geometry in the tracking and MRI scenes."""
import numpy as np
import time
from PySide6 import QtGui
import pyqtgraph.opengl as gl
from OpenGL import GL
from acoustic_field import transducer_geometry
from camera_bar import rotation
from mri_3d import preview_affine, PLANE_AXES
from scipy.spatial.transform import Rotation


SECTION_COLORS = {-12: (75, 195, 255), -6: (137, 233, 97), -3: (255, 102, 104)}


def section_segments(vertices, faces, axis, index):
    """Intersect a triangular field surface with a voxel-centre plane."""
    distances = vertices[:, axis] - index
    signed = distances[faces]
    crossing = (signed > 0).any(axis=1) & (signed <= 0).any(axis=1)
    faces = faces[crossing]
    start = faces
    end = faces[:, [1, 2, 0]]
    edges = (distances[start] > 0) != (distances[end] > 0)
    a, b = start[edges], end[edges]
    fraction = distances[a] / (distances[a] - distances[b])
    return vertices[a] + fraction[:, None] * (vertices[b] - vertices[a])


class FieldMesh(gl.GLMeshItem):
    def paint(self):
        GL.glDepthMask(GL.GL_FALSE)
        try:
            super().paint()
        finally:
            GL.glDepthMask(GL.GL_TRUE)


class AcousticLayer:
    def __init__(self,view,field,field_only=False):
        self.view=view
        self.items=[]
        self.threshold_items={}
        self.field=field
        objects=[] if field_only else transducer_geometry(field.profile['aperture_mm'],field.profile.get('radius_mm'))
        for mesh in objects:
            item=gl.GLMeshItem(vertexes=mesh['vertices'],faces=mesh['faces'],color=mesh['color'],
                               smooth=True,shader='shaded',glOptions='opaque')
            item.setDepthValue(1)
            self.add(item)
        if field.calibrated:
            # Preserve the exact supplied FIG meshes, including side lobes.
            for db in [-12,-6,-3]:
                mesh=field.surfaces[db]
                options={GL.GL_DEPTH_TEST:False,GL.GL_BLEND:True,
                         'glBlendFunc':(GL.GL_SRC_ALPHA,GL.GL_ONE_MINUS_SRC_ALPHA)}
                item=FieldMesh(vertexes=field.local_points(mesh['vertices']),faces=mesh['faces'],
                               color=mesh['color'],smooth=True,shader='shaded',glOptions=options)
                item.setDepthValue(40+[-12,-6,-3].index(db))
                self.add(item)
                self.threshold_items[db]=item
            line=gl.GLLinePlotItem(pos=np.array([[0,0,0],field.peak]),color=(.85,.9,1.,.6),width=1,
                                  glOptions='translucent')
            line.setDepthValue(39)
            self.add(line)
        self.visible(False)

    def add(self,item):
        valid=self.view.isValid()
        previous=QtGui.QOpenGLContext.currentContext()
        surface=previous.surface() if previous is not None else None
        if valid:
            self.view.makeCurrent()
        try:
            self.view.addItem(item)
        finally:
            if valid:
                self.view.doneCurrent()
                if previous is not None and surface is not None:
                    previous.makeCurrent(surface)
        self.items.append(item)

    def visible(self,value,thresholds=(-3,-6,-12)):
        for item in self.items:
            item.setVisible(value)
        for db,item in self.threshold_items.items():
            item.setVisible(value and db in thresholds)

    def pose(self,matrix):
        transform=QtGui.QMatrix4x4(*np.asarray(matrix).ravel().tolist())
        for item in self.items:
            item.setTransform(transform)

    def clear(self):
        for item in self.items:
            self.view.removeItem(item)
        self.items=[]


def tracker_from_mri(view):
    if view.phantom_pose is None or view.phantom_mri is None:
        return None
    position,orientation=view.phantom_pose
    matrix=np.eye(4)
    matrix[:3,:3]=orientation
    matrix[:3,3]=position
    height,width,depth=view.phantom_mri_dimensions
    fixed=getattr(view,'fixed_head',None)
    scale=np.eye(4) if fixed is not None and fixed.pose(view.reference,view.phantom_mri) is not None else np.diag([view.phantom.width_mm/width,view.phantom.height_mm/height,view.phantom.depth_mm/depth,1])
    ras_to_head=view.phantom_mri_transform @ np.linalg.inv(preview_affine(view.phantom_mri,view.phantom_mri_preview.shape))
    return matrix @ scale @ ras_to_head  # MRI mm -> tracker m


class AcousticController:
    def __init__(self,window,field,placement):
        self.window,self.field,self.placement=window,field,placement
        self.tracking_layer=AcousticLayer(window.view,field)
        self.mri_layer=AcousticLayer(window.mri_workspace.volume_view,field)
        self.last_frame=None
        self.last_models=None
        self.fresh=False
        self.mri_pose=None
        self.tracker_pose=None
        self.section_items = {}
        self.section_key = None
        section_options = {GL.GL_DEPTH_TEST: False, GL.GL_BLEND: True,
                           'glBlendFunc': (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)}
        for threshold, color in SECTION_COLORS.items():
            item = gl.GLLinePlotItem(mode='lines', width=2, antialias=True,
                                     color=tuple(c / 255 for c in color) + (1,),
                                     glOptions=section_options)
            item.setDepthValue(45)
            item.hide()
            window.mri_workspace.volume_view.addItem(item)
            self.section_items[threshold] = item
        self.crosshair = gl.GLScatterPlotItem(size=7, color=(.4, 1., 1., 1.),
                                             glOptions=section_options)
        self.crosshair.setDepthValue(46)
        self.crosshair.hide()
        window.mri_workspace.volume_view.addItem(self.crosshair)
        window.mri_workspace.slice_geometry_changed.connect(self.update_sections)

    def update_sections(self):
        workspace = self.window.mri_workspace
        volume = workspace.volume
        pose = self.mri_pose
        thresholds = tuple(self.placement.visible_thresholds)
        active = workspace.active_plane()
        show_face = workspace.cut_check.isChecked()
        key = (id(volume), id(self.field), None if pose is None else pose.tobytes(),
               thresholds, tuple(workspace.indices), active, show_face)
        if key == self.section_key:
            return
        self.section_key = key
        sections = {plane: {} for plane in workspace.panes}
        if volume is not None and pose is not None and self.field.calibrated:
            to_voxel = np.linalg.solve(volume.affine, pose)
            for threshold in thresholds:
                mesh = self.field.surfaces[threshold]
                local = self.field.local_points(mesh['vertices'])
                voxels = local @ to_voxel[:3, :3].T + to_voxel[:3, 3]
                for plane, values in sections.items():
                    axis = PLANE_AXES[plane]
                    points = section_segments(voxels, mesh['faces'], axis, workspace.indices[axis])
                    if len(points):
                        values[threshold] = (points, SECTION_COLORS[threshold])
        for plane, pane in workspace.panes.items():
            pane.show_field_sections(sections[plane])
        for threshold, item in self.section_items.items():
            contour = sections[active].get(threshold)
            item.setVisible(show_face and contour is not None)
            if show_face and contour is not None:
                points = contour[0]
                item.setData(pos=points @ volume.affine[:3, :3].T + volume.affine[:3, 3])
        show_crosshair = show_face and volume is not None and pose is not None
        self.crosshair.setVisible(show_crosshair)
        if show_crosshair:
            self.crosshair.setData(pos=np.array([volume.physical(workspace.indices)]))

    def rebuild(self,field=None):
        self.tracking_layer.clear()
        self.mri_layer.clear()
        if field is not None:
            self.field=field
        self.tracking_layer=AcousticLayer(self.window.view,self.field)
        self.mri_layer=AcousticLayer(self.window.mri_workspace.volume_view,self.field)
        self.section_key = None
        self.update()

    def update(self,frame=None,models=None,fresh=None):
        self.update_pose(frame,models,fresh)
        self.update_sections()

    def update_pose(self,frame=None,models=None,fresh=None):
        if fresh is not None:
            self.last_frame,self.last_models,self.fresh=frame,models,fresh
        w,p,f=self.window,self.placement,self.field
        self.tracking_layer.visible(False)
        self.mri_layer.visible(False)
        self.mri_pose=None
        self.tracker_pose=None
        w.view.acoustic_body_name=p.marker_body if p.enabled and p.mode in ['preview','tracked'] else None
        status=w.mri_workspace.acoustic_status
        if not p.enabled:
            status.setText('Ultrasound overlay hidden')
            return
        if not f.calibrated:
            status.setText('US 500 kHz: scan spacing / surface origin required — open Transducer controls')
            return
        live=bool(self.fresh and self.last_frame and time.monotonic()-self.last_frame.received<.5)
        mapping=tracker_from_mri(w.view) if live else None
        display=np.eye(4);display[:3,:3]=np.array([[1,0,0],[0,0,-1],[0,1,0]])
        tracker_pose=None
        if p.mode=='holder':
            holder=getattr(w.view,'holder_result',None) if live else None
            if holder is None or 'aperture_centre' not in holder:
                status.setText('US: waiting for three identified holder markers')
                return
            tracker_pose=np.eye(4)
            # Beam points from holder through aperture, opposite the M1 ray.
            tracker_pose[:3,:3]=holder['orientation'] @ np.diag([1.,-1.,-1.])*.001
            tracker_pose[:3,3]=holder['aperture_centre']
            self.tracker_pose=tracker_pose
            self.tracking_layer.pose(display @ tracker_pose)
            self.tracking_layer.visible(True,p.visible_thresholds)
        if p.mode in ['preview','tracked']:
            if p.mode=='tracked' and not p.marker_calibrated:
                status.setText('US: marker-to-transducer attachment is not calibrated')
                return
            matches=[b for b in self.last_frame.bodies if b.valid and
                     self.last_models.names.get(b.id,'').casefold()==p.marker_body.casefold()] if live and self.last_models else []
            if len(matches)!=1:
                status.setText('US: waiting for a fresh '+p.marker_body+' marker pose')
                return
            body=matches[0]
            marker=np.eye(4);marker[:3,:3]=rotation(body.quaternion);marker[:3,3]=body.position
            attachment=np.eye(4)
            attachment[:3,:3]=Rotation.from_euler('xyz',p.marker_rotation_deg,degrees=True).as_matrix()
            attachment[:3,3]=np.asarray(p.marker_translation_mm)/1000
            tracker_pose=marker @ attachment @ np.diag([.001,.001,.001,1])
            self.tracker_pose=tracker_pose
            self.tracking_layer.pose(display @ tracker_pose)
            self.tracking_layer.visible(True,p.visible_thresholds)
        volume=w.mri_workspace.volume
        if volume is None:
            status.setText('US: live transducer shown on left · load MRI for brain-relative display' if tracker_pose is not None else
                           'Ultrasound: load MRI for placement preview')
            return
        if p.mode=='manual':
            if not p.manual_initialized:
                p.center_focus(f,volume)
            mri_pose=p.manual_matrix()
            status.setText(f"US {f.profile['frequency_khz']:g} kHz · MANUAL MRI PREVIEW · measured peak {f.peak[2]:.1f} mm / nominal {f.profile.get('nominal_focal_distance_mm') or '?'} mm")
        else:
            if mapping is None:
                status.setText('US: transducer live on left · waiting for head pose to locate it relative to MRI')
                return
            mri_pose=np.linalg.inv(mapping) @ tracker_pose
            status.setText('US · LIVE HOLDER · 185 mm opposite M1 · beam away from holder · head alignment requires review' if p.mode=='holder' else 'US · LIVE MARKER PREVIEW · provisional transducer offset / assumed head alignment' if p.mode=='preview' else
                           'US · TRACKED · MRI/head alignment uses the assumed head offset')
        self.mri_pose=mri_pose
        self.mri_layer.pose(mri_pose)
        self.mri_layer.visible(True,p.visible_thresholds)
        if p.mode=='manual' and mapping is not None:
            self.tracker_pose=mapping @ mri_pose
            self.tracking_layer.pose(display @ mapping @ mri_pose)
            self.tracking_layer.visible(True,p.visible_thresholds)
