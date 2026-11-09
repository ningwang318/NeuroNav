import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
import uuid
import time
import gzip
from unittest.mock import patch,Mock

import nibabel as nib
import numpy as np
import SimpleITK as sitk
from PySide6 import QtWidgets as Qt

from mri_volume import prepare_volume,load_mri,load_nifti,dicom_series,load_dicom_series,demo_volume
from mri_workspace import MRIWorkspace


class MRITests(unittest.TestCase):
    def test_mgh_and_mgz_preserve_scanner_ras_geometry_and_voxel_values(self):
        data=np.arange(5*6*7,dtype=np.float32).reshape(5,6,7)
        affine=np.array([[-1.2,0,0,30],[0,0,-2.5,50],[0,1.7,0,-40],[0,0,0,1]])
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ['.mgh','.mgz']:
                path=Path(directory)/('anatomical'+suffix)
                nib.save(nib.freesurfer.mghformat.MGHImage(data,affine),path)
                expected=nib.as_closest_canonical(nib.load(path,mmap=False,keep_file_open=False))
                actual=load_mri(path)
                np.testing.assert_array_equal(actual.data,expected.get_fdata(dtype=np.float32))
                np.testing.assert_allclose(actual.affine,expected.affine,atol=1e-5)
                np.testing.assert_allclose(actual.spacing,[1.2,2.5,1.7],atol=1e-6)
                self.assertIn('scanner RAS',actual.note)

    def test_mgz_without_spatial_geometry_is_not_silently_given_default_axes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'missing-geometry.mgz'
            nib.save(nib.freesurfer.mghformat.MGHImage(np.ones((4,5,6),np.float32),np.eye(4)),path)
            with gzip.open(path,'rb') as source:
                raw=bytearray(source.read())
            raw[28:30]=b'\x00\x00'
            with gzip.open(path,'wb') as output:
                output.write(raw)
            with self.assertRaisesRegex(ValueError,'scanner RAS'):
                load_mri(path)

    def test_file_picker_offers_freesurfer_files_and_dispatches_them(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        workspace.load_path=Mock()
        with patch.object(Qt.QFileDialog,'getOpenFileName',return_value=('chosen.mgz','')) as picker:
            workspace.open_nifti()
        self.assertIn('*.mgz',picker.call_args.args[3])
        self.assertIn('*.mgh',picker.call_args.args[3])
        workspace.load_path.assert_called_once_with('chosen.mgz')
        workspace.close()

    def test_background_nifti_load_completes_without_blocking_gui(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        errors=[]
        workspace.failed=errors.append
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'background-test.nii.gz'
            image=nib.Nifti1Image(np.ones((8,9,10),np.float32),np.diag([1,2,3,1]))
            image.header.set_xyzt_units('mm')
            nib.save(image,path)
            workspace.load_path(str(path))
            deadline=time.monotonic()+5
            while workspace.jobs and time.monotonic()<deadline:
                app.processEvents()
                time.sleep(.005)
            self.assertFalse(errors)
            self.assertIsNotNone(workspace.volume)
            self.assertEqual(workspace.volume.data.shape,(8,9,10))
            self.assertFalse(workspace.jobs)
        workspace.close()

    def test_negative_axes_preserve_physical_geometry(self):
        data=np.arange(5*6*7,dtype=np.float32).reshape(5,6,7)
        affine=np.diag([-2.,3.,-4.,1.])
        affine[:3,3]=[40,20,80]
        volume=prepare_volume(data,affine,'test')
        np.testing.assert_array_equal(volume.data,data[::-1,:,::-1])
        np.testing.assert_allclose(volume.physical([0,0,0]),[32,20,56])
        np.testing.assert_allclose(volume.spacing,[2,3,4])

    def test_radiological_slice_orientation(self):
        x,y,z=np.meshgrid(np.arange(5),np.arange(6),np.arange(7),indexing='ij')
        data=(x+10*y+100*z).astype(np.float32)
        volume=prepare_volume(data,np.diag([1,2,3,1]),'orientation')
        axial,spacing=volume.slice('axial',[2,3,4])
        self.assertEqual(axial[0,0],454)  # right/anterior
        self.assertEqual(axial[-1,-1],400)  # left/posterior
        self.assertEqual(tuple(spacing),(1,2))
        coronal,_=volume.slice('coronal',[2,3,4])
        self.assertEqual(coronal[0,0],634)  # right/superior
        sagittal,_=volume.slice('sagittal',[2,3,4])
        self.assertEqual(sagittal[0,0],652)  # anterior/superior

    def test_oblique_volume_is_resampled_to_anatomical_axes(self):
        angle=.2
        affine=np.eye(4)
        affine[:3,:3]=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]]) @ np.diag([1,2,3])
        volume=prepare_volume(np.ones((5,6,7),np.float32),affine,'oblique')
        np.testing.assert_allclose(volume.affine[:3,:3],np.diag(volume.spacing))
        self.assertIn('reformatted',volume.note)

    def test_nifti_file_units_converted_to_mm(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.nii.gz'
            image=nib.Nifti1Image(np.ones((5,6,7),np.float32),np.diag([.001,.002,.003,1]))
            image.header.set_xyzt_units('meter')
            nib.save(image,path)
            volume=load_nifti(path)
            np.testing.assert_allclose(volume.spacing,[1,2,3],atol=1e-6)

    def test_unsupported_4d_and_invalid_affine_rejected(self):
        with self.assertRaises(ValueError):
            prepare_volume(np.zeros((4,5,6,2)),np.eye(4),'4d')
        with self.assertRaises(ValueError):
            prepare_volume(np.zeros((4,5,6)),np.zeros((4,4)),'bad')

    def make_dicom(self,folder,positions=(0,2.5,5,7.5)):
        uid='2.25.'+str(uuid.uuid4().int)
        raw=np.arange(len(positions)*7*9,dtype=np.uint16).reshape(len(positions),7,9)
        for i,z in enumerate(positions):
            image=sitk.GetImageFromArray(raw[i])
            image.SetSpacing((1.2,1.7))
            for tag,value in {'0008|0060':'MR','0008|103e':'SYNTHETIC TEST',
                '0020|000d':uid,'0020|000e':uid+'.1','0020|0013':str(i+1),
                '0020|0032':f'12\\20\\{z}', '0020|0037':'1\\0\\0\\0\\1\\0',
                '0018|0050':'2.5'}.items():
                image.SetMetaData(tag,value)
            writer=sitk.ImageFileWriter()
            writer.KeepOriginalImageUIDOn()
            writer.SetFileName(str(Path(folder)/f'slice_{100-i}.dcm'))
            writer.Execute(image)
        return raw

    def test_dicom_acquisition_order_lps_to_ras_and_spacing(self):
        with tempfile.TemporaryDirectory() as directory:
            raw=self.make_dicom(directory)
            series=dicom_series(directory)
            self.assertEqual(len(series),1)
            volume=load_dicom_series(series[0]['files'],'synthetic')
            expected=raw.transpose(2,1,0)[::-1,::-1,:]
            np.testing.assert_array_equal(volume.data,expected)
            np.testing.assert_allclose(volume.spacing,[1.2,1.7,2.5],atol=1e-6)
            np.testing.assert_allclose(volume.physical([0,0,0]),[-21.6,-30.2,0],atol=1e-5)

    def test_irregular_dicom_spacing_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_dicom(directory,(0,2.5,7.5,10))
            series=dicom_series(directory)
            with self.assertRaisesRegex(ValueError,'irregularly spaced'):
                load_dicom_series(series[0]['files'],'irregular')

    def test_linked_gui_crosshairs_and_unload(self):
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        volume=demo_volume()
        workspace.set_volume(volume)
        workspace.select('axial',[5,7,9])
        self.assertEqual(workspace.panes['axial'].slider.value(),9)
        self.assertEqual(workspace.panes['coronal'].slider.value(),7)
        self.assertEqual(workspace.panes['sagittal'].slider.value(),5)
        expected,_=volume.slice('axial',[5,7,9])
        np.testing.assert_array_equal(workspace.panes['axial'].image.image,expected)
        self.assertAlmostEqual(workspace.panes['axial'].vertical.value(),(96-1-5+.5)*2)
        workspace.panes['axial'].slider.setValue(11)
        self.assertEqual(workspace.indices,[5,7,11])
        workspace.clear()
        self.assertIsNone(workspace.volume)
        self.assertFalse(workspace.width.isEnabled())
        workspace.close()


if __name__=='__main__':
    unittest.main()
