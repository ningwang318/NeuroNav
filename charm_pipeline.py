"""Run CHARM once per MRI, with all results and reuse state beside the source."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import subprocess
import uuid
import nibabel as nib
import numpy as np
from mri_volume import load_mri, file_sha256
from charm_cache import cache_folder, cache_lock, saved_charm_info, store_completed_charm


def run_or_reuse_charm(source_path, install_root=r'C:\Users\Labuser\SimNIBS-4.6', recalculate=False,
                       *, t2_path=None, no_neck=False):
    volume = load_mri(source_path)
    try:
        cached = saved_charm_info(volume)
    except ValueError:
        if not recalculate:
            raise
        cached = None
    if cached is not None and not recalculate:
        from charm_cache import load_saved_geometry
        load_saved_geometry(volume)
        print('Reusing completed CHARM segmentation: '+str(cached[0]), flush=True)
        return cached[0]
    prefix = Path(install_root)/'simnibs_env'
    python = prefix/'python.exe'
    if not python.is_file():
        raise FileNotFoundError('SimNIBS is not installed at '+str(prefix))
    t2 = load_mri(t2_path) if t2_path is not None else None
    if t2 is not None and (t2.data.shape != volume.data.shape or
                           not np.allclose(t2.affine, volume.affine, atol=1e-5)):
        raise ValueError('The optional T2 must already be aligned to the T1 on the same MRI grid.')
    root = cache_folder(source_path)
    with cache_lock(root/'jobs'):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
        work = root/'jobs'/stamp
        work.mkdir()
        input_image = work/'T1_RAS_mm.nii.gz'
        image = nib.Nifti1Image(volume.data, volume.affine)
        image.header.set_xyzt_units('mm')
        image.set_qform(volume.affine, code=1); image.set_sform(volume.affine, code=1)
        nib.save(image, input_image)
        input_t2 = None
        if t2 is not None:
            input_t2 = work/'T2_RAS_mm.nii.gz'
            image_t2 = nib.Nifti1Image(t2.data, t2.affine)
            image_t2.header.set_xyzt_units('mm')
            image_t2.set_qform(t2.affine, code=1); image_t2.set_sform(t2.affine, code=1)
            nib.save(image_t2, input_t2)
        environment = os.environ.copy()
        environment['PATH'] = os.pathsep.join([str(prefix),str(prefix/'Scripts'),str(prefix/'Library'/'bin'),environment.get('PATH','')])
        environment.update(OMP_NUM_THREADS='8', MPLBACKEND='Agg', QT_QPA_PLATFORM='offscreen')
        version = subprocess.check_output([str(python),'-c','import importlib.metadata as m; print(m.version("simnibs"))'], env=environment, text=True).strip()
        command = [str(python),'-u','-m','simnibs.cli.charm','mri',str(input_image)]
        if input_t2 is not None:
            command += [str(input_t2), '--skipregisterT2']
        command += ['--initatlas','--segment']
        if no_neck:
            command.append('--noneck')
        metadata = dict(command=command, source_path=volume.source_path, source_sha256=volume.source_sha256,
                        simnibs_version=version, started_utc=datetime.now(timezone.utc).isoformat())
        if t2 is not None:
            metadata.update(t2_source_path=t2.source_path,t2_source_sha256=t2.source_sha256,
                            t2_already_aligned=True)
        metadata['no_neck'] = bool(no_neck)
        log_path = work/'charm_console.log'
        metadata_path = work/'run_metadata.json'
        metadata_path.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        print('Running CHARM. All output is stored in '+str(work),flush=True)
        with log_path.open('w',encoding='utf-8') as stream:
            process = subprocess.Popen(command,cwd=work,env=environment,stdout=stream,stderr=subprocess.STDOUT)
            try:
                code = process.wait()
            except KeyboardInterrupt:
                process.terminate(); process.wait()
                raise
        metadata.update(exit_code=code,finished_utc=datetime.now(timezone.utc).isoformat())
        metadata_path.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        if code:
            raise RuntimeError('CHARM failed; results were not activated. See '+str(log_path))
        if file_sha256(volume.source_path) != volume.source_sha256:
            raise ValueError('MRI changed during segmentation; result was not activated.')
        if t2 is not None and file_sha256(t2.source_path) != t2.source_sha256:
            raise ValueError('T2 changed during segmentation; result was not activated.')
        return store_completed_charm(volume.source_path,work/'m2m_mri',
            provenance=[metadata_path,log_path],source_sha256=volume.source_sha256,
            simnibs_version=version,replace=recalculate,adopt_existing=True,
            method='CHARM T1 + aligned T2, compact + spongy bone' if t2 is not None else 'CHARM T1-only, compact + spongy bone')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mri',help='Original MRI (.mgz/.mgh/.nii/.nii.gz)')
    parser.add_argument('--install-root',default=r'C:\Users\Labuser\SimNIBS-4.6')
    parser.add_argument('--recalculate',action='store_true',help='Explicitly run a new segmentation and retain previous runs')
    parser.add_argument('--t2',help='Optional already-aligned T2 image on the same grid')
    parser.add_argument('--no-neck',action='store_true',help='Inform CHARM that this scan has no neck')
    args = parser.parse_args()
    print(run_or_reuse_charm(args.mri,args.install_root,args.recalculate,t2_path=args.t2,no_neck=args.no_neck))
