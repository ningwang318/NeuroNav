import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from unittest.mock import Mock,patch
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets as Qt

from mri_volume import MRIVolume,demo_volume
from mri_3d import MRI3DView,preview_affine,preview_data,cut_face,clip_equation,rgba_data,PLANE_AXES,FACE_AXES,plane_outline,physical_bounds
from mri_workspace import MRIWorkspace


class MRI3DTests(unittest.TestCase):
    def test_texture_initialization_uses_mri_context_and_restores_previous_context(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        view=MRI3DView()
        previous=Mock()
        own=Mock()
        item=object()
        calls=[]
        with patch.object(view,'isValid',return_value=True),patch.object(view,'context',return_value=own), \
             patch.object(view,'makeCurrent',side_effect=lambda:calls.append('make')), \
             patch.object(view,'doneCurrent',side_effect=lambda:calls.append('done')), \
             patch('mri_3d.QtGui.QOpenGLContext.currentContext',return_value=previous), \
             patch.object(MRI3DView.__bases__[0],'addItem',side_effect=lambda i:calls.append('initialize')):
            view.addItem(item)
        self.assertEqual(calls,['make','initialize','done'])
        previous.makeCurrent.assert_called_once_with(previous.surface())
        view.close()

    def test_anatomical_views_are_fixed_width_without_resize_dividers(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        self.assertEqual(workspace.right.minimumWidth(),workspace.right.maximumWidth())
        self.assertFalse(isinstance(workspace.right,Qt.QSplitter))
        self.assertEqual(workspace.right.layout().count(),3)
        self.assertEqual(workspace.split.count(),2)
        workspace.close()

    def test_anatomical_images_cannot_pan_and_compact_canvases_are_square(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        workspace.resize(1100,800)
        workspace.show()
        workspace.set_volume(demo_volume())
        app.processEvents()
        for pane in workspace.panes.values():
            before=pane.box.viewRange()
            drag=Mock()
            pane.box.mouseDragEvent(drag)
            drag.accept.assert_called_once()
            self.assertEqual(pane.box.viewRange(),before)
            self.assertLessEqual(abs(pane.stack.width()-pane.stack.height()),2)
        workspace.clear()
        workspace.close()

    def test_model_and_slice_opacity_change_independently_with_clear_background(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        volume=demo_volume()
        original=volume.data.copy()
        workspace.set_volume(volume)
        view=workspace.volume_view
        old_model=view.volume_item.data.copy()
        old_slice=view.face_item.data.copy()
        self.assertTrue(np.any(old_slice[...,3]==0))
        self.assertLess(old_slice[...,3].max(),255)
        workspace.slice_opacity.setValue(40)
        self.assertEqual(view.face_item.data[...,3].max(),102)
        np.testing.assert_array_equal(view.volume_item.data,old_model)
        changed_slice=view.face_item.data.copy()
        workspace.model_opacity.setValue(65)
        self.assertGreater(view.volume_item.data[...,3].max(),old_model[...,3].max())
        np.testing.assert_array_equal(view.face_item.data,changed_slice)
        background=(old_slice[...,:3].max(axis=-1)==0)
        self.assertTrue(np.all(view.face_item.data[...,3][background]==0))
        np.testing.assert_array_equal(volume.data,original)
        workspace.clear()
        workspace.close()

    def test_overview_plane_intersection_matches_oblique_and_boundary_planes(self):
        volume=demo_volume()
        low,high=physical_bounds(volume)
        for normal,point in [(np.array([1.,2.,3.]),volume.physical([40,50,60])),
                             (np.array([0.,0.,1.]),low)]:
            outline=plane_outline(volume,point,normal)
            self.assertGreaterEqual(len(outline),3)
            self.assertTrue(np.all(outline>=low-1e-6))
            self.assertTrue(np.all(outline<=high+1e-6))
            np.testing.assert_allclose((outline-point) @ normal,0,atol=1e-6)
        self.assertEqual(len(plane_outline(volume,high+10,np.array([1.,0.,0.]))),0)

    def test_overview_stays_complete_and_follows_slice_then_clears(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        workspace.set_volume(demo_volume())
        overview=workspace.volume_view
        self.assertFalse(overview.volume_item.cut_enabled)
        self.assertEqual(set(np.unique(overview.face_item.data[...,3])),{0,166})
        before=overview.plane_line.pos.copy()
        workspace.scroll_3d(4)
        self.assertTrue(before.shape!=overview.plane_line.pos.shape or not np.allclose(before,overview.plane_line.pos))
        point=workspace.volume.physical(workspace.indices)
        np.testing.assert_allclose(overview.plane_line.pos[:,2],point[2],atol=1e-4)
        self.assertFalse(overview.volume_item.cut_enabled)
        workspace.cut_check.setChecked(False)
        self.assertFalse(overview.plane_line.visible())
        self.assertTrue(overview.volume_item.visible())
        workspace.clear()
        self.assertIsNone(overview.volume_item)
        self.assertEqual(overview.overlay_items,[])
        workspace.close()

    def volume(self):
        x,y,z=np.meshgrid(np.arange(5),np.arange(6),np.arange(7),indexing='ij')
        affine=np.diag([1.2,2.3,3.4,1])
        affine[:3,3]=[-30,40,50]
        return MRIVolume((x+10*y+100*z).astype(np.float32),affine,'test')

    def test_preview_bounds_use_mri_ras_mm(self):
        volume=self.volume()
        shape=(3,3,4)
        affine=preview_affine(volume,shape)
        np.testing.assert_allclose((affine @ [0,0,0,1])[:3],volume.physical([0,0,0]))
        np.testing.assert_allclose((affine @ [*shape,1])[:3],volume.physical(np.array(volume.data.shape)-1))

    def test_cut_faces_match_voxel_centres_for_all_planes(self):
        volume=self.volume()
        indices=[2,3,4]
        for plane,axis in PLANE_AXES.items():
            with self.subTest(plane=plane):
                data,affine=cut_face(volume,plane,indices)
                h,v=FACE_AXES[plane]
                source=np.array(indices)
                source[h],source[v]=1,2
                np.testing.assert_allclose((affine @ [1.5,2.5,0,1])[:3],volume.physical(source))
                self.assertEqual(data[1,2],volume.data[tuple(source)])

    def test_clip_boundary_and_reverse_keep_expected_half(self):
        volume=self.volume()
        shape=(3,4,5)
        indices=[2,3,4]
        for plane,axis in PLANE_AXES.items():
            equation=clip_equation(volume,shape,plane,indices)
            boundary=indices[axis]/(volume.data.shape[axis]-1)*shape[axis]
            point=np.zeros(4)
            point[3]=1
            point[axis]=boundary
            self.assertAlmostEqual(np.dot(equation,point),0)
            point[axis]-=.1
            self.assertGreater(np.dot(equation,point),0)
            np.testing.assert_allclose(clip_equation(volume,shape,plane,indices,True),-equation)

    def test_downsample_preview_preserves_endpoints_and_source(self):
        volume=self.volume()
        source=volume.data.copy()
        preview=preview_data(volume,4)
        self.assertEqual(max(preview.shape),4)
        self.assertAlmostEqual(preview[0,0,0],source[0,0,0])
        self.assertAlmostEqual(preview[-1,-1,-1],source[-1,-1,-1])
        rgba_data(preview,(0,650),True)
        np.testing.assert_array_equal(volume.data,source)

    def test_wheel_over_3d_updates_cut_and_linked_2d_views(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        workspace.set_volume(demo_volume())
        workspace.set_active_plane('axial')
        workspace.select('axial',[20,30,40])
        old_line=workspace.panes['coronal'].horizontal.value()
        event=QtGui.QWheelEvent(QtCore.QPointF(30,30),QtCore.QPointF(30,30),
            QtCore.QPoint(),QtCore.QPoint(0,120),QtCore.Qt.NoButton,QtCore.Qt.NoModifier,
            QtCore.Qt.ScrollPhase.NoScrollPhase,False)
        workspace.volume_view.wheelEvent(event)
        self.assertEqual(workspace.indices,[20,30,41])
        self.assertEqual(workspace.panes['axial'].slider.value(),41)
        self.assertAlmostEqual(workspace.panes['coronal'].horizontal.value(),old_line-2)
        self.assertEqual(workspace.volume_view.indices,[20,30,41])
        workspace.set_active_plane('sagittal')
        workspace.scroll_3d(-1)
        self.assertEqual(workspace.indices,[19,30,41])
        self.assertEqual(workspace.panes['sagittal'].slider.value(),19)
        workspace.clear()
        workspace.close()

    def test_cut_face_picking_returns_mri_voxel_not_tracking_coordinates(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        workspace.set_volume(demo_volume())
        workspace.set_active_plane('coronal')
        indices=[20,30,40]
        workspace.select('coronal',indices)
        target=workspace.volume.physical(indices)
        view=workspace.volume_view
        view.setCameraPosition(pos=QtGui.QVector3D(*target),distance=400,elevation=25,azimuth=-55)
        workspace.indices=[10,10,40]
        view.pick_cut(QtCore.QPointF(view.width()/2,view.height()/2))
        self.assertEqual(workspace.indices,indices)
        workspace.clear()
        workspace.close()


if __name__=='__main__':
    unittest.main()
