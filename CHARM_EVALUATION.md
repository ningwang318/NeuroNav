# CHARM installation and real-MRI evaluation

Completed 5 November 2026 on the supplied `001.mgz` T1-weighted scan.

## Outcome

SimNIBS 4.6.0, including CHARM, is installed and its atlas-initialization and
tissue-segmentation stages completed with exit code 0. The reviewed skull-vault
labels are substantially more continuous than the prototype dark-band output.
The native-grid exports are ready for reuse. A subsequent persistence update
stored the complete result beside `001.mgz` in `001_charm` and added automatic
saved-geometry loading to the navigation application. The original dark-band
extractor remains a fallback for images without a saved CHARM result.

## Installation

- Installation root: `C:\Users\Labuser\SimNIBS-4.6`
- Working Python: `C:\Users\Labuser\SimNIBS-4.6\simnibs_env\python.exe`
- Command: `C:\Users\Labuser\SimNIBS-4.6\bin\charm.cmd`
- Official release: SimNIBS v4.6.0 for Windows.
- The official bundled Windows installer was used successfully. A preliminary
  Conda dependency installation failed because Surfa required a native compiler;
  the bundled installer supplies the compiled dependency. The failed environment
  was removed separately from the working `simnibs_env`.
- The navigation runtime and requirements were not modified.
- Installed dependency versions are recorded in
  `diagnostics/charm_evaluation/setup/installed_versions.json`.

The release wheel SHA256 recorded during preparation was
`73482767740119970a62270eab4232fac48bfb8ad640d1f9767ed4332a3bb9a1`.
The installation itself uses the official bundled installer, retained with the
release environment specification and setup logs under the diagnostics folder.

## Run and provenance

Source: `D:\LIFU\MRI\Alex Wade\001.mgz`.

The MGZ was converted to a separate NIfTI image without spatial resampling.
Voxel values and scanner-RAS affine were checked against the source. Spatial
units are millimetres and both qform and sform are coded. Source SHA256 before
and after processing matches:

`049fbf3c3cb7b5c4e3358f3887a5c11a10fea21183b52943b2c2596277ebdae1`

The native grid is 208 × 320 × 320, approximately 0.9 mm isotropic.

CHARM used its default T1-only settings, eight threads, TREGA initialization,
and the v1-1 atlas that includes fat. The command was:

```text
python -u -m simnibs.cli.charm t1_skull T1_native.nii.gz --initatlas --segment
```

Run directory: `C:\Users\Labuser\SimNIBS-4.6\evaluation`.
Subject results: `C:\Users\Labuser\SimNIBS-4.6\evaluation\m2m_t1_skull`.

The run started at 14:35:19 UTC and completed at 15:02:22 UTC, approximately
27 minutes. On this date these are also Europe/London local times.
The cortical-brain-surface and FEM-meshing stages were not needed for this
skull-label evaluation. CHARM's report is `charm_report.html` in the subject folder.

The source MRI was processed locally. Input conversion, exact command, runtime
versions, logs and metrics are retained in `diagnostics/charm_evaluation/`.

## Labels evaluated

Two CHARM outputs were inspected:

1. Raw anatomical segmentation: `segmentation/labeling.nii.gz`.
   Cortical bone is label 515 and cancellous bone is label 516.
2. Cleaned tissue segmentation:
   `label_prep/tissue_labeling_upsampled.nii.gz`.
   Compact bone is label 7 and spongy bone is label 8.

The two bone classes were combined for morphology analysis; no density or
acoustic-property conversion was performed. The final tissue grid is 0.5 mm
(374 × 575 × 575). This is a processing grid, not additional acquired MRI
resolution. Exported labels were resampled back to the original 0.9 mm MRI grid
using nearest-neighbour interpolation.

The raw and cleaned bone masks have Dice agreement 0.9478 on the native grid.
This measures agreement between two processing outputs, **not segmentation
accuracy**. Both morphology and the changed processing grid contribute to their
differences.

The whole-head bone masks include facial bones, mandible and cervical vertebrae.
They should not be treated as an already isolated cranial-vault surface.

## Sampling and geometry checks

