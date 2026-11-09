"""Identify the radial holder markers and solve its centre in tracker metres."""
from itertools import permutations
import numpy as np

RADII = np.array([.075, .105, .135])
EXPECTED = np.sqrt(RADII[0]**2+RADII[1:]**2-
                   2*RADII[0]*RADII[1:]*np.cos(np.radians([30.,45.])))


def solve_holder(markers, reference, cameras, crown_tracker=None):
    if reference is None or not cameras:
        raise ValueError('Table reference and camera position are required.')
    points = np.asarray(markers, float).reshape(-1, 3)
    if not np.isfinite(points).all():
        raise ValueError('Invalid marker positions.')
    keep = np.min(np.linalg.norm(points[:, None]-reference.markers[None], axis=2), axis=1) > .003
    if crown_tracker is not None:
        keep &= np.linalg.norm(points-np.asarray(crown_tracker), axis=1) > .003
    points = points[keep]
    if len(points) != 3:
        raise ValueError(f'Expected three holder markers after excluding table/crown; found {len(points)}.')
    candidates = []
    for order in permutations(range(3)):
        p = points[list(order)]
        errors = np.linalg.norm(p[1:]-p[0], axis=1)-EXPECTED
        candidates.append((float(np.linalg.norm(errors)), p, errors))
    candidates.sort(key=lambda item: item[0])
    score, p, errors = candidates[0]
    if max(abs(errors)) > .002:
        raise ValueError('Observed spacing does not match the 75/105/135 mm radii and 30°/45° angles within 2 mm.')
    if candidates[1][0]-score < .001:
        raise ValueError('Marker identities are ambiguous.')
    # Trilateration of the three known centre-to-marker radii.
    a, b, c = p
    d = np.linalg.norm(b-a)
    ex = (b-a)/d
    i = np.dot(ex, c-a)
    perpendicular = c-a-i*ex
    j = np.linalg.norm(perpendicular)
    if j < .005:
        raise ValueError('Holder marker triangle is too nearly collinear.')
    ey = perpendicular/j
    normal = np.cross(ex, ey)
    x = (RADII[0]**2-RADII[1]**2+d*d)/(2*d)
    y = (RADII[0]**2-RADII[2]**2+i*i+j*j-2*i*x)/(2*j)
    h2 = RADII[0]**2-x*x-y*y
    if h2 <= .000005**2:
        raise ValueError('No distinct sphere-centre solutions fit the specified radii.')
    foot = a+x*ex+y*ey
    camera = np.mean([c[1] for c in cameras], axis=0)
    camera_side = np.dot(camera-a, normal)
    if abs(camera_side) < .01:
        raise ValueError('Camera is too close to the marker plane to select its rear side.')
    centre = foot-np.sign(camera_side)*np.sqrt(h2)*normal
    # Holder axes are geometric axes, not the transducer beam orientation.
    z = (a-centre)/RADII[0]
    axis_x = (b-centre)-z*np.dot(b-centre,z)
    axis_x /= np.linalg.norm(axis_x)
    orientation = np.column_stack([axis_x, np.cross(z,axis_x), z])
    edges = np.linalg.norm(p[[0,0,1]]-p[[1,2,2]], axis=1)
    angles = np.degrees(np.arccos(np.clip([
        np.dot((p[u]-centre)/RADII[u],(p[v]-centre)/RADII[v])
        for u,v in [(0,1),(0,2),(1,2)]],-1,1)))
    return dict(centre=centre, markers=p, orientation=orientation,
                edges_mm=edges*1000, angles_deg=angles,
                identity_errors_mm=errors*1000, plane_offset_mm=float(np.sqrt(h2)*1000))
