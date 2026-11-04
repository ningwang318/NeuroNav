"""Editable lab illustration geometry expressed in the saved reference frame."""
from dataclasses import dataclass,asdict
import numpy as np

from neuronav.core.config import load_settings,save_settings


@dataclass
class TableLayout:
    width_mm: float=450.
    depth_mm: float=600.
    thickness_mm: float=72.
    center_x_mm: float=33.3539
    center_z_mm: float=224.8863
    top_y_mm: float=-18.
    yaw_deg: float=-143.1422
    robot_x_mm: float=55.
    robot_z_mm: float=-215.

    def validate(self):
        if not np.isfinite(list(asdict(self).values())).all():
            raise ValueError('Table dimensions and placement must be finite.')
        if not 100<=self.width_mm<=2000 or not 100<=self.depth_mm<=2000:
            raise ValueError('Table width/depth must be between 100 and 2000 mm.')
        if not 10<=self.thickness_mm<=300:
            raise ValueError('Table thickness must be between 10 and 300 mm.')

    def frame(self,reference):
        yaw=np.radians(self.yaw_deg)
        basis=np.array([[np.cos(yaw),0,np.sin(yaw)],[0,1,0],[-np.sin(yaw),0,np.cos(yaw)]])
        center=np.array([self.center_x_mm,self.top_y_mm,self.center_z_mm])/1000
        return reference.origin+reference.basis @ center,reference.basis @ basis

    @classmethod
    def load(cls,path):
        return load_settings(cls,path,key='geometry')

    def save(self,path):
        save_settings(self,path,key='geometry',schema=1,units='millimetres/degrees',
                      note='Photo-estimated lab illustration, not a measured robot or collision model.')
