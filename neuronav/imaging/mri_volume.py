"""Local MRI loading with explicit RAS+ millimetre display geometry."""
from dataclasses import dataclass
from pathlib import Path
import nibabel as nib
from nibabel.processing import resample_from_to
from nibabel.spaces import vox2out_vox
import numpy as np
import SimpleITK as sitk

MAX_VOXELS = 128_000_000


@dataclass
class MRIVolume:
    data: np.ndarray  # X right, Y anterior, Z superior; axis-aligned display grid
    affine: np.ndarray  # display voxel centre -> RAS+ mm
    name: str
    note: str = ''
    synthetic: bool = False

    @property
    def spacing(self):
        return np.diag(self.affine)[:3]

    def physical(self, indices):
        return (self.affine @ np.r_[indices,1])[:3]

    def levels(self):
        stride=max(1,int(np.ceil((self.data.size/500000)**(1/3))))
        sample=self.data[::stride,::stride,::stride].reshape(-1)
        sample=sample[np.isfinite(sample)&(sample!=0)]
        if not len(sample):
            return 0.,1.
        low,high=np.percentile(sample,[.5,99.5])
        if high <= low:
            low,high=float(low)-.5,float(high)+.5
        return float(low),float(high)

    def slice(self, plane, indices):
        x,y,z=map(int,indices)
        if plane=='axial':
            return self.data[:,:,z].T[::-1,::-1], (self.spacing[0],self.spacing[1])
        if plane=='coronal':
            return self.data[:,y,:].T[::-1,::-1], (self.spacing[0],self.spacing[2])
        return self.data[x,:,:].T[::-1,::-1], (self.spacing[1],self.spacing[2])


def prepare_volume(data, affine, name, note=''):
    if data.ndim==4 and data.shape[3]==1:
        data=data[:,:,:,0]
    if data.ndim!=3 or min(data.shape)<2:
        raise ValueError('Load a 3D anatomical MRI volume. Multi-volume 4D scans and single 2D images are not supported yet.')
    if data.size>MAX_VOXELS:
        raise ValueError('This volume is too large for the desktop viewer (maximum 128 million voxels).')
    affine=np.asarray(affine,dtype=float)
    if affine.shape!=(4,4) or not np.isfinite(affine).all() or abs(np.linalg.det(affine[:3,:3]))<1e-10:
        raise ValueError('The MRI spatial transform is invalid.')
    image=nib.as_closest_canonical(nib.Nifti1Image(np.asarray(data,dtype=np.float32),affine))
    linear=image.affine[:3,:3]
    spacing=nib.affines.voxel_sizes(image.affine)
    if not np.isfinite(spacing).all() or min(spacing)<=0:
        raise ValueError('The MRI voxel spacing is invalid.')
    # Axis permutation alone does not remove obliquity/shear. Resample those
    # cases into true anatomical planes, with linear intensity interpolation.
    if not np.allclose(linear,np.diag(np.diag(linear)),atol=1e-5):
        shape,output_affine=vox2out_vox((image.shape,image.affine),voxel_sizes=spacing)
        if np.prod(shape)>MAX_VOXELS:
            raise ValueError('Anatomical reformatting would exceed the viewer memory limit.')
        image=resample_from_to(image,(shape,output_affine),order=1,mode='constant',cval=0)
        note=(note+' · ' if note else '')+'Oblique scan reformatted to anatomical planes (linear interpolation).'
    result=np.asarray(image.dataobj,dtype=np.float32)
    result=np.nan_to_num(result,nan=0.,posinf=0.,neginf=0.,copy=True)
    return MRIVolume(result,np.array(image.affine),name,note)


def load_nifti(path):
    image=nib.load(str(path))
    if len(image.shape)>4 or np.prod(image.shape)>MAX_VOXELS:
        raise ValueError('The MRI is too large or has unsupported dimensions.')
    if not (int(image.header['qform_code']) or int(image.header['sform_code'])):
        raise ValueError('The NIfTI file has no coded spatial transform (qform/sform); its anatomical orientation cannot be verified.')
    units=image.header.get_xyzt_units()[0]
    scale={'mm':1.,'meter':1000.,'micron':.001}.get(units)
    note=''
    if scale is None:
        scale=1.
        note='Spatial units unspecified: assuming mm. Confirm before registration.'
    affine=np.array(image.affine)
    affine[:3,:]*=scale
    return prepare_volume(image.get_fdata(dtype=np.float32),affine,Path(path).name,note)


def dicom_series(folder):
    identifiers=sitk.ImageSeriesReader.GetGDCMSeriesIDs(str(folder)) or []
    result=[]
    for uid in identifiers:
        files=list(sitk.ImageSeriesReader.GetGDCMSeriesFileNames(str(folder),uid))
        if len(files)<2:
            continue
        reader=sitk.ImageFileReader()
        reader.SetFileName(files[0])
        reader.ReadImageInformation()
        description=reader.GetMetaData('0008|103e').strip() if reader.HasMetaDataKey('0008|103e') else 'MRI series'
        result.append(dict(uid=uid,files=files,label=f'{description} · {len(files)} images · {uid[-12:]}'))
    if not result:
        raise ValueError('No 3D DICOM series found. Select the folder containing the individual DICOM slice files.')
    return result


