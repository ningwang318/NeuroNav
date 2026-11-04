"""3D MRI volume preview and anatomical cut face in MRI RAS+ millimetres.

The bounded-resolution volume is only a display preview. The cut face and
linked 2D slices use the source anatomical display grid at full resolution.
"""
import numpy as np
from PySide6 import QtCore, QtGui
import pyqtgraph.opengl as gl
from OpenGL import GL

PLANE_AXES={'axial':2,'coronal':1,'sagittal':0}
FACE_AXES={'axial':(0,1),'coronal':(0,2),'sagittal':(1,2)}


def physical_bounds(volume):
    low=volume.physical([0,0,0])
    high=volume.physical(np.array(volume.data.shape)-1)
    return np.minimum(low,high),np.maximum(low,high)


def plane_limits(volume,normal):
    low,high=physical_bounds(volume)
    return float(np.sum(np.where(normal>=0,low,high)*normal)),float(np.sum(np.where(normal>=0,high,low)*normal))


def plane_outline(volume,point,normal):
    """Ordered intersection of a cutting plane with the MRI physical bounds."""
    low,high=physical_bounds(volume)
    corners=np.array([(x,y,z) for x in [low[0],high[0]] for y in [low[1],high[1]] for z in [low[2],high[2]]])
    normal=np.asarray(normal,dtype=float)
    normal=normal/np.linalg.norm(normal)
    distances=(corners-point) @ normal
    hits=[]
    for i in range(8):
        for bit in [1,2,4]:
            j=i ^ bit
            if j<=i:
                continue
            if abs(distances[i])<1e-7:
                hits.append(corners[i])
            if abs(distances[j])<1e-7:
                hits.append(corners[j])
            if distances[i]*distances[j]<0:
                hits.append(corners[i]+distances[i]/(distances[i]-distances[j])*(corners[j]-corners[i]))
    unique=[]
    for hit in hits:
        if not any(np.linalg.norm(hit-other)<1e-6 for other in unique):
            unique.append(hit)
    if len(unique)<3:
        return np.empty((0,3))
    points=np.array(unique)
    center=points.mean(axis=0)
    horizontal=points[0]-center
    horizontal/=np.linalg.norm(horizontal)
    vertical=np.cross(normal,horizontal)
    angles=np.arctan2((points-center) @ vertical,(points-center) @ horizontal)
    return points[np.argsort(angles)]


def point_on_plane_in_volume(volume,normal,offset,anchor):
    """Closest point to anchor on a plane constrained to the MRI bounding box."""
    normal=np.asarray(normal,dtype=float)
    normal/=np.linalg.norm(normal)
    low,high=physical_bounds(volume)
    minimum,maximum=plane_limits(volume,normal)
    offset=float(np.clip(offset,minimum,maximum))
    if offset<=minimum+1e-9:
        return np.where(normal>1e-10,low,np.where(normal< -1e-10,high,np.clip(anchor,low,high)))
    if offset>=maximum-1e-9:
        return np.where(normal>1e-10,high,np.where(normal< -1e-10,low,np.clip(anchor,low,high)))
    def candidate(value):
        return np.clip(np.asarray(anchor)+value*normal,low,high)
    lower,upper=-1.,1.
    for _ in range(64):
        if np.dot(candidate(lower),normal)<=offset:
            break
        lower*=2
    for _ in range(64):
        if np.dot(candidate(upper),normal)>=offset:
            break
        upper*=2
    for _ in range(64):
        middle=(lower+upper)/2
        if np.dot(candidate(middle),normal)<offset:
            lower=middle
        else:
            upper=middle
    return candidate((lower+upper)/2)


def oblique_face(volume,point,normal,max_axis=768):
    """Trilinear reslice in a physical-space plane; no source voxels change."""
    from scipy.ndimage import map_coordinates
    normal=np.asarray(normal,dtype=float)
    normal/=np.linalg.norm(normal)
    seed=np.array([0.,0.,1.]) if abs(normal[2])<.95 else np.array([0.,1.,0.])
    horizontal=np.cross(seed,normal)
    horizontal/=np.linalg.norm(horizontal)
    vertical=np.cross(normal,horizontal)
    low,high=physical_bounds(volume)
    corners=np.array([(x,y,z) for x in [low[0],high[0]] for y in [low[1],high[1]] for z in [low[2],high[2]]])
    local=corners-point
    h,v=local @ horizontal,local @ vertical
    width,height=float(np.ptp(h)),float(np.ptp(v))
    step=float(min(volume.spacing))
    nx=min(max_axis,max(2,int(np.ceil(width/step))+1))
    ny=min(max_axis,max(2,int(np.ceil(height/step))+1))
    dh,dv=width/(nx-1),height/(ny-1)
    hs=np.linspace(h.min(),h.max(),nx)
    vs=np.linspace(v.min(),v.max(),ny)
    positions=(np.asarray(point)[:,None,None]+horizontal[:,None,None]*hs[None,:,None]
               +vertical[:,None,None]*vs[None,None,:])
    inverse=np.linalg.inv(volume.affine)
    coords=(inverse[:3,:3] @ positions.reshape(3,-1)+inverse[:3,3,None]).reshape(3,nx,ny)
    data=map_coordinates(volume.data,coords,order=1,mode='constant',cval=0,prefilter=False)
    transform=np.eye(4)
    transform[:3,0]=horizontal*dh
    transform[:3,1]=vertical*dv
    transform[:3,2]=normal*step
    transform[:3,3]=point+horizontal*(h.min()-.5*dh)+vertical*(v.min()-.5*dv)
    return data,transform


