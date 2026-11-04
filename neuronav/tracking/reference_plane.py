"""Stationary three-marker reference frame; never changes camera calibration."""
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import permutations

import numpy as np

from neuronav.core.config import read_json, write_json_atomic

PERMUTATIONS = list(permutations(range(3)))


def triangle(points):
    p = np.asarray(points, dtype=float)
    if p.shape != (3, 3) or not np.isfinite(p).all():
        raise ValueError('Exactly three finite observed markers are required.')
    opposite = np.array([np.linalg.norm(p[1]-p[2]), np.linalg.norm(p[0]-p[2]), np.linalg.norm(p[0]-p[1])])
    lengths = np.sort(opposite)
    if lengths[0] < .02:
        raise ValueError('Marker separation must be at least 20 mm.')
    if np.min(np.diff(lengths)) < .002:
        raise ValueError('Triangle is too symmetric to identify markers reliably; vary the spacing by more than 2 mm.')
    if np.linalg.norm(np.cross(p[1]-p[0], p[2]-p[0])) / lengths[-1] < .01:
        raise ValueError('Markers are too nearly collinear; use a triangle at least 10 mm high.')
    # Identity is determined by opposite-edge length, never the stream order.
    return p[np.argsort(opposite)]


def align(points, reference):
    p = np.asarray(points, dtype=float)
    if p.shape != (3, 3) or not np.isfinite(p).all():
        raise ValueError('Keep exactly the three reference markers visible throughout capture.')
    candidates = [p[list(order)] for order in PERMUTATIONS]
    return min(candidates, key=lambda c: np.sum((c-reference)**2))


def distances(points):
    p = np.asarray(points)
    return np.array([np.linalg.norm(p[0]-p[1]), np.linalg.norm(p[0]-p[2]), np.linalg.norm(p[1]-p[2])])


def visible_markers(points, reference, hide=True, tolerance_m=.002):
    """Hide at most one observed point per stationary reference position.

    This is a display-only proximity match, not marker identity tracking.
    """
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    if not hide or reference is None or not len(points):
        return points, 0
    offsets = np.linalg.norm(points[:, None, :]-reference.markers[None, :, :], axis=2)
    candidates = sorted((offsets[i,j], i, j) for i, j in zip(*np.where(offsets <= tolerance_m)))
    hidden, matched = set(), set()
    for _, i, j in candidates:
        if i not in hidden and j not in matched:
            hidden.add(i)
            matched.add(j)
    return points[[i for i in range(len(points)) if i not in hidden]], len(hidden)


@dataclass
class ReferencePlane:
    origin: np.ndarray
    basis: np.ndarray  # columns: reference axes expressed in tracker coordinates
    markers: np.ndarray
    metadata: dict
    template: np.ndarray = None

    def points(self, points):
        return (np.asarray(points, dtype=float).reshape(-1, 3)-self.origin) @ self.basis

    def orientation(self, rotation):
        return self.basis.T @ rotation

    def save(self, path):
        data = dict(schema=1, units='metres', origin_tracker_m=self.origin.tolist(),
                    reference_axes_in_tracker=self.basis.tolist(),
                    markers_tracker_m=self.markers.tolist(),
                    markers_reference_m=self.points(self.markers).tolist(),
                    geometry_template_m=(self.template if self.template is not None else self.points(self.markers)).tolist(),
                    edge_lengths_mm=(distances(self.markers)*1000).tolist(),
                    convention='Origin: centroid. X: P1 to P2. Y: plane normal. Z: X cross Y.',
                    **self.metadata)
        write_json_atomic(path, data)

    @classmethod
    def load(cls, path):
        data = read_json(path)
        if data.get('schema') != 1 or data.get('units') != 'metres':
            raise ValueError('Unsupported reference geometry file.')
        p = triangle(data['markers_tracker_m'])
        basis = np.asarray(data['reference_axes_in_tracker'], dtype=float)
        origin = np.asarray(data['origin_tracker_m'], dtype=float)
        if origin.shape != (3,) or not np.isfinite(origin).all() or basis.shape != (3, 3):
            raise ValueError('Invalid saved reference transform.')
        if not np.isfinite(basis).all() or not np.allclose(basis.T @ basis, np.eye(3), atol=1e-6) or not np.isclose(np.linalg.det(basis), 1):
            raise ValueError('Invalid saved reference orientation.')
        template = np.asarray(data.get('geometry_template_m', data['markers_reference_m']), dtype=float)
        triangle(template)
        return cls(origin, basis, p, {}, template)


class Capture:
    def __init__(self, expected=None, flip=False):
        self.samples = []
        self.expected = expected
        self.flip = flip
        self.last_frame = None

    def add(self, points, frame_number):
        if frame_number == self.last_frame:
            return
        p = triangle(points) if not self.samples else align(points, self.samples[0])
        if self.samples and np.max(np.linalg.norm(p-self.samples[0], axis=1)) > .003:
            raise ValueError('Markers moved more than 3 mm during capture. Keep the reference stationary and retry.')
        self.samples.append(p)
        self.last_frame = frame_number

    def finish(self, provenance=None):
        if len(self.samples) < 45:
            raise ValueError('Not enough distinct frames; capture needs at least 45 samples. Retry with a healthy stream.')
        points = np.mean(self.samples, axis=0)
        triangle(points)
        jitter = np.sqrt(np.mean(np.sum((np.asarray(self.samples)-points)**2, axis=2), axis=0))
        if max(jitter) > .001:
            raise ValueError('Marker jitter exceeds 1 mm RMS. Stabilize the markers and retry.')
        if self.expected is not None:
            target = self.expected.template if self.expected.template is not None else self.expected.markers
            difference = np.abs(distances(points)-distances(target))
            if max(difference) > .002:
                raise ValueError('Marker spacing differs from saved geometry by more than 2 mm. Use the saved triangle or capture a new reference.')
        origin = points.mean(axis=0)
        x = points[1]-points[0]
        x /= np.linalg.norm(x)
        y = np.cross(x, points[2]-points[0])
        y /= np.linalg.norm(y)
        # Choose the normal nearest tracker +Y by default; expose a manual flip.
        if y[1] < 0:
            y = -y
        if self.flip:
            y = -y
        z = np.cross(x, y)
        result = ReferencePlane(origin, np.column_stack([x, y, z]), points,
            dict(captured_utc=datetime.now(timezone.utc).isoformat(), samples=len(self.samples),
                 marker_jitter_rms_mm=(jitter*1000).tolist(), provenance=provenance or {}))
        if self.expected is not None:
            result.template = (self.expected.template if self.expected.template is not None
                               else self.expected.points(self.expected.markers)).copy()
        return result
