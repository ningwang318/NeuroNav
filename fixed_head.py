"""One-time crown-marker placement retained in the three-marker table frame."""
from datetime import datetime, timezone
from itertools import permutations
import json
from pathlib import Path
import numpy as np


def crown_marker(markers, reference, tolerance=.003):
    points = np.asarray(markers, dtype=float)
    if points.shape != (4, 3) or not np.isfinite(points).all():
        raise ValueError('Keep exactly four markers visible: three table markers and the crown marker.')
    matches = []
    for order in permutations(range(4), 3):
        errors = np.linalg.norm(points[list(order)]-reference.markers, axis=1)
        if max(errors) <= tolerance:
            matches.append(order)
    if len(matches) != 1:
        raise ValueError('Cannot uniquely match all three table markers within 3 mm of the saved reference.')
    return points[next(i for i in range(4) if i not in matches[0])]


def estimate_mri_crown(volume, local_transform, preview_shape):
    """Largest intensity component's superior extent; an estimated scalp landmark.

    Use native MRI resolution rather than the downsampled display volume.
    RAS display volumes are axis aligned. This is not a reviewed scalp segmentation.
    """
    from scipy import ndimage
    from mri_3d import preview_affine
    low, high = volume.levels()
    # Exclude the low-level background of averaged templates. Match the visible
    # model's principal head rather than its faint intensity halo.
    labels, _ = ndimage.label(volume.data > low+.15*(high-low))
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    if sizes.max() == 0:
        raise ValueError('MRI has no connected head foreground.')
    component = labels == sizes.argmax()
    occupied = np.flatnonzero(np.any(component, axis=(0, 1)))
    top = int(occupied[-1])
    if top >= volume.data.shape[2]-1:
        raise ValueError('MRI head touches the superior image boundary; the crown may be cropped.')
    # Centre the apex over the very top foreground band, not the neck/FOV bounds.
    band = max(1, int(round(1.0/volume.spacing[2])))
    xy = np.argwhere(np.any(component[:, :, max(0, top-band+1):top+1], axis=2)).mean(axis=0)
    ras = volume.physical([*xy, top+.5])
    ras_to_head = local_transform @ np.linalg.inv(preview_affine(volume, preview_shape))
    return (ras_to_head @ np.r_[ras, 1])[:3], ras


class FixedHead:
    def __init__(self, data):
        self.data = data
        for key in ('origin_reference_m', 'crown_marker_reference_m'):
            value = np.asarray(data[key], float)
            if value.shape != (3,) or not np.isfinite(value).all():
                raise ValueError('Invalid saved head position.')
        basis = np.asarray(data['orientation_reference'], float)
        if basis.shape != (3, 3) or not np.isfinite(basis).all() or not np.allclose(basis.T@basis, np.eye(3), atol=1e-6) or not np.isclose(np.linalg.det(basis), 1):
            raise ValueError('Invalid saved head orientation.')

    @classmethod
    def capture(cls, samples, reference, crown_local, crown_ras, volume, gap_mm=7.5):
        samples = np.asarray(samples, float)
        if samples.ndim != 2 or samples.shape[1] != 3 or len(samples) < 45 or not np.isfinite(samples).all():
            raise ValueError('At least 45 fresh crown-marker samples are required.')
        centre = samples.mean(axis=0)
        jitter = float(np.sqrt(np.mean(np.sum((samples-centre)**2, axis=1)))*1000)
        if jitter > 1 or np.max(np.linalg.norm(samples-centre, axis=1)) > .003:
            raise ValueError('Crown marker is unstable; keep the phantom stationary and retry.')
        marker = reference.points([centre])[0]
        up = np.array([0., 1., 0.])
        toward_p2 = reference.points([reference.markers[1]])[0]-marker
        toward_p2[1] = 0
        if np.linalg.norm(toward_p2) < .02:
            raise ValueError('P2 is too close to the crown projection to determine the nose heading.')
        forward = toward_p2/np.linalg.norm(toward_p2)
        orientation = np.column_stack([np.cross(up, forward), up, forward])
        origin = marker-up*gap_mm/1000-orientation@np.asarray(crown_local)
        from reference_plane import distances
        return cls(dict(schema=1, units='metres', captured_utc=datetime.now(timezone.utc).isoformat(),
            origin_reference_m=origin.tolist(), orientation_reference=orientation.tolist(),
            crown_marker_reference_m=marker.tolist(), marker_above_crown_mm=gap_mm,
            mri_crown_ras_mm=np.asarray(crown_ras).tolist(), mri_source=volume.source_path,
            mri_sha256=volume.source_sha256, samples=len(samples), crown_jitter_rms_mm=jitter,
            reference_edges_mm=(distances(reference.markers)*1000).tolist(),
            assumptions='Stationary phantom; upright to table normal; nose toward P2 (15/25 cm corner). MRI crown estimated from intensity foreground; review required.'))

    def pose(self, reference, volume):
        if reference is None or volume is None or volume.source_sha256 != self.data['mri_sha256']:
            return None
        from reference_plane import distances
        if max(abs(distances(reference.markers)*1000-np.asarray(self.data['reference_edges_mm']))) > 2:
            return None
        return (reference.origin+reference.basis@np.asarray(self.data['origin_reference_m']),
                reference.basis@np.asarray(self.data['orientation_reference']))

    def save(self, path):
        path = Path(path)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(path)

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('schema') != 1 or data.get('units') != 'metres':
            raise ValueError('Unsupported fixed head placement.')
        return cls(data)
