"""Experimental T1 low-signal-band morphology; no density/acoustic inference.

Coordinates are MRI RAS+ mm. A dark band is a skull *candidate*, not a bone
segmentation. Unsupported directions remain missing; no shell is fabricated.
"""
from dataclasses import dataclass
import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, cKDTree


@dataclass(frozen=True)
class SkullOptions:
    directions: int = 4000
    dark_fraction: float = .28
    min_band_mm: float = 1.5
    max_band_mm: float = 12.
    max_scalp_depth_mm: float = 22.
    normal_radius_mm: float = 12.

    def validate(self):
        if not 300 <= self.directions <= 20000:
            raise ValueError('Use 300–20000 sample directions.')
        values = [self.dark_fraction, self.min_band_mm, self.max_band_mm,
                  self.max_scalp_depth_mm, self.normal_radius_mm]
        if not np.isfinite(values).all() or not .05 <= self.dark_fraction <= .7:
            raise ValueError('Invalid low-signal threshold or morphology settings.')
        if not 0 < self.min_band_mm < self.max_band_mm <= 25:
            raise ValueError('Band thickness limits must satisfy 0 < minimum < maximum ≤ 25 mm.')
        if self.max_scalp_depth_mm <= self.max_band_mm or self.normal_radius_mm <= 0:
            raise ValueError('Search depth must exceed maximum band thickness; normal radius must be positive.')


@dataclass
class SkullResult:
    target: np.ndarray
    center: np.ndarray
    outer: np.ndarray
    inner: np.ndarray
    faces: np.ndarray
    outer_normals: np.ndarray
    inner_hits: np.ndarray
    outer_angle: np.ndarray
    inner_angle: np.ndarray
    path_mm: np.ndarray
    normal_mm: np.ndarray
    fit_error_mm: np.ndarray
    options: SkullOptions
    source_name: str
    synthetic: bool
    method: str = 'Experimental T1 low-signal bands'
    segmentation_path: str = ''
    coverage_scope: str = 'upper calvarium; no density or transmission prediction'
    transducer_access: np.ndarray | None = None
    transducer_standoff_mm: np.ndarray | None = None
    transducer_geometry: dict | None = None

    def eligible(self, angle_limit):
        return (np.isfinite(self.path_mm) & (self.outer_angle <= angle_limit)
                & (self.inner_angle <= angle_limit)
                & (self.transducer_access if self.transducer_access is not None else True))


def _check(cancel):
    if cancel and cancel():
        raise InterruptedError('Skull analysis cancelled.')


def fibonacci(n):
    i = np.arange(n)
    z = 1 - 2 * (i + .5) / n
    r = np.sqrt(1 - z*z)
    a = i * np.pi * (3 - np.sqrt(5))
    return np.column_stack((r*np.cos(a), r*np.sin(a), z))


def fit_normals(points, radius):
    """Fit each boundary separately; reject sparse/non-planar local patches."""
    tree = cKDTree(points)
    normals = np.full_like(points, np.nan)
    errors = np.full(len(points), np.nan)
    for i, point in enumerate(points):
        ids = tree.query_ball_point(point, radius)
        if len(ids) < 8:
            continue
        patch = points[ids] - np.mean(points[ids], axis=0)
        values, vectors = np.linalg.eigh(patch.T @ patch / len(ids))
        error = np.sqrt(max(values[0], 0))
        if values[1] < 1 or values[0] > .15*values[1] or error > 2:
            continue
        normals[i] = vectors[:, 0]
        errors[i] = error
    return normals, errors


def ray_hits(origin, direction, vertices, faces):
    """Closest positive intersection with supported triangles, RAS mm."""
    if not len(faces):
        return np.nan, np.full(3, np.nan)
    triangles = vertices[faces]
    e1 = triangles[:, 1] - triangles[:, 0]
    e2 = triangles[:, 2] - triangles[:, 0]
    h = np.cross(np.broadcast_to(direction, e2.shape), e2)
    det = np.einsum('ij,ij->i', e1, h)
    ok = np.abs(det) > 1e-9
    inv = np.divide(1., det, out=np.zeros_like(det), where=ok)
    s = origin - triangles[:, 0]
    u = inv * np.einsum('ij,ij->i', s, h)
    q = np.cross(s, e1)
    v = inv * (q @ direction)
    t = inv * np.einsum('ij,ij->i', e2, q)
    ok &= (u >= -1e-7) & (v >= -1e-7) & (u+v <= 1+1e-7) & (t > 1e-5)
    if not np.any(ok):
        return np.nan, np.full(3, np.nan)
    index = np.argmin(np.where(ok, t, np.inf))
    normal = np.cross(e1[index], e2[index])
    return t[index], normal / np.linalg.norm(normal)