The same 4000-direction Fibonacci construction was used, retaining its 1800
upper-directed rays. The CHARM checks use nearest-neighbour bone-label sampling
and the first eligible contiguous bone run; no gaps were filled in this sampling
analysis. Local outer normals use the prototype's separate 12 mm plane fit.

| Method | Boundary pairs | Fitted outer normals |
| --- | ---: | ---: |
| Prototype, original origin and defaults | 229 | 57 |
| Raw CHARM labels, original origin | 1793 | 1382 |
| Cleaned CHARM labels, original origin | 1789 | 1454 |
| Raw CHARM labels, brain-derived origin | 1800 | 1716 |
| Cleaned CHARM labels, brain-derived origin | 1800 | 1788 |

The same-origin rows control for moving the sampling origin. The brain-origin
rows sample a different physical upper-cap domain and should not be interpreted
as a direct anatomical coverage percentage against the original rows.

The brain-derived origin is approximately (0.125, -0.375, 8.287) mm RAS and lies
in CHARM's Sub-CSF compartment. The prototype's original full-head bounding-box
origin (-2.905, 27.363, -32.079) mm is classified as internal air. This reinforces
the need to derive skull-sampling geometry from an intracranial mask, rather
than the head/neck bounding box.

The cleaned bone mask above brain-centre Z + 10 mm (Z = 18.287 mm for this scan)
contains 307571 native-grid voxels in one connected component. Connectivity is
not proof of a watertight surface or anatomical accuracy.

The median brain-centred radial bone run is approximately 6.75 mm in both raw
and cleaned labels. This is a segmented radial path length, not a validated
normal skull-thickness measurement. It must not be substituted directly for
normal thickness on an oblique ray.

The label-sampling analysis was also checked on the known synthetic 7 mm shell:
all 1800 rays yielded boundary pairs and fitted normals, with median sampled
length 6.75 mm at the test grid resolution.

## Visual review and limits

Nine representative planes were inspected: axial Z = 30/55/80 mm, coronal
Y = -40/0/40 mm and sagittal X = -40/0/40 mm. A direct three-plane comparison
with the prototype was also generated.

The predicted upper cranial-vault bone layer is continuous over the inspected
regions, with far fewer unsupported areas than the original detector. The model
represents a bone compartment rather than isolated dark intensity bands. The
skull base, mastoid/air spaces, orbital boundaries and facial/cervical bones are
more complex and are outside the upper-vault sampling conclusion.

This is an initial visual/geometric evaluation of one T1 scan. There is no CT,
corresponding T2, or manually labelled skull reference for a surface-distance
or thickness-accuracy measurement. A complete label mask can still contain
systematic inner/outer boundary errors, particularly where CSF, dura, marrow
and compact bone have ambiguous T1 contrast. Raw and cleaned labels should
remain available for boundary review.

## Exported files

All files below are under `diagnostics/charm_evaluation/evaluation/`:

- `charm_tissues_native.nii.gz`: cleaned tissue labels on the original MRI grid.
- `charm_bone_mask_native.nii.gz`: combined cleaned bone classes 7 and 8.
- `charm_raw_labels_native.nii.gz`: original anatomical label vocabulary.
- `charm_raw_bone_mask_native.nii.gz`: combined raw bone classes 515 and 516.
- `brain_center_skull_samples.npz`: sampled boundary pairs and fitted outer normals.
- `baseline_center_skull_samples.npz`: same-origin comparison samples.
- `charm_skull_overlays.png`: nine-plane visual review.
- `baseline_vs_charm.png`: prototype versus CHARM on identical MRI planes.
- `metrics.json`: quantitative results and validation checks.

Export checks passed: native shape and affine match the MRI; units are mm;
qform/sform are coded; the combined bone mask exactly matches labels 7 or 8;
the source hash is unchanged; CHARM completed with exit code 0.

## Recommendation for our application

Use the cleaned CHARM tissue labels as a precomputed geometry source, with the
raw mask available for comparison. Cache this segmentation independently of
the target. Derive a cranial region from the intracranial compartment, isolate
inner/outer bone interfaces, and sample those surfaces evenly in physical area.
Then recompute target-specific incidence and traversal geometry when the target
changes. Preserve outer-only angle support where inner-boundary pairing is
uncertain, and label thickness as an MRI-derived estimate.

