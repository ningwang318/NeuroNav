"""Tracked phantom illustration and a persistent marker-to-phantom transform.

Head coordinates: origin at bottom center, +Y up, +Z toward the nose.
Offsets map head coordinates into the rigid body's local frame, in metres.
This supplies an assumed MRI placement, not a measured registration.
"""
from dataclasses import dataclass,field
import numpy as np

from neuronav.core.config import load_settings,save_settings
from neuronav.core.frames import body_named,euler_xyz,euler_xyz_degrees,pose_matrix,rotation
from neuronav.imaging.volume_geometry import preview_affine,preview_data


@dataclass
class PhantomSettings:
    enabled: bool=True
    body_name: str='Head'
    height_mm: float=180.
    width_mm: float=160.
    depth_mm: float=200.
    marker_above_top_mm: float=50.
    translation_mm: list=field(default_factory=lambda:[0.,-230.,0.])
    rotation_deg: list=field(default_factory=lambda:[0.,0.,0.])
    configured: bool=False
    use_mri_size: bool=True

    def validate(self):
        if not self.body_name.strip():
            raise ValueError('Choose a rigid-body name.')
        for value in [self.height_mm,self.width_mm,self.depth_mm]:
            if not np.isfinite(value) or not 20<=value<=500:
                raise ValueError('Phantom dimensions must be between 20 and 500 mm.')
        for vector in [self.translation_mm,self.rotation_deg]:
            if len(vector)!=3 or not np.isfinite(vector).all():
                raise ValueError('The offset and rotation must contain three finite values.')
        if not 0<=self.marker_above_top_mm<=500:
            raise ValueError('Marker height above the head must be between 0 and 500 mm.')

    @classmethod
    def load(cls,path):
        return load_settings(cls,path)

    def save(self,path):
        save_settings(self,path)

    def body(self,frame,models):
        return body_named(frame,models,self.body_name)

    def align_upright(self,body,reference,cameras):
        """Capture a fixed body-local offset from the current upright placement."""
        if reference is None or not cameras or not body.valid:
            raise ValueError('A saved table reference, calibrated cameras and a tracked head body are required.')
        up=np.asarray(reference.basis[:,1])
        right=np.mean([camera[1] for camera in cameras],axis=0)-reference.origin
        right-=up*np.dot(right,up)
        if np.linalg.norm(right)<1e-6:
            raise ValueError('Cannot determine the camera side of the table.')
        forward=right/np.linalg.norm(right)
        head_rotation=np.column_stack([np.cross(up,forward),up,forward])
        body_rotation=rotation(body.quaternion)
        offset=-up*(self.height_mm+self.marker_above_top_mm)
        self.translation_mm=(body_rotation.T @ offset).tolist()
        self.rotation_deg=euler_xyz_degrees(body_rotation.T @ head_rotation).tolist()
        self.configured=True
        self.validate()

    def pose(self,body):
        body_rotation=rotation(body.quaternion)
        local_rotation=euler_xyz(self.rotation_deg)
        return (np.asarray(body.position)+body_rotation @ (np.asarray(self.translation_mm)/1000),
                body_rotation @ local_rotation)

    def adopt_mri_size(self,dimensions):
        """Keep marker-to-top placement when replacing the estimated head height."""
        height,width,depth=map(float,dimensions)
        if self.configured:
            up=euler_xyz(self.rotation_deg)[:,1]
            self.translation_mm=(np.asarray(self.translation_mm)+up*(self.height_mm-height)).tolist()
        self.height_mm,self.width_mm,self.depth_mm=height,width,depth
        self.validate()


def mri_head_preview(volume):
    """Full MRI intensity preview with display bounds, not a segmented surface."""
    preview=preview_data(volume,128)
    affine=preview_affine(volume,preview.shape)
    low,high=volume.levels()
    foreground=preview>low+.06*(high-low)
    ranges=[]
    for axis in range(3):
        occupied=np.flatnonzero(np.any(foreground,axis=tuple(i for i in range(3) if i!=axis)))
        if not len(occupied):
            raise ValueError('The MRI has no visible head intensities.')
        ranges.append((occupied[0],occupied[-1]))
    bounds=np.array(ranges,dtype=float)
    # Include one sample width around occupied centres, within the MRI field.
    start=np.maximum(0,bounds[:,0]-.5)
    end=np.minimum(preview.shape,bounds[:,1]+1.5)
    minimum=(affine @ np.r_[start,1])[:3]
    maximum=(affine @ np.r_[end,1])[:3]
    origin=np.array([(minimum[0]+maximum[0])/2,(minimum[1]+maximum[1])/2,minimum[2]])
    ras_to_head=np.array([[-1.,0,0],[0,0,1.],[0,1.,0]])
    local=np.eye(4)
    local[:3,:3]=ras_to_head/1000
    local[:3,3]=-ras_to_head @ origin/1000
    extent=maximum-minimum
    dimensions=np.array([extent[2],extent[0],extent[1]])
    return preview,local @ affine,dimensions


def preview_to_tracker(pose,settings,mri_dimensions,preview_to_head):
    """MRI preview voxel -> tracker metres for the tracked head model.

    pose: (position, orientation) of the head bottom centre, from PhantomSettings.pose.
    mri_dimensions/preview_to_head: from mri_head_preview. The head model is scaled
    from the MRI's physical size to the configured phantom size.
    """
    position,orientation=pose
    height,width,depth=mri_dimensions
    scale=np.diag([settings.width_mm/width,settings.height_mm/height,settings.depth_mm/depth,1])
    return pose_matrix(orientation,position) @ scale @ preview_to_head