def incidence(direction, normal):
    return np.degrees(np.arccos(np.clip(np.abs(np.dot(direction, normal)), 0, 1)))


def analyze_t1(volume, target, options=None, cancel=None):
    options = options or SkullOptions()
    options.validate()
    target = np.asarray(target, float)
    if target.shape != (3,) or not np.isfinite(target).all():
        raise ValueError('Select a finite target in MRI RAS millimetres.')
    # The viewer supplies an anatomical, axis-aligned RAS grid. Do not silently
    # interpret arbitrary affines as axis-aligned geometry.
    affine = np.asarray(volume.affine, float)
    spacing = np.diag(affine)[:3]
    if np.any(spacing <= 0) or not np.allclose(affine[:3, :3], np.diag(spacing)):
        raise ValueError('Skull analysis requires the anatomically reformatted MRI grid.')
    voxel = (np.linalg.inv(affine) @ np.r_[target, 1])[:3]
    if np.any(voxel < 0) or np.any(voxel > np.array(volume.data.shape)-1):
        raise ValueError('Target is outside the MRI.')
    _check(cancel)
    # Bound memory while retaining native coordinates. This grid finds support
    # and the head centre only; profiles use the original MRI resolution.
    stride = max(1, int(np.ceil(max(volume.data.shape)/160)))
    small = np.asarray(volume.data[::stride, ::stride, ::stride], dtype=float)
    finite = small[np.isfinite(small)]
    low, high = np.percentile(finite, [2, 99])
    if high <= low:
        raise ValueError('MRI has insufficient intensity contrast.')
    support = small > low + .08*(high-low)
    support = ndimage.binary_closing(support, iterations=2)
    labels, count = ndimage.label(support)
    if not count:
        raise ValueError('No head tissue found. Use a full-head T1 MRI.')
    sizes = np.bincount(labels.ravel()); sizes[0] = 0
    support = ndimage.binary_fill_holes(labels == np.argmax(sizes))
    coords = np.array(np.where(support))
    lo, hi = coords.min(axis=1)*stride, coords.max(axis=1)*stride
    center = volume.physical((lo+hi)/2)
    if not support[tuple(np.clip(np.rint(voxel/stride).astype(int), 0, np.array(support.shape)-1))]:
        raise ValueError('Target is outside head support. Select an intracranial target.')
    units = fibonacci(options.directions)
    # First release deliberately limits analysis to the upper calvarium, where
    # this radial model is meaningful; no facial/skull-base extrapolation.
    units = units[units[:, 2] >= .1]
    step = min(.5, float(min(spacing))/2)
    maximum = np.linalg.norm((np.array(volume.data.shape)-1)*spacing)
    radii = np.arange(0, maximum, step)
    outer = np.full((len(units), 3), np.nan)
    inner = outer.copy()
    dark = low + options.dark_fraction*(high-low)
    tissue = low + max(.12, options.dark_fraction+.08)*(high-low)
    for i, unit in enumerate(units):
        if i % 32 == 0:
            _check(cancel)
        xyz = center + radii[:, None]*unit
        ijk = (xyz-affine[:3, 3])/spacing
        inside = np.all((ijk >= 0) & (ijk <= np.array(volume.data.shape)-1), axis=1)
        profile = ndimage.map_coordinates(volume.data, ijk.T, order=1, mode='constant', cval=low)
        profile = ndimage.gaussian_filter1d(profile, .5/step)
        bright = (profile > tissue) & inside
        # Reject bright specks as scalp; require a sustained external tissue run.
        runs, n = ndimage.label(bright)
        good = [j for j in range(1, n+1) if np.sum(runs == j)*step >= 2]
        if not good:
            continue
        scalp = np.where(runs == good[-1])[0][-1]
        if not inside[min(scalp+int(2/step), len(inside)-1)]:
            continue  # clipped scalp/FOV
        bands, n = ndimage.label((profile < dark) & inside)
        choices = []
        for j in range(1, n+1):
            ids = np.where(bands == j)[0]
            a, b = ids[0], ids[-1]
            width = (b-a+1)*step
            if (a*step < .45*radii[scalp] or b >= scalp or
                not options.min_band_mm <= width <= options.max_band_mm or
                not 2 <= (scalp-b)*step <= options.max_scalp_depth_mm):
                continue
            flank = max(1, int(3/step))
            before = profile[max(0, a-flank):a]
            after = profile[b+1:min(scalp+1, b+1+flank)]
            if len(before) and len(after) and np.median(before) > tissue and np.median(after) > tissue:
                choices.append((a, b))
        # Multiple supported bands are ambiguous, not an excuse to guess.
        if len(choices) != 1:
            continue
        a, b = choices[0]
        def crossing(j, k):
            fraction = np.clip((dark-profile[j])/(profile[k]-profile[j]), 0, 1)
            return radii[j] + fraction*(radii[k]-radii[j])
        inner[i] = center + crossing(a-1, a)*unit
        outer[i] = center + crossing(b, b+1)*unit
    _check(cancel)
    valid = np.isfinite(outer[:, 0])
    if valid.sum() < 40:
        raise ValueError('Too few supported skull-band candidates. Full-head T1 with visible scalp is required; review contrast/settings. Skull-stripped MRI cannot supply skull thickness.')
    geometry = make_surface_geometry(units, outer, inner, center, opts_radius=options.normal_radius_mm, cancel=cancel)
    return analyze_geometry(volume, target, geometry, options, cancel)


