import tempfile
from pathlib import Path
import unittest
import numpy as np
from scipy.spatial.transform import Rotation
from neuronav.core.table_layout import TableLayout
from neuronav.tracking.reference_plane import ReferencePlane
from neuronav.ui.lab_setup import lab_setup_meshes


class TableLayoutTests(unittest.TestCase):
    def reference(self):
        points=np.array([[-.136883873,0,.040057913],[.113465383,0,.040057913],[.023418490,0,-.080115825]])
        basis=Rotation.from_euler('xyz',[8,21,-12],degrees=True).as_matrix()
        origin=np.array([.3,-.2,.7])
        return ReferencePlane(origin,basis,points @ basis.T+origin,{})

    def test_reference_markers_anchor_photo_table_edges_and_metric_scale(self):
        table=TableLayout()
        reference=self.reference()
        origin,basis=table.frame(reference)
        markers=(reference.markers-origin) @ basis
        # Photo CS-200: right/front P1, left/rear P2, left/front P3.
        np.testing.assert_allclose(markers[:,[0,2]]*1000,[[25,250],[-175,100],[-175,250]],atol=1)
        np.testing.assert_allclose(markers[:,1]*1000,18,atol=1e-6)
        self.assertTrue(np.all(abs(markers[:,0])<table.width_mm/2000))
        self.assertTrue(np.all(abs(markers[:,2])<table.depth_mm/2000))
        np.testing.assert_allclose(basis.T @ basis,np.eye(3),atol=1e-10)
        self.assertAlmostEqual(np.linalg.det(basis),1)

    def test_rendered_top_matches_dimensions_and_table_bottom_meets_ground(self):
        reference=self.reference()
        table=TableLayout()
        origin,basis=table.frame(reference)
        meshes,_,_=lab_setup_meshes([],reference,table)
        top=next(mesh for mesh in meshes if mesh['color']==(.63,.65,.67,1))
        local=(top['vertices']-origin) @ basis
        np.testing.assert_allclose(np.ptp(local,axis=0),[.450,.008,.600],atol=1e-9)
        self.assertAlmostEqual(local[:,1].max(),0)
        bottom=next(mesh for mesh in meshes if mesh['color']==(.40,.44,.48,1))
        ground=next(mesh for mesh in meshes if mesh['color']==(.15,.18,.22,1))
        bottom_y=((bottom['vertices']-origin) @ basis)[:,1].min()
        ground_y=((ground['vertices']-origin) @ basis)[:,1].max()
        self.assertAlmostEqual(bottom_y,ground_y)

    def test_saved_dimensions_and_offsets_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'table.json'
            original=TableLayout(width_mm=500,center_x_mm=41,robot_z_mm=-195)
            original.save(path)
            self.assertEqual(TableLayout.load(path),original)
            with self.assertRaises(ValueError):
                TableLayout(width_mm=0).save(path)


if __name__=='__main__':
    unittest.main()
