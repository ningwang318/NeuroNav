"""Measured focal geometry and transducer transforms; all local lengths are mm.

Local +Z is downstream into the field. MAT axis 0 runs far-to-near, hence its
last slice is the nearest sample. Exact FIG meshes retain MATLAB 1-based
indices until physical sampling metadata is supplied.
"""
from dataclasses import dataclass,field,asdict
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation

from neuronav.core.config import ROOT


class AcousticField:
    def __init__(self,path,root=None):
        self.path=Path(path)
        self.root=Path(root or ROOT)
        self.profile=json.loads(self.path.read_text(encoding='utf-8'))
        self.validate_profile()
        asset=self.root/self.profile['asset']
        with np.load(asset,allow_pickle=False) as data:
            self.volume=np.array(data['acoustic_volume'])
            self.surfaces={db:dict(vertices=np.array(data[f'vertices_{abs(db)}'],dtype=float),
                                   faces=np.array(data[f'faces_{abs(db)}'],dtype=np.int32),
                                   color=tuple(data[f'color_{abs(db)}'])) for db in [-3,-6,-12]}
        if self.volume.ndim!=3 or not np.isfinite(self.volume).all() or self.volume.max()<=0:
            raise ValueError('The measured field must be a finite, nonzero 3D array.')
        for mesh in self.surfaces.values():
            if mesh['vertices'].ndim!=2 or mesh['vertices'].shape[1]!=3 or not np.isfinite(mesh['vertices']).all():
                raise ValueError('Invalid focal mesh vertices.')
            if mesh['faces'].ndim!=2 or mesh['faces'].shape[1]!=3 or mesh['faces'].min()<0 or mesh['faces'].max()>=len(mesh['vertices']):
                raise ValueError('Invalid focal mesh faces.')
        self.axial_reversed=False
        self.set_axial_reversed(bool(self.profile.get('reverse_axial_matrix',False)))

    def set_axial_reversed(self,value):
        value=bool(value)
        if value!=self.axial_reversed:
            self.volume=np.ascontiguousarray(self.volume[::-1,:,:])
            surfaces={}
            for db,mesh in self.surfaces.items():
                vertices=mesh['vertices'].copy()
                vertices[:,0]=self.volume.shape[0]+1-vertices[:,0]
                # Axial reflection reverses handedness; keep outward normals.
                surfaces[db]=dict(mesh,vertices=vertices,faces=mesh['faces'][:,[0,2,1]].copy())
            self.surfaces=surfaces
        self.axial_reversed=value
        self.profile['reverse_axial_matrix']=value

    def validate_profile(self):
        p=self.profile
        if not str(p.get('name','')).strip():
            raise ValueError('Enter a transducer name.')
        for key in ['frequency_khz','aperture_mm']:
            if not np.isfinite(p[key]) or p[key]<=0:
                raise ValueError('Frequency and aperture must be positive.')
        radius=p.get('radius_mm')
        if radius is not None and (not np.isfinite(radius) or radius<=p['aperture_mm']/2):
            raise ValueError('Radius of curvature must exceed half the aperture diameter.')
        if p.get('metric_confirmed') and not self.calibrated:
            raise ValueError('Confirmed scan spacing and nearest-slice distance are invalid.')

    @property
    def calibrated(self):
        p=self.profile
        values=[p.get(key) for key in ['axial_spacing_mm','lateral1_spacing_mm','lateral2_spacing_mm']]
        return bool(p.get('metric_confirmed') and all(v is not None and np.isfinite(v) and v>0 for v in values)
                    and p.get('near_offset_mm') is not None and np.isfinite(p['near_offset_mm']) and p['near_offset_mm']>=0)

    def local_points(self,matlab_points):
        if not self.calibrated:
            raise ValueError('Enter confirmed scan spacing and nearest-slice distance before MRI overlay.')
        p=self.profile
        v=np.asarray(matlab_points,dtype=float)
        result=np.empty_like(v)
        result[...,0]=(v[...,1]-(self.volume.shape[1]+1)/2)*p['lateral1_spacing_mm']
        result[...,1]=-(v[...,2]-(self.volume.shape[2]+1)/2)*p['lateral2_spacing_mm']
        result[...,2]=(self.volume.shape[0]-v[...,0])*p['axial_spacing_mm']+p['near_offset_mm']
        return result

    @property
    def peak(self):
        index=np.array(np.unravel_index(np.argmax(self.volume),self.volume.shape),dtype=float)+1
        return self.local_points(index)

    def save_profile(self):
        self.validate_profile()
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.profile,indent=2,allow_nan=False),encoding='utf-8')
        temporary.replace(self.path)


