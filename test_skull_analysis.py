import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import time
import numpy as np
from PySide6 import QtWidgets as Qt
from mri_volume import MRIVolume
from skull_analysis import (analyze_t1, demo_t1, SkullOptions, incidence,
                            ray_hits, window_labels, fit_normals, angle_colors)


def mesh_colors(dialog):
    return dialog.items[0].opts['meshdata'].vertexColors()


def display_colors(result, opacity=.25):
    colors=angle_colors(result)
    colors[:,3]*=opacity
    return colors


class SkullMorphologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.volume = demo_t1()
        cls.result = analyze_t1(cls.volume, [0, 0, 0], SkullOptions(directions=1600))

    def test_centered_shell_angles_and_estimated_lengths(self):
        r = self.result
        self.assertGreater(np.isfinite(r.path_mm).sum(), 500)
        self.assertLess(np.nanpercentile(r.outer_angle, 90), 4)
        self.assertLess(abs(np.nanmedian(r.path_mm)-7), 1.5)
        self.assertLess(abs(np.nanmedian(r.normal_mm)-7), 1.5)
        self.assertTrue(np.all(r.outer[:, 2] > 0))
        self.assertTrue(np.any(window_labels(r, 25) >= 0))

    def test_off_center_target_changes_incidence_and_path(self):
        r = analyze_t1(self.volume, [35, 0, 0], SkullOptions(directions=1600))
        self.assertGreater(np.nanmedian(r.outer_angle), 15)
        self.assertGreater(np.nanmedian(r.path_mm), np.nanmedian(self.result.path_mm))
        self.assertLess(np.sum(r.eligible(10)), np.sum(r.eligible(30)))
        # The shape is unchanged, but target-dependent colours/windows must move.
        np.testing.assert_array_equal(r.outer, self.result.outer)
        self.assertGreater(np.sum(np.any(np.abs(angle_colors(r)-angle_colors(self.result)) > .05, axis=1)), 300)
        self.assertLess(np.sum(r.eligible(25)), np.sum(self.result.eligible(25)))

    def test_physical_translation_preserves_geometry(self):
        shift = np.array([31., -17., 9.])
        affine = self.volume.affine.copy(); affine[:3, 3] += shift
        translated = MRIVolume(self.volume.data, affine, 'translated')
        r = analyze_t1(translated, shift, SkullOptions(directions=1600))
        np.testing.assert_allclose(r.outer-shift, self.result.outer, atol=1e-8)
        np.testing.assert_allclose(r.path_mm, self.result.path_mm, atol=1e-6)

    def test_anisotropic_shell(self):
        spacing = np.array([1., 1.5, 2.])
        shape = np.array([185, 125, 95])
        coords = (np.indices(shape).transpose(1, 2, 3, 0)-(shape-1)/2)*spacing
        radius = np.linalg.norm(coords, axis=-1)
        data = np.zeros(shape, np.float32)
        data[radius < 86] = 100; data[radius < 80] = 5; data[radius < 73] = 120
        affine = np.diag([*spacing, 1.]); affine[:3, 3] = -(shape-1)/2*spacing
        r = analyze_t1(MRIVolume(data, affine, 'anisotropic'), [0, 0, 0], SkullOptions(directions=1600))
        self.assertLess(np.nanpercentile(r.outer_angle, 90), 5)
        self.assertLess(abs(np.nanmedian(r.path_mm)-7), 1.5)

    def test_brain_only_does_not_fabricate_a_skull(self):
        v = self.volume
        data = np.where(v.data == 120, 120, 0).astype(np.float32)
        with self.assertRaisesRegex(ValueError, 'Too few supported'):
            analyze_t1(MRIVolume(data, v.affine, 'skull stripped'), [0, 0, 0], SkullOptions(directions=800))

    def test_cancel_and_invalid_target(self):
        with self.assertRaises(InterruptedError):
            analyze_t1(self.volume, [0, 0, 0], cancel=lambda: True)
        with self.assertRaisesRegex(ValueError, 'outside'):
            analyze_t1(self.volume, [1000, 0, 0])
        with self.assertRaises(ValueError):
            analyze_t1(self.volume, [0, 0, 0], SkullOptions(min_band_mm=15, max_band_mm=5))

    def test_plane_normals_and_ray_intersection(self):
        x, y = np.meshgrid(np.arange(-10, 11, 2), np.arange(-10, 11, 2))
        points = np.column_stack([x.ravel(), y.ravel(), np.full(x.size, 7.)])
        normals, errors = fit_normals(points, 8)
        self.assertAlmostEqual(incidence(np.array([0., 0., 1.]), normals[len(points)//2]), 0)
        vertices = np.array([[-10., -10., 7.], [10., -10., 7.], [0., 10., 7.]])
        distance, normal = ray_hits(np.zeros(3), np.array([0., 0., 1.]), vertices, np.array([[0, 1, 2]]))
        self.assertAlmostEqual(distance, 7)
        self.assertAlmostEqual(incidence(np.array([0., 0., 1.]), normal), 0)
        self.assertTrue(np.isnan(ray_hits(np.zeros(3), np.array([1., 0., 0.]), vertices, np.array([[0, 1, 2]]))[0]))

    def test_unreviewed_angle_map_is_visible_and_boundaries_are_separate(self):
        from mri_workspace import MRIWorkspace
        app = Qt.QApplication.instance() or Qt.QApplication([])
        w = MRIWorkspace(Qt.QWidget())
        w.set_volume(self.volume)
        w.open_skull_analysis()
        d = w.skull_dialog
        d.commit_target()
        target = d.target.copy()
        w.indices = [60, 60, 70]
        w.render()
        np.testing.assert_array_equal(d.target, target)
        d.result = self.result
        d.refresh()
        self.assertGreater(d.table.rowCount(), 0)
        self.assertFalse(d.review.isChecked())
        np.testing.assert_allclose(mesh_colors(d), display_colors(self.result))
        self.assertIn('INSERTION ANGLES', w.skull_status.text())
        self.assertIn('UNREVIEWED', w.skull_status.text())
        self.assertEqual(d.table.currentRow(), 0)
        d.display_mode.setCurrentIndex(1)
        np.testing.assert_allclose(mesh_colors(d), np.tile([1., .55, .12, .25], (len(self.result.outer), 1)))
        d.display_mode.setCurrentIndex(0)
        d.review.setChecked(True)
        np.testing.assert_allclose(mesh_colors(d), display_colors(self.result))
        self.assertGreater(d.table.rowCount(), 0)
        d.dark.setValue(.3)
        self.assertIsNone(d.result)
        self.assertFalse(d.review.isChecked())
        self.assertEqual(len(d.items), 0)
        self.assertTrue(w.skull_status.isHidden())
        w.clear()
        self.assertIsNone(d.target)
        d.close(); w.close()

    def test_background_job_lifecycle_and_volume_change_discards_result(self):
        from mri_workspace import MRIWorkspace
        app = Qt.QApplication.instance() or Qt.QApplication([])
        w = MRIWorkspace(Qt.QWidget())
        w.set_volume(self.volume); w.open_skull_analysis()
        d = w.skull_dialog
        d.commit_target(); d.start()
        deadline = time.monotonic()+15
        while w.jobs and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.005)
        self.assertFalse(w.jobs)
        self.assertIsNone(d.worker)
        self.assertIsNotNone(d.result)
        self.assertFalse(d.review.isChecked())
        np.testing.assert_allclose(mesh_colors(d), display_colors(d.result))
        first_colors = mesh_colors(d).copy()
        d.review.setChecked(True)
        first_result = d.result
        first_count = first_result.eligible(25).sum()
        # A moved crosshair is visibly pending; applying it automatically
        # recalculates and preserves review of the unchanged MRI boundaries.
        w.indices = [87, 64, 64]
        w.render()
        self.assertIn('from committed target', d.crosshair_label.text())
        self.assertIn('recalculate', d.target_button.text())
        # Calculate must use the new crosshair even without pressing Set target.
        d.calculate.click()
        deadline = time.monotonic()+15
        while w.jobs and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.005)
        self.assertIsNotNone(d.result)
        self.assertTrue(d.review.isChecked())
        np.testing.assert_allclose(d.result.target, w.volume.physical(w.indices))
        self.assertLess(d.result.eligible(25).sum(), first_count)
        self.assertGreater(np.sum(np.any(np.abs(mesh_colors(d)-first_colors) > .05, axis=1)), 300)
        self.assertIn('Target moved', d.status.text())
        self.assertGreater(d.table.rowCount(), 0)
        self.assertEqual(d.table.currentRow(), 0)
        current = d.result
        d.commit_target()
        self.assertIs(d.result, current)
        self.assertIn('unchanged', d.status.text())
        d.start()
        w.clear()
        while w.jobs and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.005)
        self.assertFalse(w.jobs)
        self.assertIsNone(d.result)
        self.assertIsNone(d.target)
        d.close(); w.close()

    def test_contour_visibility_opacity_and_slice_intersections(self):
        from mri_workspace import MRIWorkspace
        from skull_dialog import surface_slice_segments
        import pyqtgraph.opengl as gl
        app=Qt.QApplication.instance() or Qt.QApplication([])
        w=MRIWorkspace(Qt.QWidget()); w.set_volume(self.volume); w.open_skull_analysis()
        d=w.skull_dialog; d.commit_target(); d.result=self.result; d.refresh()
        self.assertIsInstance(d.items[0],gl.GLMeshItem)
        np.testing.assert_array_equal(d.items[0].opts['meshdata'].faces(),self.result.faces)
        w.skull_opacity.setValue(10)
        np.testing.assert_allclose(mesh_colors(d),display_colors(self.result,.10))
        result=d.result
        w.skull_contour.setChecked(False)
        self.assertFalse(any(isinstance(item,gl.GLMeshItem) for item in d.items))
        self.assertTrue(any(isinstance(item,gl.GLScatterPlotItem) for item in d.items))
        for pane in w.panes.values():
            self.assertTrue(pane.skull_outer.xData is None or len(pane.skull_outer.xData)==0)
        w.skull_contour.setChecked(True)
        self.assertIs(d.result,result)
        self.assertIsInstance(d.items[0],gl.GLMeshItem)
        w.skull_opacity.setValue(0)
        self.assertFalse(any(isinstance(item,gl.GLMeshItem) for item in d.items))
        vertices=np.array([[0.,0.,-1.],[2.,0.,1.],[0.,2.,1.], [9.,9.,-1.]])
        segments=surface_slice_segments(vertices,np.array([[0,1,2]]),2,0.)
        np.testing.assert_allclose(segments,[[1,0,0],[0,1,0]])
        self.assertEqual(len(surface_slice_segments(vertices,np.array([[0,1,2]]),2,3.)),0)
        d.close(); w.close()


if __name__ == '__main__':
    unittest.main()
