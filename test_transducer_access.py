import unittest
from types import SimpleNamespace
import numpy as np
from transducer_access import (TransducerGeometry,aperture_samples,
                               aperture_sources_clear,filter_transducer_access)


class TransducerAccessTests(unittest.TestCase):
    @staticmethod
    def geometry(head_z):
        field=np.zeros((161,161,161),np.uint8)
        field[:,:,:int(head_z+80)+1]=1
        affine=np.eye(4); affine[:3,3]=-80
        return dict(access_grid=field,access_affine=affine)

    @staticmethod
    def result(depths):
        return SimpleNamespace(target=np.zeros(3),outer=np.array([[0.,0.,z] for z in depths]),
                               path_mm=np.ones(len(depths)))

    def test_shallow_target_allows_standoff_but_depth_beyond_focus_is_rejected(self):
        result=self.result([40,70])
        filter_transducer_access(result,self.geometry(45),TransducerGeometry())
        np.testing.assert_array_equal(result.transducer_access,[True,False])
        np.testing.assert_allclose(result.transducer_standoff_mm,[23.2,-6.8])

    def test_finite_aperture_rim_collision_is_checked(self):
        geometry=self.geometry(58)
        wide=filter_transducer_access(self.result([53]),geometry,TransducerGeometry(64,63.2))
        narrow=filter_transducer_access(self.result([53]),geometry,TransducerGeometry(20,63.2))
        self.assertFalse(wide.transducer_access[0])
        self.assertTrue(narrow.transducer_access[0])
        samples=aperture_samples(TransducerGeometry())
        np.testing.assert_allclose(np.linalg.norm(samples,axis=1),63.2)
        self.assertAlmostEqual(np.linalg.norm(samples[-1,:2]),32.)

    def test_outside_scan_requires_confirmed_air_and_cancellation(self):
        profiles=np.array([[3,2,1,0,0,0,0,0,255], [3,2,1,0,255,255,255,255,255],
                           [3,2,1,1,1,1,1,1,1]],np.uint8)
        np.testing.assert_array_equal(aperture_sources_clear(profiles,1.),[True,False,False])
        with self.assertRaises(InterruptedError):
            filter_transducer_access(self.result([40]),self.geometry(45),cancel=lambda:True)
        with self.assertRaises(ValueError):
            aperture_samples(TransducerGeometry(64,20))


if __name__=='__main__':
    unittest.main()
