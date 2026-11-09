import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import json
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import nibabel as nib
import numpy as np
from PySide6 import QtWidgets as Qt
from mri_volume import load_mri, file_sha256
from skull_analysis import demo_t1
from charm_cache import (cache_folder, store_completed_charm, saved_charm_info,
                         load_saved_geometry, analyze_mri_skull, cache_lock)
from charm_pipeline import run_or_reuse_charm


class CharmCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent=Path(os.environ.get('NEURONAV_TEST_TEMP',str(Path(tempfile.gettempdir())/'neuro_charm_tests'))).resolve()
        cls.parent.mkdir(parents=True,exist_ok=True)
        cls.fixture=tempfile.TemporaryDirectory(dir=cls.parent)
        base=Path(cls.fixture.name)
        volume=demo_t1()
        image=nib.Nifti1Image(volume.data,volume.affine)
        image.header.set_xyzt_units('mm')
        image.set_qform(volume.affine,1); image.set_sform(volume.affine,1)
        cls.source=base/'sphere.nii.gz'
        nib.save(image,cls.source)
        cls.subject=base/'completed'/'m2m_sphere'
        cls.make_subject(cls.subject,cls.source)
        cls.cache=store_completed_charm(cls.source,cls.subject,source_sha256=file_sha256(cls.source))

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    @staticmethod
    def make_subject(subject, source):
        subject.mkdir(parents=True)
        (subject/'segmentation').mkdir(); (subject/'label_prep').mkdir()
        shutil.copy2(source,subject/'T1.nii.gz')
        image=nib.load(source); data=np.asarray(image.dataobj)
        final=np.zeros(data.shape,np.uint8)
        final[data==120]=1; final[data==5]=7; final[data==100]=5
        raw=np.zeros(data.shape,np.uint16)
        raw[data==120]=2; raw[data==5]=515; raw[data==100]=511
        for values,path in [(raw,subject/'segmentation'/'labeling.nii.gz'),
                            (final,subject/'label_prep'/'tissue_labeling_upsampled.nii.gz')]:
            labels=nib.Nifti1Image(values,image.affine)
            labels.header.set_xyzt_units('mm')
            nib.save(labels,path)
        (subject/'settings.ini').write_text('[general]\nthreads = 8\n')
        (subject/'charm_report.html').write_text('<html>Synthetic test report</html>')
        (subject/'extra_result.txt').write_text('All outputs must be preserved, not only selected masks.')

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir=self.parent)
        self.directory=Path(self.temporary.name)
        self.path=self.directory/'mri.nii.gz'
        shutil.copy2(self.source,self.path)
        self.root=cache_folder(self.path)
        shutil.copytree(self.cache,self.root)
        self.volume=load_mri(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_complete_portable_bundle_contains_all_original_files(self):
        root,manifest=saved_charm_info(self.volume)
        self.assertEqual(root,self.root)
        subject=root/manifest['subject_directory']
        self.assertEqual((subject/'extra_result.txt').read_text(),(self.subject/'extra_result.txt').read_text())
        for item in manifest['copied_charm_files']:
            self.assertEqual(file_sha256(root/item['path']),item['sha256'])
        geometry,_,_=load_saved_geometry(self.volume)
        self.assertGreater(len(geometry['outer']),1800)
        self.assertTrue(np.any(geometry['outer'][:,2] < geometry['center'][2]-20))
        self.assertTrue(np.isfinite(geometry['normal_mm']).any())
        self.assertEqual(self.volume.source_sha256,file_sha256(self.path))

    def test_reuse_and_retarget_launch_no_segmentation_or_extraction(self):
        before=(self.root/'manifest.json').read_bytes()
        with (patch('charm_cache.build_geometry',side_effect=AssertionError('No geometry rebuild')),
             patch('charm_cache.analyze_t1',side_effect=AssertionError('No band extraction')),
             patch('charm_pipeline.subprocess.Popen',side_effect=AssertionError('No CHARM process'))):
            self.assertEqual(run_or_reuse_charm(self.path,install_root=self.directory/'not_installed'),self.root)
            first=analyze_mri_skull(self.volume,[0,0,0])
            second=analyze_mri_skull(self.volume,[35,0,0])
        np.testing.assert_array_equal(first.outer,second.outer)
        self.assertGreater(np.nanmedian(second.outer_angle),np.nanmedian(first.outer_angle)+10)
        self.assertIn('Saved CHARM',first.method)
        self.assertEqual(first.segmentation_path,str(self.root))
        self.assertEqual(before,(self.root/'manifest.json').read_bytes())

    def test_changed_mri_is_rejected_without_automatic_rerun(self):
        image=nib.load(self.path); data=image.get_fdata(dtype=np.float32); data[60,60,60]+=1
        changed=nib.Nifti1Image(data,image.affine); changed.header.set_xyzt_units('mm'); nib.save(changed,self.path)
        volume=load_mri(self.path)
        with self.assertRaisesRegex(ValueError,'different MRI'):
            analyze_mri_skull(volume,[0,0,0])
        with patch('charm_pipeline.subprocess.Popen') as process:
            with self.assertRaisesRegex(ValueError,'different MRI'):
                run_or_reuse_charm(self.path)
            process.assert_not_called()

    def test_corrupt_geometry_is_not_reused(self):
        _,manifest=saved_charm_info(self.volume)
        path=self.root/manifest['geometry']
        with path.open('ab') as stream: stream.write(b'corruption')
        with self.assertRaisesRegex(ValueError,'integrity'):
            analyze_mri_skull(self.volume,[0,0,0])

    def test_upper_cap_cache_upgrades_from_saved_labels_without_charm(self):
        manifest_path=self.root/'manifest.json'
        manifest=json.loads(manifest_path.read_text())
        old=self.root/manifest['geometry']
        with np.load(old) as archive:
            legacy={k:archive[k] for k in archive.files if not k.startswith('access_')}
        np.savez_compressed(old,**legacy)
        manifest.update(geometry_version=1,geometry_sha256=file_sha256(old))
        manifest_path.write_text(json.dumps(manifest))
        with patch('charm_pipeline.subprocess.Popen',side_effect=AssertionError('No segmentation')):
            geometry,_,updated=load_saved_geometry(self.volume)
        self.assertEqual(updated['geometry_version'],2)
        self.assertNotEqual(updated['geometry'],manifest['geometry'])
        self.assertEqual(file_sha256(old),manifest['geometry_sha256'])
        self.assertEqual(updated['copied_charm_files'],manifest['copied_charm_files'])
        self.assertIn('access_grid',geometry)
        with patch('charm_cache.build_geometry',side_effect=AssertionError('Upgrade only once')):
            load_saved_geometry(self.volume)

    def test_incomplete_or_wrong_grid_or_escaping_cache_is_rejected(self):
        manifest_path=self.root/'manifest.json'
        manifest=json.loads(manifest_path.read_text())
        self.volume.affine=self.volume.affine.copy(); self.volume.affine[0,3]+=2
        with self.assertRaisesRegex(ValueError,'grid'):
            saved_charm_info(self.volume)
        self.volume=load_mri(self.path)
        manifest['geometry']='../outside.npz'
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'escapes'):
            saved_charm_info(self.volume)
        manifest_path.unlink()
        with self.assertRaisesRegex(ValueError,'incomplete'):
            analyze_mri_skull(self.volume,[0,0,0])

    def test_existing_result_is_not_rebuilt_and_writers_do_not_collide(self):
        with patch('charm_cache.build_geometry',side_effect=AssertionError('Do not rebuild')):
            self.assertEqual(store_completed_charm(self.path,self.subject),self.root)
        before=(self.root/'manifest.json').read_bytes()
        with cache_lock(self.root):
            with self.assertRaisesRegex(ValueError,'already owns'):
                store_completed_charm(self.path,self.subject,replace=True)
            self.assertTrue((self.root/'write.lock').is_file())
        self.assertEqual(before,(self.root/'manifest.json').read_bytes())

    def test_future_run_saves_beside_mri_and_keeps_previous_result(self):
        install=self.directory/'install'/'simnibs_env'
        install.mkdir(parents=True); (install/'python.exe').touch()
        old_manifest=json.loads((self.root/'manifest.json').read_text())
        t2=self.directory/'t2.nii.gz'
        shutil.copy2(self.path,t2)
        def fake_job(command,**kwargs):
            work=Path(kwargs['cwd'])
            self.assertTrue(work.is_relative_to(self.root))
            self.assertIn('--skipregisterT2',command)
            self.assertIn('--noneck',command)
            self.assertTrue((work/'T2_RAS_mm.nii.gz').is_file())
            self.make_subject(work/'m2m_mri',Path(command[5]))
            return Mock(wait=Mock(return_value=0))
        with patch('charm_pipeline.subprocess.check_output',return_value='4.6.0\n'), \
             patch('charm_pipeline.subprocess.Popen',side_effect=fake_job) as process:
            result=run_or_reuse_charm(self.path,install_root=install.parent,recalculate=True,t2_path=t2,no_neck=True)
            process.assert_called_once()
        self.assertEqual(result,self.root)
        manifest=json.loads((self.root/'manifest.json').read_text())
        self.assertIn('T1 + aligned T2',manifest['method'])
        self.assertNotEqual(manifest['geometry'],old_manifest['geometry'])
        self.assertTrue((self.root/old_manifest['geometry']).is_file())
        self.assertTrue((self.root/manifest['subject_directory']/'extra_result.txt').exists())

    def test_gui_uses_saved_surfaces_and_disables_band_thresholds(self):
        from mri_workspace import MRIWorkspace
        app=Qt.QApplication.instance() or Qt.QApplication([])
        workspace=MRIWorkspace(Qt.QWidget())
        workspace.set_volume(self.volume); workspace.open_skull_analysis()
        dialog=workspace.skull_dialog
        self.assertIn('Saved CHARM',dialog.cache_label.text())
        self.assertFalse(dialog.dark.isEnabled())
        with patch('charm_cache.analyze_t1',side_effect=AssertionError('Must reuse segmentation')):
            dialog.calculate.click()
            deadline=time.monotonic()+10
            while workspace.jobs and time.monotonic()<deadline:
                app.processEvents(); time.sleep(.005)
        self.assertFalse(workspace.jobs)
        self.assertIsNotNone(dialog.result)
        self.assertIn('Saved CHARM',dialog.result.method)
        # The centred synthetic head is deeper than this device's 63.2 mm focus.
        self.assertEqual(dialog.table.rowCount(),0)
        self.assertFalse(dialog.result.transducer_access.any())
        self.assertFalse(dialog.dark.isEnabled())
        dialog.close(); workspace.close()


if __name__=='__main__':
    unittest.main()
