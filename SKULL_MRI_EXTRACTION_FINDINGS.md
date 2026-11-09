# Real-MRI skull extraction investigation

5 November 2026. Local source: `D:\LIFU\MRI\Alex Wade\001.mgz`.
The input image was read without modification. All experiments and generated
images remain in `diagnostics/skull_real_mri/`; application extraction defaults
have not been changed by this investigation.

## Main finding

The current independent, globally thresholded dark-band detector is not a
sufficient skull segmentation method for this scan. It misses most sampled
directions, confuses possible tissue interfaces, and produces too sparse a
surface for reliable normals and target paths. More seeds or simply closing
holes does not solve this failure.

The recommended next implementation is a target-independent, anatomically
constrained multi-tissue segmentation, followed by separate inner/outer skull
surface extraction. Retain a lightweight local-contrast/spatial-consistency
method as an explicitly experimental fallback. Treat angle support and thickness
support separately; do not require both surfaces just to show an outer-surface
angle, and do not invent thickness where the inner boundary is unresolved.

## Image and baseline

- Anatomically reformatted MRI: 208 × 320 × 320 voxels, approximately 0.9 mm
  isotropic. Scanner RAS geometry was retained through the existing loader.
- Intensity percentiles: 2nd = 0; subsampled 99th = 1235.
- The default global dark threshold is 345.8; the bright-flank threshold is
  444.6. These are arbitrary MRI intensity values, not physical tissue units.
- Diagnostic target: RAS approximately (-2.455, 26.013, -16.329) mm, the image
  grid centre. This was chosen to exercise the code, not as a treatment target.
  Boundary extraction itself is independent of this target.
- The full-head bounding-box centre is approximately (-2.905, 27.363, -32.079)
  mm. Inspection shows the field of view includes substantial neck and face;
  this centre is displaced from a useful cranial sampling origin.
- Of 4000 Fibonacci sphere directions, 1800 remain after the upper-cap cutoff.
  The baseline accepts 229 boundary pairs (12.7% of that sampling budget).
- Only 57 outer normals and 150 supported surface triangles remain; only 23
  target paths pass the subsequent geometry checks.

## Exact rejection accounting

The offline diagnostic reproduces the production count of 229 accepted rays.
Each of the 1800 sampled directions receives exactly one terminal classification:

| Terminal result | Directions |
| --- | ---: |
| Geometrically plausible band but bright-flank test fails | 841 |
| No dark band satisfying the geometric constraints | 707 |
| Accepted | 229 |
| Multiple accepted bands, therefore ambiguous | 5 |
| No sustained bright tissue run for scalp detection | 18 |

The diagnostic also counts individual candidate rejection events: 1436 width,
1116 depth, and 1257 flank rejections. Those events are not mutually exclusive
per ray and must not be added to the terminal-direction counts.

Observed profile failures include brain/CSF intensities below the global
threshold, so several tissues merge into one large dark interval; insufficient
brightness adjacent to a legitimate-looking dark interface; and multiple
low-signal interfaces that cannot be identified anatomically from one ray.
Some accepted intervals may represent a cortical plate or CSF boundary rather
than the complete skull. No CT or manually labelled skull reference was
available, so these cannot be assigned an accuracy score.

## Experiments actually executed

### End-to-end threshold sweep, with identical centre and target

| Dark fraction | Candidate boundary pairs | Valid outer normals | Valid target paths | Median reported normal-band thickness |
| --- | ---: | ---: | ---: | ---: |
| 0.28, current default | 229 | 57 | 23 | 2.77 mm |
| 0.20 | 582 | 282 | 185 | 2.42 mm |
| 0.15 | 684 | 307 | 179 | 2.15 mm |

More candidate points do not produce monotonically more valid paths. The
thickness estimate changes with threshold. This supports revising the tissue
model rather than changing the default to the value yielding the most points.
These are reported MRI band lengths, not reference skull thicknesses.

### N4 pilot

Using SimpleITK N4, a foreground mask `intensity > 50`, shrink factor 3, and
iteration limits [20, 10], then applying the fitted field at full resolution:
the default detector accepts 242 pairs instead of 229. At dark fraction 0.15,
the count remains 684. This limited pilot does not establish that the scan is
free of bias or that a longer/more carefully masked N4 fit would not help. It
shows that this preprocessing trial alone does not repair the detector.

### Sampling-centre experiment

A manually chosen experimental cranial centre of approximately (-2.905, -10,
15) mm gives 1075 accepted rays with threshold 0.15. However, moving the centre
also moves the upper-cap sampling domain. That count must not be compared as
an anatomical coverage percentage with the baseline's 229.

To separate these effects, a second control aimed new-centre rays through the
same 1782 scalp locations identifiable by the baseline:

| Same reference scalp-location set | Dark fraction | Accepted rays |
| --- | ---: | ---: |
| Original centre | 0.28 | 229 |
| Cranial centre | 0.28 | 236 |
| Original centre | 0.15 | 683 |
| Cranial centre | 0.15 | 784 |

The centre has a real effect, but global tissue assumptions are the dominant
failure in this comparison. A replacement should define its scalp/calvarial
region anatomically, rather than by an arbitrary cap around the full-head box.