@dataclass
class AcousticPlacement:
    profile_name: str='transducer_500khz.json'
    enabled: bool=True
    mode: str='manual'
    position_mm: list=field(default_factory=lambda:[0.,0.,150.])
    rotation_deg: list=field(default_factory=lambda:[180.,0.,0.])
    beam_shift_mm: float=0.
    manual_initialized: bool=False
    marker_body: str='Transducer'
    marker_translation_mm: list=field(default_factory=lambda:[0.,0.,0.])
    marker_rotation_deg: list=field(default_factory=lambda:[0.,0.,0.])
    marker_calibrated: bool=False
    visible_thresholds: list=field(default_factory=lambda:[-3,-6,-12])

    def validate(self):
        if Path(self.profile_name).name!=self.profile_name or not self.profile_name.endswith('.json'):
            raise ValueError('Select a transducer profile from the configuration folder.')
        if self.mode not in ['manual','preview','tracked']:
            raise ValueError('Unknown transducer placement mode.')
        if not self.marker_body.strip():
            raise ValueError('Enter the marker rigid-body name.')
        for value in [self.position_mm,self.rotation_deg,self.marker_translation_mm,self.marker_rotation_deg]:
            if len(value)!=3 or not np.isfinite(value).all():
                raise ValueError('Positions and rotations require three finite values.')
        if not np.isfinite(self.beam_shift_mm):
            raise ValueError('Invalid beam translation.')
        if not set(self.visible_thresholds)<= {-3,-6,-12}:
            raise ValueError('Unknown focal threshold.')

    def manual_matrix(self):
        self.validate()
        matrix=np.eye(4)
        matrix[:3,:3]=Rotation.from_euler('xyz',self.rotation_deg,degrees=True).as_matrix()
        matrix[:3,3]=self.position_mm
        matrix[:3,3]+=matrix[:3,2]*self.beam_shift_mm
        return matrix

    def center_focus(self,field,volume):
        center=volume.physical((np.array(volume.data.shape)-1)/2)
        self.focus_at(field,center)

    def focus_at(self,field,point):
        rotation=Rotation.from_euler('xyz',self.rotation_deg,degrees=True).as_matrix()
        self.position_mm=(np.asarray(point)-rotation @ field.peak).tolist()
        self.beam_shift_mm=0.
        self.manual_initialized=True

    @classmethod
    def load(cls,path):
        value=cls(**json.loads(Path(path).read_text(encoding='utf-8'))) if Path(path).exists() else cls()
        value.validate()
        return value

    def save(self,path):
        self.validate()
        Path(path).write_text(json.dumps(asdict(self),indent=2,allow_nan=False),encoding='utf-8')


def transducer_geometry(aperture_mm=64.,radius_mm=None):
    """Schematic bowl and annular housing. Apex at z=0; bowl faces +Z."""
    aperture=float(aperture_mm)/2
    radius=float(radius_mm or aperture_mm)  # illustration only if radius unknown
    if radius<=aperture or aperture<=0:
        raise ValueError('Radius of curvature must exceed the aperture radius.')
    n,rows=64,14
    angles=np.arange(n)*2*np.pi/n
    vertices=[]
    for r in np.linspace(0,aperture,rows):
        vertices.extend(np.column_stack([r*np.cos(angles),r*np.sin(angles),np.full(n,radius-np.sqrt(radius**2-r**2))]))
    faces=[]
    for row in range(rows-1):
        for i in range(n):
            a=row*n+i;b=row*n+(i+1)%n;c=a+n;d=b+n
            faces.extend([[a,c,b],[b,c,d]])
    bowl=dict(vertices=np.array(vertices),faces=np.array(faces),color=(.76,.81,.84,1.))
    sag=radius-np.sqrt(radius**2-aperture**2)
    vertices=[]
    for z,r in [(-22.,aperture+4),(sag,aperture+4),(sag,aperture),(-22.,aperture)]:
        vertices.extend(np.column_stack([r*np.cos(angles),r*np.sin(angles),np.full(n,z)]))
    faces=[]
    for ring in range(4):
        nxt=(ring+1)%4
        for i in range(n):
            j=(i+1)%n;a=ring*n+i;b=ring*n+j;c=nxt*n+i;d=nxt*n+j
            faces.extend([[a,b,c],[b,d,c]])
    housing=dict(vertices=np.array(vertices),faces=np.array(faces),color=(.21,.25,.30,1.))
    return [bowl,housing]
