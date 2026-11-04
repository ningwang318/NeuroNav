"""Shared transducer/focal geometry in the tracking and MRI scenes."""
import numpy as np
import time
from PySide6 import QtGui
import pyqtgraph.opengl as gl
from OpenGL import GL
from neuronav.acoustics.field import transducer_geometry
from neuronav.core.frames import DISPLAY4, body_matrix, body_named, euler_xyz, pose_matrix
from neuronav.imaging.volume_geometry import preview_affine
from neuronav.ui.gl_utils import gl_context


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
        with gl_context(self.view, skip_if_current=False):
            self.view.addItem(item)
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
    """MRI RAS+ mm -> tracker m, through the same chain that places the head model."""
    preview_to_tracker=view.preview_to_tracker()
    if preview_to_tracker is None:
        return None
    return preview_to_tracker @ np.linalg.inv(preview_affine(view.phantom_mri,view.phantom_mri_preview.shape))


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

    def rebuild(self,field=None):
        self.tracking_layer.clear()
        self.mri_layer.clear()
        if field is not None:
            self.field=field
        self.tracking_layer=AcousticLayer(self.window.view,self.field)
        self.mri_layer=AcousticLayer(self.window.mri_workspace.volume_view,self.field)
        self.update()

    def update(self,frame=None,models=None,fresh=None):
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
        display=DISPLAY4
        tracker_pose=None
        if p.mode in ['preview','tracked']:
            if p.mode=='tracked' and not p.marker_calibrated:
                status.setText('US: marker-to-transducer attachment is not calibrated')
                return
            body=body_named(self.last_frame,self.last_models,p.marker_body) if live else None
            if body is None:
                status.setText('US: waiting for a fresh '+p.marker_body+' marker pose')
                return
            attachment=pose_matrix(euler_xyz(p.marker_rotation_deg),np.asarray(p.marker_translation_mm)/1000)
            tracker_pose=body_matrix(body) @ attachment @ np.diag([.001,.001,.001,1])
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
            status.setText('US · LIVE MARKER PREVIEW · provisional transducer offset / assumed head alignment' if p.mode=='preview' else
                           'US · TRACKED · MRI/head alignment uses the assumed head offset')
        self.mri_pose=mri_pose
        self.mri_layer.pose(mri_pose)
        self.mri_layer.visible(True,p.visible_thresholds)
        if p.mode=='manual' and mapping is not None:
            self.tracker_pose=mapping @ mri_pose
            self.tracking_layer.pose(display @ mapping @ mri_pose)
            self.tracking_layer.visible(True,p.visible_thresholds)