def make_surface_geometry(units, outer, inner, center, opts_radius=12., cancel=None):
    """Target-independent boundary geometry, suitable for persistent storage."""
    valid = np.isfinite(outer[:, 0]) & np.isfinite(inner[:, 0])
    if valid.sum() < 40:
        raise ValueError('Too few supported boundary pairs for skull geometry.')
    # Triangulate the original angular samples; never bridge missing directions
    # by constructing a new convex hull from only surviving boundary points.
    hull = ConvexHull(units)
    # Remove the artificial planar lid at the cut of the sampled hemisphere.
    supported = np.einsum('ij,ij->i', hull.equations[:, :3], units[hull.simplices].mean(axis=1)) > 0
    faces = hull.simplices[supported]
    faces = faces[np.all(valid[faces], axis=1)]
    points = outer[faces]
    edges = np.stack([points[:, 1]-points[:, 0], points[:, 2]-points[:, 0], points[:, 2]-points[:, 1]])
    faces = faces[np.max(np.linalg.norm(edges, axis=2), axis=0) <= 15]
    mapping = np.full(len(units), -1, int); mapping[valid] = np.arange(valid.sum())
    faces = mapping[faces]
    outer, inner = outer[valid], inner[valid]
    normals, errors = fit_normals(outer, opts_radius)
    inward = np.einsum('ij,ij->i', normals, outer-center) < 0
    normals[inward] *= -1
    inner_normals, _ = fit_normals(inner, opts_radius)
    # Exclude inner triangles with unreliable normal support.
    inner_faces = faces[np.all(np.isfinite(inner_normals[faces, 0]), axis=1)]
    normal_mm = np.full(len(outer), np.nan)
    for i, p in enumerate(outer):
        if i % 32 == 0:
            _check(cancel)
        if np.isfinite(normals[i]).all():
            t, _ = ray_hits(p-.05*normals[i], -normals[i], inner, inner_faces)
            if np.isfinite(t) and .5 <= t+.05 <= 25:
                normal_mm[i] = t+.05
    return dict(center=np.asarray(center), outer=outer, inner=inner, faces=faces,
                normals=normals, errors=errors, inner_faces=inner_faces, normal_mm=normal_mm)


