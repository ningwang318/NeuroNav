"""Coordinate-frame conventions and conversions shared by every scene.

Tracker space: Motive's right-handed, Y-up frame in metres; quaternions are
(x, y, z, w). Display space: OpenGL's right-handed, Z-up frame in metres.
Euler angles in persisted settings are extrinsic 'xyz' (fixed X, then Y, then Z), degrees.
"""
import numpy as np
from scipy.spatial.transform import Rotation

from neuronav.tracking.datatypes import Body, Frame, Models


# Motive's right-handed Y-up -> OpenGL's right-handed Z-up, in metres.
DISPLAY = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float)
DISPLAY4 = np.eye(4)
DISPLAY4[:3, :3] = DISPLAY


def world(points):
    """Tracker points (N x 3, Y-up) -> display points (Z-up)."""
    return np.asarray(points, dtype=float).reshape(-1, 3) @ DISPLAY.T


def rotation(quaternion):
    """Quaternion (x, y, z, w), any nonzero norm -> 3x3 rotation. Zero -> identity."""
    q = np.asarray(quaternion, dtype=float)
    if not np.isfinite(q).all():
        return np.full((3, 3), np.nan)
    if np.linalg.norm(q) < 1e-8:
        return np.eye(3)
    return Rotation.from_quat(q).as_matrix()


def quaternion_from_matrix(values):
    """Proper 3x3 rotation -> quaternion tuple (x, y, z, w) with w >= 0.

    Rejects non-finite, non-orthonormal (tolerance 0.01) and reflecting matrices.
    Slightly non-orthonormal input is mapped to the optimal (closest) rotation.
    """
    matrix = np.asarray(values, dtype=float).reshape(3, 3)
    if not np.isfinite(matrix).all() or not np.allclose(matrix.T @ matrix, np.eye(3), atol=.01):
        raise ValueError('Invalid camera orientation matrix')
    if not np.isclose(np.linalg.det(matrix), 1, atol=.01):
        raise ValueError('Camera orientation is not a proper rotation')
    q = Rotation.from_matrix(matrix).as_quat()
    if q[3] < 0:
        q = -q
    return tuple(q.tolist())


def euler_xyz(degrees):
    """Extrinsic xyz Euler angles in degrees -> 3x3 rotation."""
    return Rotation.from_euler('xyz', degrees, degrees=True).as_matrix()


def euler_xyz_degrees(matrix):
    """3x3 rotation -> extrinsic xyz Euler angles in degrees (numpy array)."""
    return Rotation.from_matrix(matrix).as_euler('xyz', degrees=True)


def pose_matrix(rotation_matrix, translation):
    """4x4 homogeneous transform from a 3x3 rotation and a translation."""
    matrix = np.eye(4)
    matrix[:3, :3] = rotation_matrix
    matrix[:3, 3] = translation
    return matrix


def body_matrix(body):
    """4x4 rigid-body pose in tracker metres."""
    return pose_matrix(rotation(body.quaternion), body.position)


def name_matches(models, body_id, name):
    return models.names.get(body_id, '').casefold() == name.casefold()


def body_named(frame, models, name):
    """The single valid body whose asset name matches ``name`` (case-insensitive), else None."""
    if frame is None or models is None:
        return None
    matches = [body for body in frame.bodies if body.valid and name_matches(models, body.id, name)]
    return matches[0] if len(matches) == 1 else None


def reference_data(reference, frame, models):
    """Express a tracker frame and camera poses in the three-marker reference frame."""
    if reference is None:
        return frame, models
    cameras = [(name, tuple(reference.points([pos])[0]),
                quaternion_from_matrix(reference.orientation(rotation(quat))))
               for name, pos, quat in models.cameras]
    converted = None
    if frame:
        bodies = tuple(Body(b.id, tuple(reference.points([b.position])[0]),
                            quaternion_from_matrix(reference.orientation(rotation(b.quaternion))),
                            b.mean_error_m, b.valid) for b in frame.bodies)
        converted = Frame(frame.number, frame.received,
                          tuple(map(tuple, reference.points(frame.markers))), bodies, frame.unlabeled_count)
    return converted, Models(models.names, cameras)
