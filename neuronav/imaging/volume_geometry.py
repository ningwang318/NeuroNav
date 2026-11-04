"""Pure MRI volume geometry in RAS+ millimetres: bounds, planes, reslicing, previews.

No Qt or OpenGL. The 3D views (neuronav.ui.mri_3d) and the tracked head model use these.
"""
import numpy as np
from scipy.ndimage import map_coordinates, zoom

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
