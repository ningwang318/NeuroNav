import math
import unittest

import numpy as np

from neuronav.tracking.motive_api import MotiveTracking, quaternion_from_matrix


class APITests(unittest.TestCase):
    def test_orientation_identity(self):
        np.testing.assert_allclose(quaternion_from_matrix(np.eye(3)), [0, 0, 0, 1])

    def test_orientation_quarter_turn(self):
        matrix = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        np.testing.assert_allclose(quaternion_from_matrix(matrix),
                                   [0, 0, math.sqrt(.5), math.sqrt(.5)], atol=1e-12)

    def test_orientation_half_turn(self):
        q = quaternion_from_matrix(np.diag([1, -1, -1]))
        self.assertAlmostEqual(abs(q[0]), 1)
        np.testing.assert_allclose(q[1:], [0, 0, 0], atol=1e-12)

    def test_reflection_and_nonfinite_matrices_rejected(self):
        for matrix in [np.diag([1, 1, -1]), np.zeros((3, 3)), np.full((3, 3), math.nan)]:
            with self.assertRaises(ValueError):
                quaternion_from_matrix(matrix)

    def test_pipe_frames_preserve_units_and_reject_bad_orientation(self):
        adapter = MotiveTracking('', '', '')
        adapter.accept(dict(type='frame', number=3, markers=[[.1, .2, .3], [math.nan, 0, 0]],
            bodies=[dict(id=0, position=[.2, .3, .4], quaternion=[0, 0, 0, 1],
                         mean_error_m=.0003, valid=True),
                    dict(id=1, position=[0, 0, 0], quaternion=[0, 0, 0, 0],
                         mean_error_m=0, valid=True)]))
        snap = adapter.snapshot()
        self.assertEqual(snap['frames'], 1)
        self.assertEqual(snap['source'], 'api')
        self.assertEqual(snap['frame'].markers, ((.1, .2, .3),))
        self.assertTrue(snap['frame'].bodies[0].valid)
        self.assertFalse(snap['frame'].bodies[1].valid)
        self.assertEqual(snap['frame'].bodies[0].mean_error_m, .0003)

    def test_models_use_sdk_indices_and_names(self):
        adapter = MotiveTracking('', '', '')
        adapter.accept(dict(type='models', names={'0': 'Head', '1': 'Transducer'},
                            cameras=[dict(name='Trio', position=[1, 2, 3], matrix=np.eye(3).ravel().tolist())]))
        self.assertEqual(adapter.snapshot()['models'].names, {0: 'Head', 1: 'Transducer'})
        self.assertEqual(adapter.snapshot()['models'].cameras[0][1], (1, 2, 3))

    def test_engine_failure_is_visible(self):
        adapter = MotiveTracking('', '', '')
        adapter.accept(dict(type='error', message='Initialize: InvalidLicense (6)'))
        self.assertIn('InvalidLicense', adapter.snapshot()['error'])


if __name__ == '__main__':
    unittest.main()