def oblique_clip_equation(volume,shape,point,normal,reverse=False):
    normal=np.asarray(normal,dtype=float)
    normal/=np.linalg.norm(normal)
    physical_equation=np.r_[-normal,np.dot(normal,point)]
    equation=preview_affine(volume,shape).T @ physical_equation
    return -equation if reverse else equation


def preview_data(volume,max_axis=192):
    from scipy.ndimage import zoom
    shape=np.array(volume.data.shape)
    factor=min(1.,max_axis/shape.max())
    target=np.maximum(2,np.rint(shape*factor).astype(int))
    if np.array_equal(target,shape):
        return volume.data
    return zoom(volume.data,target/shape,order=1,prefilter=False,grid_mode=False).astype(np.float32,copy=False)


def rgba_data(data,levels,volume=False):
    low,high=levels
    normalized=np.clip((data-low)/max(high-low,1e-6),0,1)
    gray=np.rint(normalized*255).astype(np.uint8)
    rgba=np.empty(data.shape+(4,),dtype=np.uint8)
    rgba[...,:3]=gray[...,None]
    if volume:
        rgba[...,3]=np.where(normalized>.035,5+normalized*25,0).astype(np.uint8)
    else:
        rgba[...,3]=np.where(normalized>.005,255,0).astype(np.uint8)
    return np.ascontiguousarray(rgba)


def slice_plane_rgba(data,transform,volume,levels,opacity=.65):
    """Translucent tissue with transparent low-intensity MRI background."""
    rgba=rgba_data(data,levels)
    x,y=np.meshgrid(np.arange(data.shape[0])+.5,np.arange(data.shape[1])+.5,indexing='ij')
    points=transform[:3,3,None,None]+transform[:3,0,None,None]*x+transform[:3,1,None,None]*y
    low,high=physical_bounds(volume)
    inside=np.all((points>=low[:,None,None]-1e-5)&(points<=high[:,None,None]+1e-5),axis=0)
    normalized=np.clip((data-levels[0])/max(levels[1]-levels[0],1e-6),0,1)
    fade=np.clip((normalized-.02)/.05,0,1)
    rgba[...,3]=np.rint(inside*fade*255*np.clip(opacity,0,1)).astype(np.uint8)
    return rgba


def preview_affine(volume,shape):
    # GLVolumeItem spans [0,N], while sample centres span [0,sourceN-1].
    transform=np.array(volume.affine)
    transform[:3,:3]=volume.affine[:3,:3] @ np.diag((np.array(volume.data.shape)-1)/np.array(shape))
    return transform


def cut_face(volume,plane,indices):
    axis=PLANE_AXES[plane]
    h,v=FACE_AXES[plane]
    data=np.take(volume.data,int(indices[axis]),axis=axis)
    # Remaining axes are ascending: XY, XZ or YZ.
    transform=np.eye(4)
    transform[:3,0]=volume.affine[:3,h]
    transform[:3,1]=volume.affine[:3,v]
    transform[:3,2]=volume.affine[:3,axis]
    origin=np.asarray(indices,dtype=float).copy()
    origin[h]=origin[v]=-.5
    transform[:3,3]=volume.physical(origin)
    return data,transform


def clip_equation(volume,shape,plane,indices,reverse=False):
    axis=PLANE_AXES[plane]
    boundary=float(indices[axis])/(volume.data.shape[axis]-1)*shape[axis]
    equation=np.zeros(4,dtype=float)
    # OpenGL retains the positive side of the plane equation.
    equation[axis]=1 if reverse else -1
    equation[3]=-boundary if reverse else boundary
    return equation


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
        if not self.isValid():
            return super().addItem(item)
        previous=QtGui.QOpenGLContext.currentContext()
        if previous==self.context():
            return super().addItem(item)
        surface=previous.surface() if previous is not None else None
        self.makeCurrent()
        try:
            return super().addItem(item)
        finally:
            self.doneCurrent()
            if previous is not None and surface is not None:
                previous.makeCurrent(surface)

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
        if valid:
            self.makeCurrent()
        for item in [self.volume_item,self.face_item]:
            if item is None:
                continue
            if valid:
                texture=getattr(item,'texture',None)
                if texture is not None:
                    GL.glDeleteTextures([texture])
                for display_list in getattr(item,'lists',{}).values():
                    GL.glDeleteLists(display_list,1)
            self.removeItem(item)
        for item in self.overlay_items:
            self.removeItem(item)
        self.overlay_items.clear()
        if valid:
            self.doneCurrent()
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
