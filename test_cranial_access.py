import unittest
import numpy as np
from cranial_access import (INTRACRANIAL, BONE, SOFT, EXTERIOR, EXCLUDED, FACE,
                            UNKNOWN, screen_profile, screen_target_path, access_field)


class CranialAccessTests(unittest.TestCase):
    def test_corridor_excludes_face_cavities_repeated_bone_and_clipped_scans(self):
        profile=np.array([INTRACRANIAL]*30+[BONE]*6+[SOFT]*4+[EXTERIOR]*50,np.uint8)
        radii=np.arange(len(profile),dtype=float)
        self.assertTrue(screen_profile(profile,radii,30,35)[0])
        for index,value in [(20,EXCLUDED),(33,FACE),(37,EXCLUDED),(40,BONE),(43,UNKNOWN)]:
            modified=profile.copy(); modified[index]=value
            self.assertFalse(screen_profile(modified,radii,30,35)[0],(index,value))
        deep=np.array([INTRACRANIAL]*30+[BONE]*6+[SOFT]*35+[EXTERIOR]*10,np.uint8)
        self.assertFalse(screen_profile(deep,np.arange(len(deep)),30,35)[0])

    def test_changed_target_rechecks_actual_eye_crossing(self):
        field=np.full((101,101,101),INTRACRANIAL,np.uint8)
        field[70:76]=BONE; field[76:80]=SOFT; field[80:]=EXTERIOR
        field[76:80,45:56,45:56]=EXCLUDED
        geometry=dict(access_grid=field,access_affine=np.eye(4))
        self.assertFalse(screen_target_path(geometry,np.array([40.,50.,50.]),np.array([75.5,50.,50.]))[0])
        self.assertTrue(screen_target_path(geometry,np.array([40.,30.,50.]),np.array([75.5,30.,50.]))[0])

    def test_eye_margin_and_inferior_facial_exclusion_leave_posterior_skull(self):
        indices=np.indices((81,81,81)); radius=np.linalg.norm(indices-40,axis=0)
        brain=radius<25
        final=np.zeros(radius.shape,np.uint8); raw=np.zeros(radius.shape,np.uint16)
        final[radius<34]=5; final[(radius>=27)&(radius<30)]=7
        final[brain]=1; raw[brain]=2
        final[36:45,67:73,29:36]=6; raw[36:45,67:73,29:36]=506
        field,_,summary=access_field(final,raw,np.eye(4),brain)
        self.assertEqual(field[40,68,33],EXCLUDED)
        self.assertEqual(field[40,69,40],EXCLUDED)  # eye neighbourhood
        self.assertEqual(field[40,65,18],FACE)
        self.assertEqual(field[40,12,40],BONE)  # posterior entry retained
        self.assertEqual(field[12,40,40],BONE)  # side entry retained
        self.assertTrue(summary['eye_labels_present'])


if __name__=='__main__':
    unittest.main()
