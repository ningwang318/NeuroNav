"""3D MRI volume preview and anatomical cut face in MRI RAS+ millimetres.

The bounded-resolution volume is only a display preview. The cut face and
linked 2D slices use the source anatomical display grid at full resolution.
"""
import numpy as np
from PySide6 import QtCore, QtGui
import pyqtgraph.opengl as gl
from OpenGL import GL

from neuronav.imaging.volume_geometry import (
    PLANE_AXES, plane_outline, oblique_face, oblique_clip_equation, preview_data, rgba_data,
    slice_plane_rgba, preview_affine, cut_face, clip_equation)
from neuronav.ui.gl_utils import gl_context, delete_gl_resources


class ClippedVolume(gl.GLVolumeItem):
    failed=QtCore.Signal(str)
    def __init__(self,data):
        super().__init__(data,sliceDensity=1,smooth=True,glOptions='translucent')
        self.cut_enabled=True
        self.equation=np.array([0,0,-1,data.shape[2]/2],dtype=float)
        self.setDepthValue(-10)

    def _uploadData(self):
        for display_list in getattr(self,'lists',{}).values():
            GL.glDeleteLists(display_list,1)
        self.lists={}
        super()._uploadData()

    def paint(self):
        try:
            GL.glDepthMask(GL.GL_FALSE)
            if self.cut_enabled:
                GL.glClipPlane(GL.GL_CLIP_PLANE0,self.equation)
                GL.glEnable(GL.GL_CLIP_PLANE0)
            super().paint()
        except Exception as exc:
            self.setVisible(False)
            self.failed.emit(str(exc))
        finally:
            GL.glDepthMask(GL.GL_TRUE)
            GL.glDisable(GL.GL_CLIP_PLANE0)


