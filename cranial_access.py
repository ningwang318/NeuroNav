"""Anatomical centreline screening on CHARM labels, not transducer-clearance modelling."""
from dataclasses import asdict, dataclass
import numpy as np
from scipy import ndimage

EXTERIOR, SOFT, BONE, INTRACRANIAL, EXCLUDED, FACE = range(6)
UNKNOWN = 255


@dataclass(frozen=True)
class AccessPolicy:
    max_overlying_tissue_mm: float = 30.
    exterior_clearance_mm: float = 5.
    eye_margin_mm: float = 8.
    intracranial_gap_mm: float = 5.
    inferior_margin_mm: float = 8.


def access_field(final, raw, affine, brain, policy=None):
    """Separate intracranial support, exterior and excluded facial anatomy."""
    policy = policy or AccessPolicy()
    spacing = np.diag(affine)[:3]
    intracranial = ((raw > 0) & (raw < 100)) | (raw == 520)
    components, count = ndimage.label(intracranial)
    sizes = np.bincount(components.ravel()); sizes[0] = 0
    if not count or not sizes.max():
        raise ValueError('No connected intracranial compartment in saved CHARM labels.')
    intracranial = ndimage.binary_fill_holes(components == np.argmax(sizes))
    del components
    bone = np.isin(final, [7,8])
    intracranial &= ~bone
    field = np.zeros(final.shape, np.uint8)
    field[final != 0] = SOFT
    field[bone] = BONE
    field[intracranial] = INTRACRANIAL
    eyes = (raw == 506) | (final == 6)
    excluded = np.isin(raw, [501,506,509,512,530]) | np.isin(final, [6,11])
    if eyes.any():
        eye_distance = ndimage.distance_transform_edt(~eyes, sampling=spacing)
        excluded |= (eye_distance <= policy.eye_margin_mm) & ~intracranial
        del eye_distance
        eye_indices = np.array(np.where(eyes))
        eye_back = affine[1,3]+eye_indices[1].min()*spacing[1]
        eye_top = affine[2,3]+eye_indices[2].max()*spacing[2]
    else:
        # Conservative fallback when a scan has no segmented eye compartments.
        indices = np.array(np.where(brain))
        eye_back = affine[1,3]+np.percentile(indices[1],90)*spacing[1]+policy.eye_margin_mm
        eye_top = affine[2,3]+np.mean(indices[2])*spacing[2]-policy.eye_margin_mm
    field[excluded] = EXCLUDED
    brain_indices = np.array(np.where(brain))
    inferior_limit = affine[2,3]+brain_indices[2].min()*spacing[2]-policy.inferior_margin_mm
    y = affine[1,3]+np.arange(field.shape[1])*spacing[1]
    z = affine[2,3]+np.arange(field.shape[2])*spacing[2]
    facial = ((y[None,:,None] >= eye_back-policy.eye_margin_mm)
              & (z[None,None,:] <= eye_top+policy.eye_margin_mm))
    inferior = z[None,None,:] < inferior_limit
    restricted = np.broadcast_to(facial | inferior, field.shape)
    field[restricted & ~intracranial & (field != EXTERIOR) & (field != EXCLUDED)] = FACE
    distance_to_brain = ndimage.distance_transform_edt(~intracranial, sampling=spacing)
    summary = dict(policy=asdict(policy), eye_labels_present=bool(eyes.any()),
                   facial_posterior_y_mm=float(eye_back-policy.eye_margin_mm),
                   facial_superior_z_mm=float(eye_top+policy.eye_margin_mm),
                   inferior_limit_z_mm=float(inferior_limit),
                   scope='Full sphere; intracranial-adjacent bone with an unobstructed outward soft-tissue/exterior path; eyes, face/jaw, cavities and neck excluded')
    return field, distance_to_brain, summary


def bone_runs(profile):
    changes = np.flatnonzero(np.diff(np.r_[False, profile == BONE, False]))
    return list(zip(changes[::2], changes[1::2]-1))


def screen_profile(profile, radii, a, b, policy=None):
    """Require one skull crossing and a supported external entry corridor."""
    policy = policy or AccessPolicy()
    before = profile[:b+1]
    if np.any(before == EXCLUDED):
        return False, 'excluded_anatomy'
    if np.any(before == FACE):
        return False, 'face_or_neck'
    if np.any(before == UNKNOWN):
        return False, 'clipped_scan'
    if len(bone_runs(before)) != 1:
        return False, 'multiple_bone_crossings'
    intracranial = np.flatnonzero(profile[:a] == INTRACRANIAL)
    if not len(intracranial) or radii[a]-radii[intracranial[-1]] > policy.intracranial_gap_mm:
        return False, 'not_adjacent_to_intracranial_space'
    step = radii[1]-radii[0]
    clear_samples = max(1, int(np.ceil(policy.exterior_clearance_mm/step)))
    exterior_count = 0
    exterior_start = None
    for j in range(b+1,len(profile)):
        value = profile[j]
        if value == EXCLUDED:
            return False, 'excluded_external_anatomy'
        if value == FACE:
            return False, 'face_or_neck'
        if value == UNKNOWN:
            return False, 'clipped_scan'
        if value == BONE:
            return False, 'additional_external_bone'
        if value == INTRACRANIAL:
            return False, 'intracranial_reentry'
        if value == EXTERIOR:
            if exterior_count == 0:
                exterior_start = radii[j]
            exterior_count += 1
            if exterior_count >= clear_samples:
                if exterior_start-radii[b] > policy.max_overlying_tissue_mm:
                    return False, 'deep_overlying_tissue'
                return True, 'accepted'
        else:
            exterior_count = 0
        if radii[j]-radii[b] > policy.max_overlying_tissue_mm+policy.exterior_clearance_mm:
            return False, 'deep_overlying_tissue'
    return False, 'no_supported_exterior'


def sample_field(field, affine, xyz):
    ijk = (xyz-affine[:3,3])/np.diag(affine)[:3]
    return ndimage.map_coordinates(field, ijk.T, order=0, mode='constant', cval=UNKNOWN)


def screen_target_path(geometry, target, outer, policy=None):
    """Recheck the actual target approach; a changed target changes the corridor."""
    policy = policy or AccessPolicy()
    affine = geometry['access_affine']
    step = float(min(np.diag(affine)[:3]))/2
    tolerance = float(max(np.diag(affine)[:3]))*2
    length = np.linalg.norm(outer-target)
    direction = (outer-target)/length
    radii = np.arange(0,length+policy.max_overlying_tissue_mm+policy.exterior_clearance_mm+tolerance,step)
    profile = sample_field(geometry['access_grid'],affine,target+radii[:,None]*direction)
    near = int(np.searchsorted(radii,length+tolerance))
    choices = [(a,b) for a,b in bone_runs(profile[:near+1]) if abs(radii[b]-length) <= tolerance]
    if not choices:
        return False, 'entry_not_on_supported_bone'
    a,b = choices[-1]
    return screen_profile(profile,radii,a,b,policy)
