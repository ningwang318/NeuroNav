"""Sampled spherical-aperture reach with the geometric focus at the MRI target."""
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import numpy as np
from cranial_access import EXTERIOR, UNKNOWN, sample_field


@dataclass(frozen=True)
class TransducerGeometry:
    diameter_mm: float = 64.
    focus_mm: float = 63.2
    aperture_sampling_mm: float = 4.

    def validate(self):
        if (not np.isfinite([self.diameter_mm,self.focus_mm,self.aperture_sampling_mm]).all()
                or not 0 < self.diameter_mm/2 < self.focus_mm or self.aperture_sampling_mm <= 0):
            raise ValueError('A spherical transducer requires focus > aperture radius > 0.')


def configured_transducer():
    root=Path(__file__).resolve().parent/'config'
    placement=root/'acoustic_placement.json'
    name=json.loads(placement.read_text()).get('profile_name','transducer_500khz.json') if placement.exists() else 'transducer_500khz.json'
    if Path(name).name != name:
        raise ValueError('Invalid transducer profile path.')
    profile=json.loads((root/name).read_text())
    model=TransducerGeometry(float(profile['aperture_mm']),float(profile['radius_mm']))
    model.validate()
    return model


def aperture_samples(model):
    """Bowl centred on +Z, focus at origin, including the full aperture rim."""
    model.validate()
    points=[[0.,0.,model.focus_mm]]
    radius=model.diameter_mm/2
    for r in np.linspace(0,radius,int(np.ceil(radius/model.aperture_sampling_mm))+1)[1:]:
        count=max(8,int(np.ceil(2*np.pi*r/model.aperture_sampling_mm)))
        angles=np.arange(count)*2*np.pi/count
        points.extend(np.column_stack([r*np.cos(angles),r*np.sin(angles),
                                      np.full(count,np.sqrt(model.focus_mm**2-r*r))]))
    return np.asarray(points)


def aperture_sources_clear(profiles, step):
    """The emitting bowl must lie outside the head, with variable standoff.

    Outside-FOV source positions are accepted only after >=5 mm of observed
    exterior air. Unknown anatomy before that exterior corridor is rejected.
    """
    n,m=profiles.shape
    unknown=profiles==UNKNOWN
    first_unknown=np.where(unknown.any(axis=1),unknown.argmax(axis=1),m)
    indices=np.arange(m)[None,:]
    observed=indices < first_unknown[:,None]
    # The source must be in exterior air, not soft tissue; distance is allowed
    # to vary freely for shallow targets, without pushing the bowl into the head.
    last_observed=np.maximum(0,first_unknown-1)
    ends_exterior=profiles[np.arange(n),last_observed]==EXTERIOR
    last_non_exterior=np.max(np.where((profiles!=EXTERIOR)&observed,indices,-1),axis=1)
    observed_air=(last_observed-last_non_exterior)*step
    fov_supported=(first_unknown==m)|(observed_air>=5.)
    return ends_exterior&fov_supported


def filter_transducer_access(result, geometry, model=None, cancel=None):
    """Focus stays at target; each candidate orientation has a movable standoff."""
    from skull_analysis import _check
    model=model or configured_transducer()
    model.validate()
    local=aperture_samples(model)
    step=float(min(np.diag(geometry['access_affine'])[:3]))/2
    radii=np.linspace(0,model.focus_mm,int(np.ceil(model.focus_mm/step))+1)
    step=float(radii[1]-radii[0])
    fractions=radii/model.focus_mm
    accessible=np.zeros(len(result.outer),bool)
    standoff=model.focus_mm-np.linalg.norm(result.outer-result.target,axis=1)
    candidates=np.flatnonzero(np.isfinite(result.path_mm)&(standoff>=0))
    for number,i in enumerate(candidates):
        if number%8==0:
            _check(cancel)
        axis=result.outer[i]-result.target
        axis/=np.linalg.norm(axis)
        helper=np.array([0.,0.,1.]) if abs(axis[2])<.9 else np.array([0.,1.,0.])
        x=np.cross(helper,axis); x/=np.linalg.norm(x)
        y=np.cross(axis,x)
        sources=local@np.stack([x,y,axis])
        profiles=sample_field(geometry['access_grid'],geometry['access_affine'],
                              (result.target+sources[:,None,:]*fractions[None,:,None]).reshape(-1,3)).reshape(len(local),-1)
        accessible[i]=bool(aperture_sources_clear(profiles,step).all())
    result.transducer_access=accessible
    result.transducer_standoff_mm=standoff
    result.transducer_geometry=dict(**asdict(model),aperture_samples=len(local),
        ray_step_mm=step,model='Spherical emitting aperture focused at target; sampled bowl clearance and centreline anatomy/angles, no housing or refraction')
    return result