def load_dicom_series(files, name):
    # Check geometry before allocating the pixel volume. A missing slice,
    # mixed echo/time series, or localizer must not silently become a uniform
    # anatomical stack with incorrect distances.
    positions=[]
    first_orientation=None
    first_spacing=None
    first_size=None
    for path in files:
        info=sitk.ImageFileReader()
        info.SetFileName(path)
        info.ReadImageInformation()
        size=info.GetSize()
        if len(size)>2 and size[2]!=1:
            raise ValueError('Multi-frame DICOM is not supported yet; convert this series to NIfTI.')
        if not all(info.HasMetaDataKey(tag) for tag in ['0020|0032','0020|0037','0028|0030']):
            raise ValueError('DICOM patient position/orientation/spacing metadata is missing.')
        orientation=np.array([float(v) for v in info.GetMetaData('0020|0037').split('\\')])
        spacing=np.array([float(v) for v in info.GetMetaData('0028|0030').split('\\')])
        position=np.array([float(v) for v in info.GetMetaData('0020|0032').split('\\')])
        if first_orientation is None:
            first_orientation,first_spacing,first_size=orientation,spacing,size[:2]
        if size[:2]!=first_size or not np.allclose(orientation,first_orientation,atol=1e-4) or not np.allclose(spacing,first_spacing,atol=1e-4):
            raise ValueError('The DICOM series contains inconsistent slice geometry. Select a single 3D acquisition.')
        positions.append(position)
    if len(positions)<2 or np.prod(first_size)*len(positions)>MAX_VOXELS:
        raise ValueError('DICOM series is empty, single-slice, or exceeds the viewer memory limit.')
    normal=np.cross(first_orientation[:3],first_orientation[3:])
    normal/=np.linalg.norm(normal)
    positions=np.asarray(positions)
    gaps=np.diff(positions @ normal)
    step=np.median(gaps)
    if abs(step)<.01 or not (np.all(gaps>0) or np.all(gaps<0)) or np.max(np.abs(gaps-step))>max(.05,abs(step)*.02):
        raise ValueError('DICOM slices are duplicated, missing or irregularly spaced; a regular 3D volume is required.')
    expected=positions[0]+np.arange(len(positions))[:,None]*np.median(np.diff(positions,axis=0),axis=0)
    if np.max(np.linalg.norm(positions-expected,axis=1))>.1:
        raise ValueError('DICOM slice positions do not form a regular spatial grid.')
    reader=sitk.ImageSeriesReader()
    reader.SetFileNames(files)  # GDCM acquisition order, never filename sorting
    reader.ForceOrthogonalDirectionOff()
    image=reader.Execute()
    if image.GetDimension()!=3 or image.GetNumberOfComponentsPerPixel()!=1:
        raise ValueError('This DICOM series is not a scalar 3D volume.')
    if np.prod(image.GetSize())>MAX_VOXELS:
        raise ValueError('This DICOM volume exceeds the desktop viewer memory limit.')
    data=sitk.GetArrayFromImage(image).transpose(2,1,0)
    affine_lps=np.eye(4)
    affine_lps[:3,:3]=np.array(image.GetDirection()).reshape(3,3) @ np.diag(image.GetSpacing())
    affine_lps[:3,3]=image.GetOrigin()
    affine_ras=np.diag([-1,-1,1,1]) @ affine_lps
    return prepare_volume(data,affine_ras,name,'DICOM series · patient LPS converted to RAS+ mm.')


def demo_volume():
    """Asymmetric synthetic phantom for UI QA, never presented as patient MRI."""
    n=96
    x,y,z=np.meshgrid(*[np.linspace(-1,1,n)]*3,indexing='ij')
    r=(x/.72)**2+(y/.86)**2+(z/.92)**2
    data=np.zeros((n,n,n),dtype=np.float32)
    data[r<1]=90
    data[r<.87]=45
    inner=r<.70
    data[inner]=140+15*np.cos(x[inner]*35)*np.sin(z[inner]*31)
    ventricles=(((x-.11)/.10)**2+(y/.18)**2+(z/.32)**2<1)|(((x+.11)/.10)**2+(y/.18)**2+(z/.32)**2<1)
    data[ventricles]=20
    data[((x-.30)/.12)**2+((y-.20)/.12)**2+((z+.10)/.12)**2<1]=210
    affine=np.diag([2.,2.,2.,1.])
    affine[:3,3]=-95
    return MRIVolume(data,affine,'SYNTHETIC DEMO — not patient MRI','Synthetic phantom for interface demonstration.',True)
