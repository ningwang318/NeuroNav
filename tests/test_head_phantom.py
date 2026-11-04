import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from PySide6 import QtGui,QtWidgets as Qt
from neuronav.core.head_phantom import PhantomSettings,mri_head_preview
from neuronav.imaging.mri_volume import demo_volume
from neuronav.tracking.reference_plane import ReferencePlane
from neuronav.tracking.natnet import Body,Frame,Models
from app import Workspace,DISPLAY


class HeadPhantomTests(unittest.TestCase):
    def setup_pose(self):
        reference_rotation=Rotation.from_euler('xyz',[12,-26,9],degrees=True).as_matrix()
        reference=ReferencePlane(np.array([.2,.4,-.3]),reference_rotation,np.zeros((3,3)),{})
        right=reference_rotation[:,0]
        up=reference_rotation[:,1]
        cameras=[('Trio',reference.origin+right*.8+up,(0,0,0,1))]
        bottom=reference.origin+right*.04+up*.005
        marker=bottom+up*.23
        body=Body(7,tuple(marker),tuple(Rotation.from_euler('xyz',[38,54,-16],degrees=True).as_quat()),.0004,True)
        return reference,cameras,body,bottom

    def test_assumed_offset_uses_body_local_frame_and_camera_side_heading(self):
        reference,cameras,body,bottom=self.setup_pose()
        settings=PhantomSettings()
        settings.align_upright(body,reference,cameras)
        position,orientation=settings.pose(body)
        np.testing.assert_allclose(position,bottom,atol=1e-10)
        np.testing.assert_allclose(orientation[:,1],reference.basis[:,1],atol=1e-10)
        np.testing.assert_allclose(orientation[:,2],reference.basis[:,0],atol=1e-10)
        self.assertTrue(settings.configured)
        np.testing.assert_allclose(np.linalg.norm(settings.translation_mm),230)

    def test_phantom_follows_rigid_body_motion_without_snapping_to_table(self):
        reference,cameras,body,bottom=self.setup_pose()
        settings=PhantomSettings()
        settings.align_upright(body,reference,cameras)
        old_position,old_rotation=settings.pose(body)
        motion=Rotation.from_euler('xyz',[17,28,4],degrees=True)
        translation=np.array([.1,-.06,.08])
        new_rotation=motion*Rotation.from_quat(body.quaternion)
        moved=Body(7,tuple(motion.apply(body.position)+translation),tuple(new_rotation.as_quat()),.0004,True)
        position,orientation=settings.pose(moved)
        np.testing.assert_allclose(position,motion.apply(old_position)+translation,atol=1e-10)
        np.testing.assert_allclose(orientation,motion.as_matrix() @ old_rotation,atol=1e-10)

    def test_settings_round_trip_and_validation(self):
        reference,cameras,body,_=self.setup_pose()
        settings=PhantomSettings()
        settings.align_upright(body,reference,cameras)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'head.json'
            settings.save(path)
            loaded=PhantomSettings.load(path)
            np.testing.assert_allclose(loaded.pose(body)[0],settings.pose(body)[0])
            np.testing.assert_allclose(loaded.pose(body)[1],settings.pose(body)[1])
            loaded.translation_mm=[float('nan'),0,0]
            with self.assertRaises(ValueError):
                loaded.save(path)

    def test_no_generic_head_is_shown_without_a_loaded_mri(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        reference,cameras,body,_=self.setup_pose()
        view=Workspace()
        view.phantom.align_upright(body,reference,cameras)
        view.render_frame(Frame(1,time.monotonic(),(),(body,),0),True,Models({7:'Head'},[]))
        self.assertEqual(view.phantom_items,[])
        self.assertIsNone(view.phantom_pose)
        view.close()

    def test_scene_replaces_head_axes_and_hides_model_when_tracking_is_lost(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        reference,cameras,body,bottom=self.setup_pose()
        view=Workspace()
        view.set_phantom_mri(demo_volume(),(0,140))
        view.phantom.align_upright(body,reference,cameras)
        models=Models({7:'Head'},[])
        frame=Frame(1,time.monotonic(),(),(body,),0)
        view.render_frame(frame,True,models)
        self.assertEqual(view.dynamic,[])
        self.assertTrue(all(item.visible() for item in view.phantom_items))
        local_origin=np.linalg.solve(view.phantom_mri_transform,[0,0,0,1])[:3]
        point=view.phantom_items[0].transform().map(QtGui.QVector3D(*local_origin))
        np.testing.assert_allclose([point.x(),point.y(),point.z()],DISPLAY @ bottom,atol=1e-7)
        items=list(view.phantom_items)
        view.render_frame(frame,True,models)
        self.assertEqual(items,view.phantom_items)
        view.render_frame(frame,False,models)
        self.assertTrue(all(not item.visible() for item in items))
        self.assertIsNone(view.phantom_pose)
        view.set_phantom_mri(None)
        self.assertEqual(view.phantom_items,[])
        view.close()

    def test_mri_coordinates_map_superior_up_and_anterior_toward_nose(self):
        volume=demo_volume()
        preview,transform,dimensions=mri_head_preview(volume)
        self.assertLessEqual(max(preview.shape),128)
        from neuronav.ui.mri_3d import preview_affine
        to_head=transform @ np.linalg.inv(preview_affine(volume,preview.shape))
        np.testing.assert_allclose(to_head[:3,:3] @ [0,0,10],[0,.01,0])
        np.testing.assert_allclose(to_head[:3,:3] @ [0,10,0],[0,0,.01])
        self.assertGreater(np.linalg.det(to_head[:3,:3]),0)
        self.assertTrue(np.all(dimensions>20))

    def test_adopting_mri_size_preserves_marker_gap_above_head_top(self):
        reference,cameras,body,_=self.setup_pose()
        settings=PhantomSettings()
        settings.align_upright(body,reference,cameras)
        bottom,orientation=settings.pose(body)
        top=bottom+orientation[:,1]*settings.height_mm/1000
        settings.adopt_mri_size([210,170,190])
        new_bottom,new_orientation=settings.pose(body)
        np.testing.assert_allclose(new_bottom+new_orientation[:,1]*.210,top,atol=1e-10)


if __name__=='__main__':
    unittest.main()
