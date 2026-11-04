from itertools import permutations
from pathlib import Path
import tempfile
import unittest

import numpy as np

from reference_plane import Capture, ReferencePlane, triangle, distances, visible_markers


POINTS = np.array([[0., 0., 0.], [.2, 0., 0.], [.07, 0., .12]]) + [.1, .2, .3]


def captured(points=POINTS, expected=None, flip=False):
    capture = Capture(expected, flip)
    for i in range(60):
        capture.add(points[list(list(permutations(range(3)))[i % 6])], i)
    return capture.finish()


class ReferenceTests(unittest.TestCase):
    def test_reference_filter_hides_only_stationary_matches(self):
        ref = captured()
        extra = np.array([[1,2,3], POINTS[0]+[.004,0,0]])
        observations = np.vstack([POINTS+0.00005, extra])
        before = observations.copy()
        result, hidden = visible_markers(observations, ref)
        self.assertEqual(hidden, 3)
        np.testing.assert_allclose(result, extra)
        np.testing.assert_array_equal(observations, before)
        self.assertEqual(visible_markers(observations, ref, False)[1], 0)
        self.assertEqual(visible_markers(observations, None)[1], 0)

    def test_reference_filter_never_hides_multiple_points_for_one_reference(self):
        ref = captured()
        points = np.vstack([ref.markers[0], ref.markers[0]+[.0001,0,0]])
        result, hidden = visible_markers(points, ref)
        self.assertEqual(hidden, 1)
        self.assertEqual(len(result), 1)

    def test_unordered_markers_produce_same_reference(self):
        reference = captured()
        np.testing.assert_allclose(reference.origin, POINTS.mean(axis=0), atol=1e-12)
        np.testing.assert_allclose(reference.points(POINTS)[:, 1], 0, atol=1e-12)
        np.testing.assert_allclose(reference.basis.T @ reference.basis, np.eye(3), atol=1e-12)
        self.assertAlmostEqual(np.linalg.det(reference.basis), 1)

    def test_flip_normal_preserves_right_handed_frame(self):
        a, b = captured(), captured(flip=True)
        np.testing.assert_allclose(a.basis[:, 0], b.basis[:, 0])
        np.testing.assert_allclose(a.basis[:, 1:], -b.basis[:, 1:])
        self.assertAlmostEqual(np.linalg.det(b.basis), 1)

    def test_motion_and_missing_markers_rejected(self):
        capture = Capture()
        capture.add(POINTS, 1)
        with self.assertRaises(ValueError):
            capture.add(POINTS + [.004, 0, 0], 2)
        with self.assertRaises(ValueError):
            capture.add(POINTS[:2], 3)

    def test_collinear_and_symmetric_rejected(self):
        for points in [[[0,0,0], [.1,0,0], [.3,0,0]],
                       [[0,0,0], [.1,0,0], [.05,0,.08]]]:
            with self.assertRaises(ValueError):
                triangle(points)

    def test_duplicate_frames_do_not_count(self):
        capture = Capture()
        for _ in range(60):
            capture.add(POINTS, 1)
        with self.assertRaises(ValueError):
            capture.finish()

    def test_save_reload_and_recalibrate_moved_triangle(self):
        original = captured()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'reference.json'
            original.save(path)
            saved = ReferencePlane.load(path)
            angle = .7
            r = np.array([[np.cos(angle), 0, np.sin(angle)], [0,1,0], [-np.sin(angle),0,np.cos(angle)]])
            moved = POINTS @ r.T + [1, 2, 3]
            again = captured(moved, saved)
            np.testing.assert_allclose(again.points(again.markers), original.points(original.markers), atol=1e-12)
            np.testing.assert_allclose(again.template, saved.template)

    def test_wrong_saved_triangle_rejected(self):
        expected = captured()
        with self.assertRaises(ValueError):
            captured(POINTS*1.1, expected)

    def test_lengths_preserved_by_transform(self):
        ref = captured()
        np.testing.assert_allclose(distances(ref.points(POINTS)), distances(POINTS), atol=1e-12)

    def test_scene_and_pose_table_use_same_transform(self):
        import time
        from app import reference_data, rotation
        from tracking import Body, Frame, Models
        ref = captured()
        body = Body(0, tuple(ref.origin), (0,0,0,1), .0002, True)
        raw = Frame(1, time.monotonic(), tuple(map(tuple, POINTS)), (body,), 0)
        models = Models({0:'Head'}, [('Camera', tuple(ref.origin+[0,1,0]), (0,0,0,1))])
        transformed, converted_models = reference_data(ref, raw, models)
        np.testing.assert_allclose(transformed.bodies[0].position, [0,0,0], atol=1e-12)
        np.testing.assert_allclose(rotation(transformed.bodies[0].quaternion), ref.basis.T, atol=1e-12)
        np.testing.assert_allclose(transformed.markers, ref.points(POINTS), atol=1e-12)
        np.testing.assert_allclose(converted_models.cameras[0][1], ref.points([models.cameras[0][1]])[0])
        self.assertEqual(transformed.bodies[0].mean_error_m, .0002)


if __name__ == '__main__':
    unittest.main()