CHARM is a substantially stronger geometry source than the dark-band detector.
The application now loads prepared geometry from the verified `001_charm` folder
beside the original MRI. Restart the application, load `001.mgz`, and open Skull
analysis to use it. Target changes recompute paths and angles, while segmentation
and boundary preparation remain cached. The complete original output is retained.

## Documentation

### Expanded cranial coverage

The initial app sampling had an explicit superior-direction cutoff (`z >= 0.1`),
which produced the cap visible in the insertion view. Geometry version 2 searches
the full sphere and screens candidates against native CHARM anatomical labels.
The real MRI yields 2733 boundary pairs versus 1800 previously; 843 lie below the
brain centre. The sides and occipital skull are now represented. At target RAS
(0, -20, 40) mm, 2398 paths have supported boundaries and pass the target-specific
anatomy screen; 209 meet a 25 degree incidence threshold at both interfaces.

Screens reject eye/orbital neighbourhood, anterior-inferior face/jaw, internal air,
mucosa, optic nerves, spinal cord, repeated bone crossings and unsupported/deep
external approaches. An intracranial-adjacency check prevents facial bone from
being treated as skull simply because CHARM labels it as bone. The default eye
margin is 8 mm; maximum overlying tissue is 30 mm, with 5 mm supported exterior.
These are explicit engineering heuristics. They do not validate safe sonication
or physical transducer access. The lower accessible skull boundary depends on
the segmentation and approach. Full-sphere radial sampling can still miss
non-star-shaped anatomy and does not have equal physical surface-area density.

Before/after projections and quantitative diagnostics are under
`diagnostics/charm_evaluation/full_access`. Cache migration preserves the original
upper-cap geometry and all CHARM segmentation files; it does not run CHARM.

The real study's saved cache has been upgraded beside `001.mgz`. All 17 original
CHARM files still match their recorded SHA256 hashes, and the source MRI hash is
unchanged. Reload/retarget verification blocked CHARM, band extraction and geometry
rebuilding: targets (0,-20,40) and (25,-20,40) mm used the same 2733 boundary pairs,
with 2398/2315 supported paths and 209/164 candidates within 25 degrees. Each
calculation took about 1.4 seconds. The offscreen GUI loaded this saved backend
successfully. All 118 automated tests passed, including anatomical exclusion,
target corridor, one-time cache migration and original-file preservation checks.

### Device reach and green-only display

The user subsequently confirmed a 64 mm aperture and 63.2 mm geometrical focus.
The configured spherical emitting bowl now uses that curvature/focus; measured
acoustic field samples are unchanged. Each entry orientation places the focus
at the target and the apex 63.2 mm away. Apex-to-outer-skull standoff is
`63.2 - norm(entry - target)`: shallow entries permit outward standoff, while
negative values are rejected. Clearance also samples the entire emitting bowl,
including its 32 mm aperture radius; the rim is 54.5 mm axially from the target.
An aperture rim embedded in scalp is rejected even if the apex itself is clear.

At RAS (0,-20,40), 825 entries lie within focal reach, 54 pass sampled emitting
surface clearance, and 48 also meet the 25-degree centreline incidence criterion.
At (25,-20,40), these last two counts are 115 and 38. The GUI draws only triangles
with three eligible vertices, in translucent green; there are no displayed
entries beyond the focal reach. Slice contours use the same mask. All 122 tests
passed. Real-MRI reload and GUI verification blocked segmentation, band extraction
and geometry rebuilding; the cache manifest remained unchanged. Each target
calculation took approximately 2.7 seconds.

This is sampled geometric reach (232 emitting-surface samples at ~4 mm spacing),
not full-beam acoustic validation. Centreline anatomy and incidence checks remain
in effect. Housing/holder collisions, coupling material and skull refraction are
not modelled. Source samples outside the image require at least 5 mm of observed
exterior air before the ray leaves the field of view. Source depth, the device
parameters and aperture sample resolution are included in CSV/JSON exports.

- [Official installation](https://simnibs.github.io/simnibs/build/html/installation/simnibs_installer.html)
- [CHARM command and T1-only support](https://simnibs.github.io/simnibs/build/html/documentation/command_line/charm.html)
