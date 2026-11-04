"""Exercise capture/save/apply with synthetic frames, never the physical tracker."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import unittest

import numpy as np
from PySide6 import QtWidgets as Qt

from neuronav.ui.main_window import MainWindow
from neuronav.core.frames import world
from PySide6 import QtGui
from neuronav.tracking.natnet import Frame, Models
from neuronav.tracking.reference_plane import ReferencePlane


class ReferenceGUITest(unittest.TestCase):
    def test_capture_uses_receiver_history_and_applies_saved_reference(self):
        application = Qt.QApplication.instance() or Qt.QApplication([])
        points = ((.1, .2, .3), (.3, .2, .3), (.17, .2, .42))
        now = time.monotonic()
        frames = [Frame(i, now+i*.008, points, (), 0) for i in range(61)]
        class Receiver:
            def snapshot(self):
                return dict(frame=frames[0], error='')
            def frames_since(self, received):
                return [frame for frame in frames if frame.received > received]
            def stop(self):
                pass
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(auto_connect=False)
            window.timer.stop()
            window.receiver = Receiver()
            window.reference_path = Path(directory)/'plane.json'
            window.last_snapshot = dict(frame=frames[0], models=Models())
            window.open_reference_dialog()
            window.start_reference_capture(False)
            window.capture_started = time.monotonic()-2.1
            window.advance_reference_capture(frames[-1], True)
            self.assertIsNotNone(window.reference)
            saved = ReferencePlane.load(window.reference_path)
            np.testing.assert_allclose(saved.points(points)[:, 1], 0, atol=1e-12)
            self.assertIn('61 samples', window.reference_status.text())
            self.assertIn('Three-marker reference', window.coordinate_label.text())
            self.assertEqual(len(window.view.reference_items), 0)
            ref = window.reference
            np.testing.assert_allclose(window.view.origin_items[0].pos,
                world([ref.origin, ref.origin+ref.basis[:,0]*.25]), atol=1e-7)
            local = np.array([.07, 0, .11])
            mapped = window.view.grid.transform().map(QtGui.QVector3D(*world([local])[0]))
            np.testing.assert_allclose([mapped.x(),mapped.y(),mapped.z()],
                world([ref.origin+ref.basis @ local])[0], atol=1e-7)
            window.toggle_saved_geometry(True)
            self.assertEqual(len(window.view.reference_items), 4)
            cameras=[('Trio',tuple(ref.origin+[.8,1,.4]),(0,0,0,1))]
            window.last_snapshot=dict(frame=frames[0],models=Models({},cameras))
            window.view.update_setup(cameras)
            window.fit_scene()
            normal=ref.basis[:,1]
            right=np.asarray(cameras[0][1])-ref.origin
            right-=normal*np.dot(right,normal)
            right/=np.linalg.norm(right)
            back=np.cross(right,normal)
            table,_=window.view.table_layout.frame(ref)
            mapped=window.view.viewMatrix().map(QtGui.QVector3D(*world([table])[0]))
            self.assertAlmostEqual(mapped.x(),0,places=5)
            self.assertAlmostEqual(mapped.y(),0,places=5)
            upright=window.view.viewMatrix().mapVector(QtGui.QVector3D(*world([normal])[0]))
            self.assertAlmostEqual(upright.x(),0,places=5)
            window.reference_dialog.close()
            window.close()


if __name__ == '__main__':
    unittest.main()
