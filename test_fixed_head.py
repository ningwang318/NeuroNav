import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import numpy as np
from scipy.spatial.transform import Rotation
from fixed_head import FixedHead, crown_marker
from reference_plane import ReferencePlane
from mri_volume import demo_volume
from head_phantom import mri_head_preview
from fixed_head import estimate_mri_crown


class FixedHeadTests(unittest.TestCase):
    def setup_capture(self):
        reference = ReferencePlane.load('config/reference_plane.json')
        volume = demo_volume()
        volume.source_sha256 = 'test-mri'
        marker_local = np.array([.01, .22, .03])
        marker = reference.origin+reference.basis@marker_local
        fixed = FixedHead.capture(np.tile(marker, (100, 1)), reference, [0, .21, 0],
                                  [0, 0, 100], volume)
        return reference, volume, marker, fixed

    def test_crown_is_remaining_marker_independent_of_stream_order(self):
        reference, _, marker, _ = self.setup_capture()
        points = np.vstack([reference.markers, marker])[[2, 3, 0, 1]]
        np.testing.assert_allclose(crown_marker(points, reference), marker)
        with self.assertRaises(ValueError):
            crown_marker(points[:3], reference)
        points[0] += .02
        with self.assertRaises(ValueError):
            crown_marker(points, reference)

    def test_gap_heading_and_pose_survive_marker_removal_and_reference_relocation(self):
        reference, volume, marker, fixed = self.setup_capture()
        origin, basis = fixed.pose(reference, volume)
        np.testing.assert_allclose(origin+basis@[0, .21, 0]+reference.basis[:, 1]*.0075, marker)
        forward = reference.markers[1]-marker
        up = reference.basis[:, 1]
        forward -= up*np.dot(forward, up)
        np.testing.assert_allclose(basis[:, 2], forward/np.linalg.norm(forward))
        # No frame or crown observation is consumed when resolving a saved pose.
        motion = Rotation.from_euler('xyz', [13, 22, -7], degrees=True).as_matrix()
        shift = np.array([.2, -.1, .3])
        moved = ReferencePlane(motion@reference.origin+shift, motion@reference.basis,
                               reference.markers@motion.T+shift, {})
        p, r = fixed.pose(moved, volume)
        np.testing.assert_allclose(p, motion@origin+shift)
        np.testing.assert_allclose(r, motion@basis)
        volume.source_sha256 = 'different-mri'
        self.assertIsNone(fixed.pose(reference, volume))

    def test_crown_estimate_and_render_with_no_head_body_or_fourth_marker(self):
        from PySide6 import QtWidgets as Qt
        from app import Workspace
        from tracking import Models
        app = Qt.QApplication.instance() or Qt.QApplication([])
        reference, volume, _, fixed = self.setup_capture()
        preview, transform, _ = mri_head_preview(volume)
        crown, ras = estimate_mri_crown(volume, transform, preview.shape)
        self.assertTrue(np.isfinite(crown).all())
        self.assertGreater(crown[1], .05)
        view = Workspace()
        view.set_reference(reference)
        view.fixed_head = fixed
        view.set_phantom_mri(volume, volume.levels())
        view.render_frame(None, False, Models())
        self.assertIsNotNone(view.phantom_pose)
        self.assertTrue(all(item.visible() for item in view.phantom_items))
        view.set_reference(None)
        view.render_frame(None, False, Models())
        self.assertIsNone(view.phantom_pose)
        view.close()


if __name__ == '__main__':
    unittest.main()