def analyze_geometry(volume, target, geometry, options=None, cancel=None):
    """Calculate target paths on existing surfaces without repeating segmentation."""
    options = options or SkullOptions()
    target = np.asarray(target, dtype=float)
    if target.shape != (3,) or not np.isfinite(target).all():
        raise ValueError('Select a finite target in MRI RAS millimetres.')
    voxel = (np.linalg.inv(volume.affine) @ np.r_[target, 1])[:3]
    if np.any(voxel < 0) or np.any(voxel > np.array(volume.data.shape)-1):
        raise ValueError('Target is outside the MRI.')
    center, outer, inner, faces, normals, errors, inner_faces = (
        geometry[k] for k in ['center', 'outer', 'inner', 'faces', 'normals', 'errors', 'inner_faces'])
    hits = np.full_like(outer, np.nan)
    angles = np.full((len(outer), 2), np.nan)
    paths = np.full(len(outer), np.nan)
    normal_thickness = geometry['normal_mm']
    access_policy = None
    if 'access_grid' in geometry:
        import json
        from cranial_access import AccessPolicy, INTRACRANIAL, sample_field, screen_target_path
        access_policy = AccessPolicy(**json.loads(str(geometry['access_policy_json']))['policy'])
        if sample_field(geometry['access_grid'], geometry['access_affine'], target[None,:])[0] != INTRACRANIAL:
            raise ValueError('Select a target inside the segmented intracranial compartment.')
    for i, p in enumerate(outer):
        if i % 32 == 0:
            _check(cancel)
        if not np.isfinite(normals[i]).all():
            continue
        direction = p-target
        distance = np.linalg.norm(direction)
        if distance < 1 or np.dot(direction, normals[i]) <= 0:
            continue
        if access_policy is not None and not screen_target_path(geometry, target, p, access_policy)[0]:
            continue
        direction /= distance
        t, inner_n = ray_hits(target, direction, inner, inner_faces)
        if np.isfinite(t) and .5 <= distance-t <= 30:
            hits[i] = target+t*direction
            angles[i] = incidence(direction, normals[i]), incidence(direction, inner_n)
            paths[i] = distance-t
    if np.isfinite(paths).sum() < 10:
        raise ValueError('Insufficient reliable inner/outer boundary pairs for this target. Review the MRI boundaries and target.')
    return SkullResult(target.copy(), center, outer, inner, faces, normals, hits,
                       angles[:, 0], angles[:, 1], paths, normal_thickness, errors,
                       options, volume.name, volume.synthetic)


def window_labels(result, angle_limit):
    """Connected candidate patches using supported mesh edges only."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    eligible = result.eligible(angle_limit)
    faces = result.faces
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    edges = edges[np.all(eligible[edges], axis=1)]
    graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(len(eligible),)*2)
    _, labels = connected_components(graph, directed=False)
    counts = np.bincount(labels[eligible], minlength=len(eligible))
    usable = eligible & (counts[labels] >= 5)
    return np.where(usable, labels, -1)


def angle_colors(result):
    """Fixed degree scale so target comparisons never rescale themselves."""
    angles = np.maximum(result.outer_angle, result.inner_angle)
    known = np.isfinite(result.path_mm) & np.isfinite(angles)
    stops = np.array([0., 15., 30., 60., 90.])
    palette = np.array([[.15, .85, .3], [.15, .85, .85], [1., .85, .15],
                        [1., .3, .15], [.75, .1, .2]])
    colors = np.tile([.5, .5, .5, .45], (len(angles), 1))
    for channel in range(3):
        colors[known, channel] = np.interp(angles[known], stops, palette[:, channel])
    colors[known, 3] = .95
    return colors


def demo_t1():
    """Explicitly synthetic full-head T1-like layered shell for verification."""
    from mri_volume import MRIVolume
    n = 128
    spacing = 1.5
    grid = (np.indices((n,)*3).astype(float)-(n-1)/2)*spacing
    radius = np.sqrt(np.sum(grid*grid, axis=0))
    data = np.zeros((n,)*3, np.float32)
    data[radius < 86] = 100  # scalp
    data[radius < 80] = 5    # low-signal shell
    data[radius < 73] = 120  # intracranial tissue
    affine = np.diag([spacing]*3+[1.]); affine[:3, 3] = -(n-1)/2*spacing
    return MRIVolume(data, affine, 'SYNTHETIC T1 SKULL DEMO', 'Known 7 mm spherical shell; not patient MRI.', True)
