"""Portable per-MRI CHARM results and target-independent planning geometry."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import shutil
import uuid
from collections import Counter
import numpy as np
from scipy import ndimage
from mri_volume import load_mri, file_sha256
from skull_analysis import (SkullOptions, fibonacci, make_surface_geometry,
                            analyze_geometry, analyze_t1, _check)
from cranial_access import AccessPolicy, access_field, sample_field, bone_runs, screen_profile

SCHEMA_VERSION = 1
GEOMETRY_VERSION = 2


def cache_folder(source_path):
    path = Path(source_path).resolve()
    stem = path.name[:-7] if path.name.lower().endswith('.nii.gz') else path.stem
    return path.parent/(stem+'_charm')


def _inside(root, relative):
    path = (root/relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Saved CHARM path escapes its results folder.')
    return path


@contextmanager
def cache_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    lock = root/'write.lock'
    try:
        stream = lock.open('x', encoding='utf-8')
    except FileExistsError:
        raise ValueError('A CHARM job or results copy already owns '+str(lock)) from None
    try:
        with stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def saved_charm_info(volume):
    """None means no saved result; incomplete/stale data raises, never reruns."""
    if not volume.source_path:
        return None
    root = cache_folder(volume.source_path)
    if not root.exists():
        return None
    manifest_path = root/'manifest.json'
    if not manifest_path.is_file():
        raise ValueError('Saved CHARM folder is incomplete: '+str(root))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict):
        raise ValueError('Saved CHARM manifest must be an object.')
    required = {'geometry','geometry_sha256','simnibs_version','display_affine_ras_mm','display_shape','source_sha256'}
    if not required.issubset(manifest):
        raise ValueError('Saved CHARM manifest is missing required fields.')
    if manifest.get('schema_version') != SCHEMA_VERSION or not manifest.get('complete'):
        raise ValueError('Saved CHARM result is incomplete or has an unsupported format.')
    if not volume.source_sha256 or manifest.get('source_sha256') != volume.source_sha256:
        raise ValueError('Saved CHARM result belongs to a different MRI file. Recalculate CHARM explicitly for this scan.')
    affine = np.asarray(manifest.get('display_affine_ras_mm'), dtype=float)
    if list(volume.data.shape) != manifest.get('display_shape') or affine.shape != (4,4) or not np.isfinite(affine).all() or not np.allclose(volume.affine, affine, atol=1e-5):
        raise ValueError('Saved CHARM geometry does not match the loaded MRI grid.')
    if manifest.get('geometry_version') not in (1, GEOMETRY_VERSION):
        raise ValueError('Saved planning geometry needs updating from the stored segmentation; CHARM need not be rerun.')
    geometry_path = _inside(root, manifest['geometry'])
    if not geometry_path.is_file():
        raise ValueError('Saved CHARM planning geometry is missing: '+str(geometry_path))
    return root, manifest


def upgrade_saved_geometry(volume, cancel=None):
    """Upgrade planning coverage using stored labels, preserving old geometry."""
    root, manifest = saved_charm_info(volume)
    if manifest['geometry_version'] == GEOMETRY_VERSION:
        return
    with cache_lock(root):
        root, manifest = saved_charm_info(volume)
        if manifest['geometry_version'] == GEOMETRY_VERSION:
            return
        old = _inside(root, manifest['geometry'])
        if file_sha256(old) != manifest['geometry_sha256']:
            raise ValueError('Saved CHARM planning geometry failed its integrity check.')
        geometry = build_geometry(volume, _inside(root, manifest['subject_directory']),
                                  _inside(root, manifest['exports_directory']), cancel)
        _check(cancel)
        path = old.parent/('planning_geometry_v2_'+uuid.uuid4().hex[:8]+'.npz')
        np.savez_compressed(path, **geometry)
        manifest.setdefault('previous_geometry', []).append(dict(
            geometry=manifest['geometry'], sha256=manifest['geometry_sha256'], version=manifest['geometry_version']))
        manifest.update(geometry_version=GEOMETRY_VERSION, geometry=path.relative_to(root).as_posix(),
                        geometry_sha256=file_sha256(path), geometry_points=len(geometry['outer']),
                        coverage=json.loads(str(geometry['access_policy_json'])),
                        geometry_updated_utc=datetime.now(timezone.utc).isoformat())
        temporary = root/('manifest_'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        os.replace(temporary, root/'manifest.json')


def load_saved_geometry(volume, cancel=None):
    info = saved_charm_info(volume)
    if info is None:
        raise ValueError('No saved CHARM result beside this MRI.')
    root, manifest = info
    if manifest['geometry_version'] < GEOMETRY_VERSION:
        upgrade_saved_geometry(volume, cancel)
        root, manifest = saved_charm_info(volume)
    path = _inside(root, manifest['geometry'])
    if file_sha256(path) != manifest['geometry_sha256']:
        raise ValueError('Saved CHARM planning geometry failed its integrity check.')
    keys = ['center', 'outer', 'inner', 'faces', 'normals', 'errors', 'inner_faces', 'normal_mm',
            'access_grid', 'access_affine', 'access_policy_json']
    with np.load(path, allow_pickle=False) as archive:
        geometry = {key: archive[key].copy() for key in keys}
    if (geometry['access_grid'].shape != volume.data.shape or geometry['access_grid'].dtype != np.uint8
            or not np.array_equal(geometry['access_affine'], volume.affine)
            or not np.isin(geometry['access_grid'], [0,1,2,3,4,5]).all()):
        raise ValueError('Invalid saved anatomical access field.')
    AccessPolicy(**json.loads(str(geometry['access_policy_json']))['policy'])
    n = len(geometry['outer'])
    for key in ['outer', 'inner', 'normals']:
        if geometry[key].shape != (n, 3):
            raise ValueError('Invalid saved CHARM boundary arrays.')
    for key in ['errors', 'normal_mm']:
        if geometry[key].shape != (n,):
            raise ValueError('Invalid saved CHARM property arrays.')
    for key in ['faces', 'inner_faces']:
        faces = geometry[key]
        if faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in 'iu' or np.any(faces < 0) or np.any(faces >= n):
            raise ValueError('Invalid saved CHARM surface indices.')
    if geometry['center'].shape != (3,) or not np.isfinite(geometry['center']).all() or not np.isfinite(geometry['outer']).all() or not np.isfinite(geometry['inner']).all():
        raise ValueError('Invalid saved CHARM surface coordinates.')
    return geometry, root, manifest


def analyze_mri_skull(volume, target, options=None, cancel=None, transducer=None):
    """Prefer verified saved CHARM surfaces; this function never launches CHARM."""
    _check(cancel)
    if saved_charm_info(volume) is None:
        return analyze_t1(volume, target, options, cancel)
    geometry, root, manifest = load_saved_geometry(volume, cancel)
    result = analyze_geometry(volume, target, geometry, SkullOptions(), cancel)
    result.method = 'Saved CHARM '+manifest['simnibs_version']
    result.segmentation_path = str(root)
    result.coverage_scope = json.loads(str(geometry['access_policy_json']))['scope']
    from transducer_access import filter_transducer_access
    return filter_transducer_access(result, geometry, transducer, cancel)


def _native_labels(path, volume):
    import nibabel as nib
    from nibabel.processing import resample_from_to
    image = nib.load(path)
    if image.shape != volume.data.shape or not np.allclose(image.affine, volume.affine, atol=1e-5):
        image = resample_from_to(image, (volume.data.shape, volume.affine), order=0)
    return np.asarray(image.dataobj)


def build_geometry(volume, subject_dir, exports_dir, cancel=None):
    """Prepare native-grid labels and geometric surfaces once per segmentation."""
    import nibabel as nib
    subject_dir, exports_dir = Path(subject_dir), Path(exports_dir)
    exports_dir.mkdir(exist_ok=True)
    final_path = exports_dir/'charm_tissues_native.nii.gz'
    raw_path = exports_dir/'charm_raw_labels_native.nii.gz'
    final = _native_labels(final_path if final_path.exists() else subject_dir/'label_prep'/'tissue_labeling_upsampled.nii.gz', volume).astype(np.uint8)
    raw = _native_labels(raw_path if raw_path.exists() else subject_dir/'segmentation'/'labeling.nii.gz', volume).astype(np.uint16)
    bone = np.isin(final, [7, 8])
    brain = np.isin(raw, [2, 3, 41, 42, 7, 8, 46, 47])
    if bone.sum() < 100 or brain.sum() < 100:
        raise ValueError('CHARM tissue labels lack a supported bone or brain compartment.')
    indices = np.asarray(ndimage.center_of_mass(brain))
    location = tuple(np.rint(indices).astype(int))
    if not (brain[location] or raw[location] in (24, 520, 4, 43, 14, 15)):
        points = np.argwhere(brain[::2, ::2, ::2])*2
        indices = points[np.argmin(np.sum((points-indices)**2, axis=1))].astype(float)
    center = volume.physical(indices)
    policy = AccessPolicy()
    field, intracranial_distance, summary = access_field(final, raw, volume.affine, brain, policy)
    # Search the entire sphere. Anatomy/exterior access, rather than a z cutoff,
    # determines whether each entry can be retained.
    units = fibonacci(4000)
    step = float(min(volume.spacing))/2
    distance = np.arange(0, np.linalg.norm((np.array(volume.data.shape)-1)*volume.spacing), step)
    outer = np.full((len(units), 3), np.nan); inner = outer.copy()
    reasons = []
    for i, unit in enumerate(units):
        if i % 32 == 0:
            _check(cancel)
        xyz = center+distance[:, None]*unit
        ijk = (xyz-volume.affine[:3, 3])/volume.spacing
        profile = sample_field(field,volume.affine,xyz)
        reason = 'no_cranial_bone'
        if np.any(profile == 4):
            reason = 'excluded_anatomy'
        elif np.any(profile == 5):
            reason = 'face_or_neck'
        for a,b in bone_runs(profile):
            if distance[a] < 20 or (b-a+1)*step < .9:
                continue
            gap = ndimage.map_coordinates(intracranial_distance,ijk[a:a+1].T,order=1,mode='constant',cval=np.inf)[0]
            if gap > policy.intracranial_gap_mm:
                reason = 'not_adjacent_to_intracranial_space'
                continue
            accepted, reason = screen_profile(profile,distance,a,b,policy)
            if not accepted:
                continue
            inner[i] = center+max(0, distance[a]-.5*step)*unit
            outer[i] = center+(distance[b]+.5*step)*unit
            break
        reasons.append(reason)
    geometry = make_surface_geometry(units, outer, inner, center, cancel=cancel)
    summary['sampled_directions'] = len(units)
    summary['boundary_pairs'] = len(geometry['outer'])
    summary['screening_counts'] = dict(Counter(reasons))
    geometry.update(access_grid=field,access_affine=volume.affine.copy(),
                    access_policy_json=np.asarray(json.dumps(summary)))
    _check(cancel)
    for data, name in [(final, 'charm_tissues_native.nii.gz'), (raw, 'charm_raw_labels_native.nii.gz'),
                       (bone.astype(np.uint8), 'charm_bone_mask_native.nii.gz')]:
        if (exports_dir/name).exists():
            continue
        image = nib.Nifti1Image(data, volume.affine)
        image.header.set_xyzt_units('mm')
        image.set_qform(volume.affine, code=1); image.set_sform(volume.affine, code=1)
        nib.save(image, exports_dir/name)
    return geometry


def store_completed_charm(source_path, subject_dir, exports_dir=None, provenance=(),
                          source_sha256=None, simnibs_version='4.6.0', replace=False, adopt_existing=False,
                          method='CHARM T1-only, compact + spongy bone'):
    """Copy all completed results beside the MRI; publish the manifest last."""
    volume = load_mri(source_path)
    if source_sha256 is not None and volume.source_sha256 != source_sha256:
        raise ValueError('The MRI changed since CHARM ran; refusing to associate these results.')
    subject_dir = Path(subject_dir).resolve()
    required = ['T1.nii.gz', 'settings.ini', 'charm_report.html',
                'segmentation/labeling.nii.gz', 'label_prep/tissue_labeling_upsampled.nii.gz']
    for name in required:
        if not (subject_dir/name).is_file():
            raise ValueError('Incomplete CHARM output: missing '+name)
    processed_input = load_mri(subject_dir/'T1.nii.gz')
    if (processed_input.data.shape != volume.data.shape or
        not np.allclose(processed_input.affine, volume.affine, atol=1e-5) or
        not np.array_equal(processed_input.data, volume.data)):
        raise ValueError('CHARM input does not match this MRI; refusing to save mismatched results.')
    del processed_input
    root = cache_folder(source_path)
    if (root/'manifest.json').exists() and not replace:
        if saved_charm_info(volume):
            load_saved_geometry(volume)
            return root
    with cache_lock(root):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
        if adopt_existing:
            if not subject_dir.is_relative_to(root.resolve()) or subject_dir.parent == root.resolve():
                raise ValueError('Only a dedicated run inside the MRI results folder can be adopted.')
            bundle = subject_dir.parent
            destination = subject_dir
        else:
            bundle = root/'runs'/stamp
            bundle.mkdir(parents=True)
            destination = bundle/subject_dir.name
            shutil.copytree(subject_dir, destination)
        # Verify every original CHARM file was copied intact before activation.
        copied = []
        for original in subject_dir.rglob('*'):
            if original.is_file():
                target = destination/original.relative_to(subject_dir)
                digest = file_sha256(original)
                if not adopt_existing and digest != file_sha256(target):
                    raise IOError('CHARM results copy failed verification: '+str(target))
                copied.append(dict(path=target.relative_to(root).as_posix(), sha256=digest, bytes=target.stat().st_size))
        exports = bundle/'exports'
        if exports_dir is not None:
            shutil.copytree(exports_dir, exports)
        else:
            exports.mkdir()
        evidence = bundle/'provenance'; evidence.mkdir()
        for filename in provenance:
            shutil.copy2(filename, evidence/Path(filename).name)
        geometry = build_geometry(volume, destination, exports)
        geometry_path = bundle/f'planning_geometry_v{GEOMETRY_VERSION}.npz'
        np.savez_compressed(geometry_path, **geometry)
        if file_sha256(volume.source_path) != volume.source_sha256:
            raise ValueError('MRI changed while saving CHARM results; cache was not activated.')
        manifest = dict(schema_version=SCHEMA_VERSION, geometry_version=GEOMETRY_VERSION, complete=True,
            saved_utc=datetime.now(timezone.utc).isoformat(), source_name=Path(source_path).name,
            original_source_path=str(Path(source_path).resolve()), source_sha256=volume.source_sha256,
            display_shape=list(volume.data.shape), display_affine_ras_mm=volume.affine.tolist(),
            simnibs_version=simnibs_version, method=method,
            subject_directory=destination.relative_to(root).as_posix(), exports_directory=exports.relative_to(root).as_posix(),
            geometry=geometry_path.relative_to(root).as_posix(), geometry_sha256=file_sha256(geometry_path),
            copied_charm_files=copied, geometry_points=len(geometry['outer']))
        temporary = root/('manifest_'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        os.replace(temporary, root/'manifest.json')
        (root/'README.txt').write_text('Saved CHARM results for '+Path(source_path).name+'\n'
            'manifest.json selects the completed run. Keep this folder beside the MRI.\n'
            'runs contains the complete segmentation, reports, transforms, logs, native labels and reusable planning geometry.\n'
            'Changing a target recalculates angles/paths only; it does not rerun CHARM.\n', encoding='utf-8')
    return root