class MRI3DView(gl.GLViewWidget):
    scrolled=QtCore.Signal(int)
    selected=QtCore.Signal(object)
    failed=QtCore.Signal(str)
    view_changed=QtCore.Signal()
    def __init__(self):
        super().__init__()
        self.setBackgroundColor('#080f1a')
        self.setMinimumSize(220,190)
        self.volume=None
        self.volume_item=None
        self.face_item=None
        self.plane_line=None
        self.preview=None
        self.last_levels=None
        self.last_model_opacity=None
        self.last_face=None
        self.plane='axial'
        self.indices=[0,0,0]
        self.cut_enabled=True
        self.reverse=False
        self.oblique_point=None
        self.oblique_normal=None
        self.lock_angle=False
        self.press_position=None
        self.overlay_items=[]  # future acoustic overlays: MRI RAS+ mm only

    def addItem(self,item):
        # pyqtgraph initializes new textures immediately on an existing view.
        # File-loading callbacks run outside paintGL, possibly with no context
        # current (or with the tracking scene's context current instead).
        with gl_context(self):
            return super().addItem(item)

    def orbit(self,azim,elev):
        super().orbit(azim,elev)
        self.view_changed.emit()

    def cutting_normal(self,locked=False):
        if self.oblique_normal is None or not locked:
            camera=self.cameraPosition()-self.opts['center']
            normal=np.array([camera.x(),camera.y(),camera.z()],dtype=float)
            self.oblique_normal=normal/np.linalg.norm(normal)
        return self.oblique_normal.copy()

    def wheelEvent(self,event):
        if self.volume is not None and not event.modifiers() & QtCore.Qt.ControlModifier:
            value=event.angleDelta().y()
            if not value:
                value=event.pixelDelta().y()
            if value:
                self.scrolled.emit(1 if value>0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self,event):
        self.press_position=event.position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        click=(self.press_position is not None and
               (event.position()-self.press_position).manhattanLength()<4)
        super().mouseReleaseEvent(event)
        if click and event.button()==QtCore.Qt.LeftButton and self.volume is not None and self.cut_enabled:
            self.pick_cut(event.position())

    def pick_cut(self,position):
        inverse,valid=(self.projectionMatrix()*self.viewMatrix()).inverted()
        if not valid:
            return
        x=2*position.x()/max(1,self.width())-1
        y=1-2*position.y()/max(1,self.height())
        near=inverse.map(QtGui.QVector4D(x,y,-1,1))
        far=inverse.map(QtGui.QVector4D(x,y,1,1))
        if abs(near.w())<1e-9 or abs(far.w())<1e-9:
            return
        start=np.array([near.x(),near.y(),near.z()])/near.w()
        end=np.array([far.x(),far.y(),far.z()])/far.w()
        direction=end-start
        if self.plane=='oblique':
            normal=self.oblique_normal
            point=self.oblique_point
        else:
            normal=np.eye(3)[PLANE_AXES[self.plane]]
            point=self.volume.physical(self.indices)
        denominator=np.dot(direction,normal)
        if abs(denominator)<1e-9:
            return
        t=np.dot(np.asarray(point)-start,normal)/denominator
        if t<0:
            return
        point=start+t*direction
        voxel=(np.linalg.inv(self.volume.affine) @ np.r_[point,1])[:3]
        if np.any(voxel<-.5) or np.any(voxel>np.array(self.volume.data.shape)-.5):
            return
        self.selected.emit(np.clip(np.rint(voxel).astype(int),0,np.array(self.volume.data.shape)-1).tolist())

    def clear_volume(self):
        # GLVolumeItem uses legacy display lists; release them on reload.
        valid=self.isValid()
        with gl_context(self, restore_previous=False, skip_if_current=False):
            for item in [self.volume_item,self.face_item]:
                if item is None:
                    continue
                if valid:
                    delete_gl_resources(item)
                self.removeItem(item)
            for item in self.overlay_items:
                self.removeItem(item)
            self.overlay_items.clear()
        self.volume=self.preview=self.volume_item=self.face_item=None
        self.last_levels=self.last_face=None
        self.plane_line=None
        self.oblique_normal=self.oblique_point=None
        self.update()

    def set_volume(self,volume):
        self.clear_volume()
        self.volume=volume
        self.preview=preview_data(volume)
        self.reset_view()

    def reset_view(self):
        if self.volume is None:
            return
        shape=np.array(self.volume.data.shape)
        center=self.volume.physical((shape-1)/2)
        diameter=float(np.linalg.norm((shape-1)*self.volume.spacing))
        # Horizontal field-of-view: account for a shallow/wide main viewport.
        distance=max(diameter*1.4,float(max((shape-1)*self.volume.spacing))*self.width()/max(1,self.height()))
        self.setCameraPosition(pos=QtGui.QVector3D(*center),distance=distance,elevation=18,azimuth=-55)
        self.view_changed.emit()

    def show_state(self,indices,levels,plane,enabled,reverse,oblique_point=None,lock_angle=False,
                   model_opacity=.35,slice_opacity=.65):
        if self.volume is None:
            return
        self.indices=list(indices)
        self.plane,self.cut_enabled,self.reverse=plane,enabled,reverse
        self.lock_angle=lock_angle
        if self.volume_item is None or self.last_levels!=tuple(levels) or self.last_model_opacity!=model_opacity:
            rgba=rgba_data(self.preview,levels,True)
            rgba[...,3]=np.rint(rgba[...,3].astype(float)*model_opacity).astype(np.uint8)
        if self.volume_item is None:
            self.volume_item=ClippedVolume(rgba)
            self.volume_item.failed.connect(self.failed)
            self.volume_item.setTransform(QtGui.QMatrix4x4(*preview_affine(self.volume,self.preview.shape).ravel().tolist()))
            self.addItem(self.volume_item)
        elif self.last_levels!=tuple(levels) or self.last_model_opacity!=model_opacity:
            self.volume_item.setData(rgba)
        self.volume_item.cut_enabled=False
        if plane=='oblique':
            self.oblique_point=np.asarray(oblique_point if oblique_point is not None else self.volume.physical(indices),dtype=float)
            normal=self.cutting_normal(lock_angle)
            self.volume_item.equation=oblique_clip_equation(self.volume,self.preview.shape,self.oblique_point,normal,reverse)
            key=plane,tuple(np.round(self.oblique_point,5)),tuple(np.round(normal,6)),tuple(levels),slice_opacity
        else:
            self.volume_item.equation=clip_equation(self.volume,self.preview.shape,plane,indices,reverse)
            key=plane,indices[PLANE_AXES[plane]],tuple(levels),slice_opacity
        if key!=self.last_face:
            data,transform=(oblique_face(self.volume,self.oblique_point,normal) if plane=='oblique'
                            else cut_face(self.volume,plane,indices))
            rgba=slice_plane_rgba(data,transform,self.volume,levels,slice_opacity)
            if self.face_item is None:
                options={GL.GL_DEPTH_TEST:True,GL.GL_BLEND:True,GL.GL_ALPHA_TEST:True,
                         'glBlendFunc':(GL.GL_SRC_ALPHA,GL.GL_ONE_MINUS_SRC_ALPHA),
                         'glAlphaFunc':(GL.GL_GREATER,.01)}
                self.face_item=gl.GLImageItem(rgba,smooth=True,glOptions=options)
                self.face_item.setDepthValue(0)
                self.addItem(self.face_item)
            else:
                self.face_item.setData(rgba)
            self.face_item.setTransform(QtGui.QMatrix4x4(*transform.ravel().tolist()))
        self.face_item.setVisible(enabled)
        point=self.oblique_point if plane=='oblique' else self.volume.physical(indices)
        normal=self.oblique_normal if plane=='oblique' else np.eye(3)[PLANE_AXES[plane]]
        outline=plane_outline(self.volume,point,normal)
        if self.plane_line is None:
            self.plane_line=gl.GLLinePlotItem(color=(.25,1.,1.,1.),width=2,antialias=True,
                                            glOptions={GL.GL_DEPTH_TEST:False,GL.GL_BLEND:True,
                                            'glBlendFunc':(GL.GL_SRC_ALPHA,GL.GL_ONE_MINUS_SRC_ALPHA)})
            self.plane_line.setDepthValue(1)
            self.overlay_items.append(self.plane_line)
            self.addItem(self.plane_line)
        self.plane_line.setVisible(enabled and len(outline)>=3)
        if len(outline)>=3:
            self.plane_line.setData(pos=np.vstack([outline,outline[0]]))
        self.last_levels=tuple(levels)
        self.last_model_opacity=model_opacity
        self.last_face=key
        self.update()
