import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets as Qt

from neuronav.imaging.mri_volume import MRIVolume,demo_volume
from neuronav.ui.mri_3d import oblique_face,oblique_clip_equation,preview_affine,plane_limits,point_on_plane_in_volume,physical_bounds
from neuronav.ui.mri_workspace import MRIWorkspace


class ObliqueTests(unittest.TestCase):
    def volume(self):
        affine=np.diag([1.,1.5,2.,1.])
        affine[:3,3]=[-8,-14,-20]
        i,j,k=np.meshgrid(np.arange(17),np.arange(19),np.arange(21),indexing='ij')
        data=(i-8+2*(j*1.5-14)+3*(k*2-20)).astype(np.float32)
        return MRIVolume(data,affine,'linear RAS test')

    def test_reslice_samples_physical_oblique_plane_with_trilinear_interpolation(self):
        volume=self.volume()
        point=volume.physical([8,9,10])
        normal=np.array([.3,.5,.8]); normal/=np.linalg.norm(normal)
        data,transform=oblique_face(volume,point,normal)
        h,v=np.meshgrid(np.arange(data.shape[0])+.5,np.arange(data.shape[1])+.5,indexing='ij')
        positions=transform[:3,3,None]+transform[:3,:2] @ np.array([h.ravel(),v.ravel()])
        low,high=physical_bounds(volume)
        inside=np.all((positions>low[:,None]+1e-5)&(positions<high[:,None]-1e-5),axis=0)
        self.assertGreater(inside.sum(),50)
        np.testing.assert_allclose(normal @ (positions-point[:,None]),0,atol=1e-10)
        expected=positions[0]+2*positions[1]+3*positions[2]
        np.testing.assert_allclose(data.ravel()[inside],expected[inside],atol=1e-4)

    def test_clip_plane_transforms_from_mri_mm_to_gpu_coordinates(self):
        volume=self.volume()
        point=volume.physical([8,9,10])
        normal=np.array([.3,.5,.8]); normal/=np.linalg.norm(normal)
        shape=(10,12,14)
        transform=preview_affine(volume,shape)
        equation=oblique_clip_equation(volume,shape,point,normal)
        for distance in [-3,0,3]:
            local=np.linalg.solve(transform,np.r_[point+distance*normal,1])
            self.assertAlmostEqual(np.dot(equation,local),-distance,places=8)
        np.testing.assert_allclose(oblique_clip_equation(volume,shape,point,normal,True),-equation)

    def test_scroll_point_remains_on_plane_and_inside_volume_at_limits(self):
        volume=self.volume()
        anchor=volume.physical([8,9,10])
        low,high=physical_bounds(volume)
        for n in [[1,0,0],[.3,.5,.8],[1e-9,0,1]]:
            normal=np.array(n,dtype=float);normal/=np.linalg.norm(normal)
            minimum,maximum=plane_limits(volume,normal)
            for offset in [minimum-10,minimum,minimum*.3+maximum*.7,maximum,maximum+10]:
                point=point_on_plane_in_volume(volume,normal,offset,anchor)
                self.assertTrue(np.all(point>=low-1e-8) and np.all(point<=high+1e-8))
                self.assertAlmostEqual(np.dot(point,normal),float(np.clip(offset,minimum,maximum)),places=7)

    def test_three_planes_follow_selection_and_preserve_camera(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        workspace.set_volume(demo_volume())
        self.assertEqual([workspace.cut_axis.itemText(i) for i in range(workspace.cut_axis.count())],
                         ['Axial','Coronal','Sagittal'])
        view=workspace.volume_view
        view.orbit(32,12)
        camera=(view.opts['azimuth'],view.opts['elevation'],view.opts['distance'])
        for plane,axis in [('axial',2),('coronal',1),('sagittal',0)]:
            workspace.select(plane,[30,40,50])
            self.assertEqual(workspace.active_plane(),plane)
            point=workspace.volume.physical(workspace.indices)
            np.testing.assert_allclose(view.plane_line.pos[:,axis],point[axis],atol=1e-5)
            workspace.scroll_3d(1)
            self.assertEqual(workspace.indices[axis],[30,40,50][axis]+1)
            self.assertEqual(workspace.panes[plane].slider.value(),workspace.indices[axis])
            workspace.slice_changed(plane,20)
            self.assertEqual(workspace.active_plane(),plane)
            self.assertEqual((view.opts['azimuth'],view.opts['elevation'],view.opts['distance']),camera)
        workspace.clear()
        workspace.close()

    def test_pick_anatomical_face_moves_to_nearest_mri_voxel(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        workspace.set_volume(demo_volume())
        expected=[30,40,50]
        workspace.select('axial',expected)
        target=workspace.volume.physical(expected)
        view=workspace.volume_view
        view.setCameraPosition(pos=QtGui.QVector3D(*target),distance=400,elevation=32,azimuth=-22)
        workspace.render()
        workspace.indices=[1,1,50]
        view.pick_cut(QtCore.QPointF(view.width()/2,view.height()/2))
        self.assertEqual(workspace.indices,expected)
        self.assertEqual(workspace.active_plane(),'axial')
        workspace.clear()
        workspace.close()


if __name__=='__main__':
    unittest.main()