### Local contrast and spatial consistency prototype

An offline prototype uses threshold 0.15, the manually selected cranial centre,
and more permissive local flank contrast to generate alternative interfaces.
Neighbouring strict candidates then constrain scalp-relative depth and band
width. The current ray is excluded from its own neighbour evidence; at least
six independent anchors are required. Missing intensity evidence is not filled.

The local-only trial gives 887 single candidates, 788 ambiguous rays and 125
without accepted local evidence. Spatial disambiguation results are:

| Result | Rays |
| --- | ---: |
| Accepted single candidate with neighbour support | 731 |
| Disambiguated using neighbouring evidence | 744 |
| Spatial outlier | 141 |
| Insufficient neighbours | 54 |
| No local evidence | 125 |
| Remaining ambiguity | 5 |

Total: **1475 experimental boundary pairs**. The comparison images show much
more continuous calvarial candidates and removal of some isolated internal
picks. This is candidate recovery, not validated bone accuracy. Its sampling
domain differs from the baseline. It can still consistently select the wrong
interface, including only one cortical plate. It has not replaced production
extraction or been used to claim validated thickness/transmission.

## Recommended implementation

1. **Segment anatomy before planning.** Establish a brain/intracranial mask and
   scalp support, excluding neck/face from cranial-centre estimation. Keep their
   transforms in native MRI RAS millimetres. This is separate from the selected
   ultrasound target.
2. **Use an anatomical tissue model.** Prefer an established T1 whole-head
   segmentation such as SimNIBS CHARM as an offline backend, then import its
   tissue labels/surfaces. It supports T1 alone; its documentation recommends
   T2 for more reliable skull segmentation. SimNIBS and FreeSurfer commands
   were not available in the inspected runtime, so neither backend was run.
3. **For a lightweight fallback**, combine local intensity/gradient evidence
   with scalp and intracranial constraints and spatially coupled candidate
   selection. Model inner cortex, marrow/diploe and outer cortex as one skull
   compartment rather than assuming the entire skull is one uniformly dark
   interval. Keep confidence/ambiguity and evidence provenance at each boundary.
   The executed spatial prototype establishes a direction for this work; it is
   not a completed multi-tissue model.
4. **Build two separate surfaces**, enforce anatomical nesting, and inspect
   them against MRI. Avoid a fixed-distance expansion of the brain or shrinkage
   of the scalp as a thickness measurement. Surface regularity should resolve
   locally supported alternatives; large unsupported gaps remain unknown.
5. **Resample the supported surfaces uniformly in physical area**, then compute
   normals in millimetres. This addresses visible sampling distribution only
   after extraction quality has been addressed.
6. **Separate output validity.** Allow outer-angle guidance where the outer
   normal is supported. Return thickness/inner-angle only where inner/outer
   pairing is supported. Show unknown regions and confidence instead of hiding
   them. Current angle colouring requires a complete target path, discarding
   useful outer-only geometry.
7. **Cache segmentation separately from the target.** Target changes should
   recompute paths and angles, not tissue extraction. Include image/extraction
   provenance in the cache key.
8. **Validate surfaces, not point counts.** First manually review representative
   boundary traces over superior, frontal, parietal and occipital regions and
   threshold sensitivity; obtain corresponding T2 or expert labels if available.
   Measure surface distance, normal stability, nesting and boundary uncertainty.
   Use those results to determine which thickness estimates are supportable.

## Reproduction and artifacts

From the project directory using the configured NeuroNav Python runtime:

```python
import runpy
runpy.run_path('diagnostics/skull_real_mri/run_all.py', run_name='__main__')
```

- `diagnostics/skull_real_mri/diagnosis.json`: exact per-ray rejection counts and threshold sweeps.
- `diagnostics/skull_real_mri/end_to_end.json`: production-path threshold comparison.
- `diagnostics/skull_real_mri/experiments.json`: raw/N4/centre experiments.
- `diagnostics/skull_real_mri/matched_scalp_control.json`: control for the changed sampling domain.
- `diagnostics/skull_real_mri/spatial_trial.json`: local/spatial prototype accounting.
- `diagnostics/skull_real_mri/profile_failures.png`: example intensity profiles and thresholds.
- `diagnostics/skull_real_mri/extraction_comparison.png`: baseline and experimental candidate overlays.

The diagnostics directory is already excluded from Git. It contains locally
derived MRI images/profiles; they have not been uploaded to an external service.

## Primary documentation consulted

- [SimpleITK N4 example](https://simpleitk.readthedocs.io/en/latest/link_N4BiasFieldCorrection_docs.html): shrink-fit/full-resolution correction workflow.
- [SimNIBS CHARM](https://simnibs.github.io/simnibs/build/html/documentation/command_line/charm.html): T1-only support and benefit of supplementary T2 for skull segmentation.
- [FreeSurfer mri_watershed](https://freesurfer.net/fswiki/mri_watershed): explicitly describes the fixed brain-expansion/scalp-shrink BEM convenience surfaces and their boundary-estimation limitations. Those surfaces should not be treated as measured skull thickness.
