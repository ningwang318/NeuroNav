import unittest
import numpy as np
from holder_tracking import solve_holder,RADII
from reference_plane import ReferencePlane


class HolderTests(unittest.TestCase):
    def test_identification_and_rear_centre_under_marker_reordering(self):
        r=ReferencePlane.load('config/reference_plane.json')
        centre=np.array([.1,.3,.1])
        a,b=np.radians([30,45])
        directions=np.array([[0,0,1],[np.sin(a),0,np.cos(a)],
                             [np.sin(b)*.5,np.sin(b)*np.sqrt(.75),np.cos(b)]])
        points=centre+RADII[:,None]*directions
        normal=np.cross(points[1]-points[0],points[2]-points[0])
        normal/=np.linalg.norm(normal)
        camera=points.mean(0)+normal*np.sign(np.dot(points[0]-centre,normal))
        result=solve_holder(np.vstack([r.markers,points[[2,0,1]]]),r,[('camera',camera,(0,0,0,1))])
        np.testing.assert_allclose(result['centre'],centre,atol=1e-10)
        np.testing.assert_allclose(result['markers'],points)
        np.testing.assert_allclose(result['angles_deg'][:2],[30,45],atol=1e-10)
        np.testing.assert_allclose(np.linalg.det(result['orientation']),1)

    def test_wrong_marker_geometry_is_rejected(self):
        r=ReferencePlane.load('config/reference_plane.json')
        points=np.array([[0,.3,0],[.2,.3,0],[0,.3,.2]])
        with self.assertRaises(ValueError):
            solve_holder(np.vstack([r.markers,points]),r,[('camera',[0,1,0],(0,0,0,1))])


if __name__=='__main__':
    unittest.main()
