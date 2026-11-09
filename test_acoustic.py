import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import copy,deepcopy
from pathlib import Path
import time
import unittest
import numpy as np
from PySide6 import QtGui,QtWidgets as Qt
from acoustic_field import AcousticField,AcousticPlacement,transducer_geometry
from acoustic_scene import tracker_from_mri
from mri_volume import demo_volume
from tracking import Frame,Body,Models
from app import MainWindow,DISPLAY


ROOT=Path(__file__).parent


class AcousticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=AcousticField(ROOT/'config'/'transducer_500khz.json')

    def field(self):
        result=copy(self.original)
        result.profile=deepcopy(result.profile)
        result.set_axial_reversed(False)
        result.profile.update(axial_spacing_mm=.5,lateral1_spacing_mm=.25,lateral2_spacing_mm=.3,
                              near_offset_mm=2.,metric_confirmed=True)
        return result

    def test_axial_matrix_reversal_reflects_meshes_and_peak_without_changing_colors(self):
        field=self.field()
        original=field.volume.copy()
        vertices=field.surfaces[-3]['vertices'].copy()
        faces=field.surfaces[-3]['faces'].copy()
        peak=field.peak.copy()
        colors={db:mesh['color'] for db,mesh in field.surfaces.items()}
        field.set_axial_reversed(True)
        np.testing.assert_array_equal(field.volume,original[::-1,:,:])
        np.testing.assert_allclose(field.surfaces[-3]['vertices'][:,0],201-vertices[:,0])
        np.testing.assert_array_equal(field.surfaces[-3]['faces'],faces[:,[0,2,1]])
        np.testing.assert_allclose(field.peak[:2],peak[:2])
        self.assertAlmostEqual(field.peak[2]+peak[2],199*.5+4)
        self.assertEqual(colors,{db:mesh['color'] for db,mesh in field.surfaces.items()})
        field.set_axial_reversed(False)
        np.testing.assert_array_equal(field.volume,original)
        np.testing.assert_allclose(field.surfaces[-3]['vertices'],vertices,atol=1e-12)

    def test_far_to_near_reversal_and_matlab_one_based_mesh_coordinates(self):
        field=self.field()
        actual=field.local_points([[200,40.5,40.5],[1,40.5,40.5],[199,41.5,41.5]])
        np.testing.assert_allclose(actual,[[0,0,2],[0,0,101.5],[.25,-.3,2.5]])
        np.testing.assert_allclose(field.peak,[-.125,-.15,44.5])

    def test_original_figure_colors_opacities_and_topology_are_preserved(self):
        field=self.field()
        for db,color,alpha in [(-3,[.91,0,0],.7),(-6,[.66,1,.09],.5),(-12,[0,.7,1],.3)]:
            mesh=field.surfaces[db]
            np.testing.assert_allclose(mesh['color'][:3],color)
            self.assertAlmostEqual(mesh['color'][3],alpha)
            self.assertEqual(mesh['faces'].shape[1],3)
            self.assertLess(mesh['faces'].max(),len(mesh['vertices']))

    def test_unknown_sampling_cannot_silently_be_treated_as_millimetres(self):
        field=self.field()
        field.profile['metric_confirmed']=False
        self.assertFalse(field.calibrated)
        with self.assertRaises(ValueError):
            field.local_points([[115,40,41]])

    def test_manual_motion_keeps_measured_field_fixed_to_transducer(self):
        field=self.field()
        placement=AcousticPlacement()
        volume=demo_volume()
        placement.center_focus(field,volume)
        initial=placement.manual_matrix()
        np.testing.assert_allclose((initial @ np.r_[field.peak,1])[:3],volume.physical([47.5]*3),atol=1e-10)
        placement.beam_shift_mm=15
        moved=placement.manual_matrix()
        np.testing.assert_allclose(moved[:3,3]-initial[:3,3],initial[:3,2]*15,atol=1e-12)
        np.testing.assert_allclose(np.linalg.inv(moved) @ moved @ np.r_[field.peak,1],np.r_[field.peak,1])
        target=np.array([12.,-34.,56.])
        placement.focus_at(field,target)
        np.testing.assert_allclose((placement.manual_matrix() @ np.r_[field.peak,1])[:3],target,atol=1e-10)

    def test_aperture_bowl_faces_downstream_and_has_64_mm_aperture(self):
        bowl,housing=transducer_geometry(64,63.2)
        self.assertAlmostEqual(np.ptp(bowl['vertices'][:,0]),64)
        self.assertAlmostEqual(bowl['vertices'][:,2].min(),0)
        self.assertGreater(bowl['vertices'][:,2].max(),0)
        self.assertLess(housing['vertices'][:,2].min(),0)

    def test_mri_and_tracker_scenes_share_the_same_focal_transform(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        window=MainWindow(auto_connect=False)
        window.timer.stop()
        # Avoid changing the user's stored phantom dimensions in a test.
        window.phantom.use_mri_size=False
        window.mri_workspace.set_volume(demo_volume())
        window.acoustic.rebuild(self.field())
        window.acoustic.placement.mode='manual'
        window.view.phantom_pose=(np.array([.3,.5,.7]),np.eye(3))
        frame=Frame(1,time.monotonic(),(),(),0)
        window.acoustic.update(frame,Models(),True)
        point=window.acoustic.field.peak
        mri_item=window.acoustic.mri_layer.threshold_items[-3]
        tracker_item=window.acoustic.tracking_layer.threshold_items[-3]
        mri=mri_item.transform().map(QtGui.QVector3D(*point))
        tracker=tracker_item.transform().map(QtGui.QVector3D(*point))
        mri_point=np.array([mri.x(),mri.y(),mri.z()])
        expected=DISPLAY @ (tracker_from_mri(window.view) @ np.r_[mri_point,1])[:3]
        np.testing.assert_allclose([tracker.x(),tracker.y(),tracker.z()],expected,atol=1e-6)
        self.assertTrue(mri_item.visible())
        window.acoustic.update(None,None,False)
        self.assertFalse(tracker_item.visible())
        window.acoustic.placement.mode='tracked'
        window.acoustic.placement.marker_calibrated=False
        window.acoustic.update()
        self.assertFalse(mri_item.visible())
        window.acoustic.placement.marker_calibrated=True
        transducer=Body(12,(.4,.6,.8),(0,0,0,1),.0001,True)
        window.acoustic.update(Frame(2,time.monotonic(),(),(transducer,),0),Models({12:'Transducer'}),True)
        point=tracker_item.transform().map(QtGui.QVector3D(*window.acoustic.field.peak))
        np.testing.assert_allclose([point.x(),point.y(),point.z()],
                                  DISPLAY @ (np.array(transducer.position)+window.acoustic.field.peak/1000),atol=1e-6)
        window.acoustic.update(None,None,False)
        self.assertFalse(mri_item.visible())
        window.close()

    def test_live_preview_follows_markers_while_mri_camera_and_brain_stay_fixed(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        window=MainWindow(auto_connect=False)
        window.timer.stop()
        window.phantom.use_mri_size=False
        window.mri_workspace.set_volume(demo_volume())
        window.acoustic.rebuild(self.field())
        controller=window.acoustic
        controller.placement.mode='preview'
        controller.placement.marker_calibrated=False
        window.view.phantom_pose=(np.array([.2,.3,.4]),np.eye(3))
        view=window.mri_workspace.volume_view
        camera=(view.opts['azimuth'],view.opts['elevation'],view.opts['distance'],
                view.opts['center'].x(),view.opts['center'].y(),view.opts['center'].z())
        brain=view.volume_item.transform()
        first=Body(4,(.3,.4,.5),(0,0,0,1),.0001,True)
        models=Models({4:'Transducer'})
        controller.update(Frame(1,time.monotonic(),(),(first,),0),models,True)
        initial=controller.mri_pose.copy()
        displacement=np.array([.01,-.02,.03])
        moved=Body(4,tuple(np.asarray(first.position)+displacement),first.quaternion,.0001,True)
        controller.update(Frame(2,time.monotonic(),(),(moved,),0),models,True)
        np.testing.assert_allclose(controller.tracker_pose[:3,3],moved.position)
        expected=np.linalg.inv(tracker_from_mri(window.view))[:3,:3] @ displacement
        np.testing.assert_allclose(controller.mri_pose[:3,3]-initial[:3,3],expected,atol=1e-10)
        self.assertTrue(controller.tracking_layer.items[0].visible())
        self.assertTrue(controller.mri_layer.items[0].visible())
        self.assertEqual(camera,(view.opts['azimuth'],view.opts['elevation'],view.opts['distance'],
                                 view.opts['center'].x(),view.opts['center'].y(),view.opts['center'].z()))
        self.assertEqual(view.volume_item.transform(),brain)
        # Head tracking loss hides only the brain-relative representation.
        window.view.phantom_pose=None
        controller.update(Frame(3,time.monotonic(),(),(moved,),0),models,True)
        self.assertTrue(controller.tracking_layer.items[0].visible())
        self.assertFalse(controller.mri_layer.items[0].visible())
        controller.update(None,None,False)
        self.assertFalse(controller.tracking_layer.items[0].visible())
        window.close()


    def test_radial_holder_places_field_at_aperture_and_hides_on_loss(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        window=MainWindow(auto_connect=False)
        window.timer.stop()
        window.phantom.use_mri_size=False
        window.mri_workspace.set_volume(demo_volume())
        window.acoustic.rebuild(self.field())
        controller=window.acoustic
        controller.placement.mode='holder'
        controller.placement.beam_shift_mm=-7  # Old manual shift must not alter attachment.
        window.view.phantom_pose=(np.array([.2,.3,.4]),np.eye(3))
        aperture=np.array([.1,.2,.3])
        window.view.holder_result=dict(orientation=np.eye(3),aperture_centre=aperture)
        frame=Frame(1,time.monotonic(),(),(),0)
        controller.update(frame,Models(),True)
        np.testing.assert_allclose(controller.tracker_pose[:3,3],aperture)
        np.testing.assert_allclose(controller.tracker_pose[:3,2],[0,0,-.001])
        np.testing.assert_allclose(tracker_from_mri(window.view)@controller.mri_pose,controller.tracker_pose,atol=1e-12)
        self.assertTrue(controller.tracking_layer.items[0].visible())
        window.view.holder_result=None
        controller.update(frame,Models(),True)
        self.assertFalse(controller.tracking_layer.items[0].visible())
        self.assertFalse(controller.mri_layer.items[0].visible())
        window.close()


    def test_field_sections_follow_the_mri_slice_and_clear_when_hidden(self):
        app = Qt.QApplication.instance() or Qt.QApplication([])
        window = MainWindow(auto_connect=False)
        window.timer.stop()
        window.phantom.use_mri_size = False
        workspace = window.mri_workspace
        workspace.set_volume(demo_volume())
        controller = window.acoustic
        controller.rebuild(self.field())
        placement = controller.placement
        placement.mode = 'manual'
        placement.enabled = True
        placement.visible_thresholds = [-3]
        placement.rotation_deg = [0, 0, 0]
        placement.beam_shift_mm = 0
        placement.manual_initialized = True
        vertices = controller.field.local_points(controller.field.surfaces[-3]['vertices'])
        target = workspace.volume.physical(workspace.indices)
        placement.position_mm = target - vertices.mean(axis=0)
        controller.update()

        for pane in workspace.panes.values():
            self.assertTrue(pane.field_contours[-3].isVisible())
        contour = controller.section_items[-3]
        self.assertTrue(contour.visible())
        np.testing.assert_allclose(contour.pos[:, 2], target[2], atol=1e-5)
        workspace.indices[2] = 0
        workspace.render()
        self.assertFalse(contour.visible())
        self.assertFalse(workspace.panes['axial'].field_contours[-3].isVisible())
        self.assertTrue(workspace.panes['coronal'].field_contours[-3].isVisible())
        placement.enabled = False
        controller.update()
        self.assertFalse(workspace.panes['coronal'].field_contours[-3].isVisible())
        window.close()


if __name__=='__main__':
    unittest.main()
