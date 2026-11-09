# Skull insertion-window analysis proposal

Reviewed 5 November 2026. This is a source-code review and integration design; no MATLAB analysis was executed and no navigation behaviour was changed.

Implementation update: the subsequent user request prioritised morphology from
T1 MRI without CT/pCT. The application now includes an experimental low-signal
band estimator, separate boundary review overlays and target-relative geometric
planning. See README.md, “Experimental T1 skull morphology”, for the implemented
scope and limitations. The CT/pCT design below remains a longer-term reference;
it is not a prerequisite for the new MRI-only prototype.

## Recommendation

Retain the MATLAB project's target-centred search. Implement it in Python as a geometry-first planner using registered CT or pseudo-CT (pCT), separate outer/inner skull surfaces, physical millimetres, and explicit uncertainty. Produce connected insertion windows and candidate transducer poses. Evaluate the actual beam footprint for shortlisted poses. Add quantitative acoustic transmission only after a separate model-validation step.

The initial output should be described as a preferred **geometric insertion window**. An incidence angle is useful for planning but does not itself measure energy transmitted to the target.

## What the MATLAB project does

Source directory: `C:\Users\Labuser\Documents\Skull_analyse_project`.

| Stage | Functions reviewed | Behaviour |
| --- | --- | --- |
| Main properties workflow | `run_skull_properties_analysis.m`, including its local functions | Load MAT/NIfTI/array; optionally extract skull; locate or mark target; generate target-centred rays; sample profiles in batches; calculate properties; project results onto skull; validate/deduplicate; optionally estimate angles; package/save table. |
| Original workflow | `run_skull_analysis.m`, `full_skull_properties_analysis.m` | Connect the original seed generation, rotated-volume sampling, property estimation and surface mapping. Optionally invoke transmission analysis. |
| Preparation | `ct_data_transformation.m`, `generate_skull_volume.m`, `identify_brain_target.m` | Dataset-specific rotations/crops and thresholds; mark a target cube with intensity 6000. Extraction crops and pads the resulting array. |
| Candidate directions | `grow_seeds_around_target.m`, `grow_seeds_around_skull.m` | Fibonacci sampling on a sphere, expanded coordinates, directional trimming and duplicate removal. |
| Original profile extraction | `find_seed2center_trim.m`, `find_seed2center.m`, `calculate_skull2center.m` | Rotate a volume/subvolume to align the candidate ray with an array axis; extract its intensity profile; retain the relevant approach to the target. |
| Original angle calculation | `calculate_insertion_angle.m` | In a 22 by 22 neighbourhood transverse to the aligned ray, collect first and last bone hits; fit a separate plane to each surface with `poly11`; compare each plane normal with the aligned beam. Requires more than 50 outer-surface samples. |
| Properties | `calculate_skull_propeties.m` | Apply threshold/tail heuristics; estimate skull path length, mean intensity and trabecular/cortical ratio; split outer cortex, diploe and inner cortex; call external `hounsfield2density`; use `c = 1.048*rho + 654` for sound speed. |
| Surface map | `plant_seed_data2skull.m`, `filter_duplicate_signal.m` | Project path data onto surface voxels, remove duplicates, validate against slice-wise closed masks, convert layer lengths using 0.44 mm. |
| Window/pose exploration | `generate_insertion_line.m` | Sort by mean outer/inner angle; search for target-to-skull distance between 58 and 60 mm; extrapolate to a 75 mm target-to-transducer distance; draw a bowl. These are script-specific choices. |
| Transmission branch | `run_skull_analysis_clean.m`, `full_skull_tra_ref_coef.m`, `calculate_tra_ref_coef.m`, `w_s_interface.m`, `s_w_interface.m`, `s_s_interface.m` | Water/outer cortex/diploe/inner cortex/water model with refraction, interface coefficients, phase, attenuation and selected multiple reflections. Frequency sweep is normally 100–1000 kHz. |
| Aperture exploration | `generate_trans_skull.m`, `calculate_tran_skull_info.m` | Construct a schematic transducer and analyse local surface slopes/properties across a footprint using fixed grids. This supports the idea of extending analysis beyond one central ray. |
| Demonstration/output | `create_demo_skull_volume.m`, example scripts, `plot_skull_analysis_result.m`, README | Synthetic layered shell and surface colour maps. Angle plotting averages outer and inner values. |

The cleaned properties wrapper already avoids the expensive per-ray 3-D rotations. That optimisation is worth retaining. Its local PCA angle path is a different estimator from the original surface-plane method.

## Details that need correction or explicit handling

1. **Default angles are placeholders.** `AngleMethod='default'` returns the prefilled 60 degrees for every accepted point. It is unsuitable as an angle guidance map. `AngleMethod='pca'` enables actual estimates. In the original wrapper, zero angles are converted to 60 degrees, which also conflates a valid perpendicular incidence with failure.

2. **PCA currently uses solid bone voxels.** `localPcaAngle` fits all thresholded voxels in a small cube, rather than a specific outer or inner boundary. Thin skulls can mix both boundaries. Thick or irregular patches can give a covariance direction unrelated to the desired surface normal. Fit separate boundary points in physical coordinates and retain fit quality.

3. **Intensity convention needs explicit metadata.** The exploratory script adds 1000 to CT/pCT, whereas the wrapper does not normalise it. Values 1300/1400/1500 therefore cannot be assumed to mean native HU thresholds. The property function passes profile intensities directly to `hounsfield2density`. Check whether each input is native HU or HU+1000 before comparing density/SDR outputs. Remove a declared legacy offset before HU conversion; do not infer it silently from a histogram.

4. **Geometry is array-based.** NIfTI loading uses `niftiread` without retaining spatial geometry. Spacing is a scalar 0.44 mm. Manual rotations, crops, expanded coordinates and row/column/slice permutations are not a registration transform. `generate_skull_volume` crops the volume without returning its origin offset. A target from the uncropped MRI cannot be reused as if it were in the cropped skull array.

5. **Profile trimming depends on batch padding.** The wrapper pads each batch to its longest ray; the kernel removes `end-50`, rather than 50 samples relative to each target endpoint. Depending on ray length and padding, this can trim different anatomical regions, leave target-marker intensities in a profile, or remove relevant skull samples. `ProfileTailTrim` does not replace the kernel's hard-coded 50. Pass explicit sample coordinates and valid lengths; remove marker-based segmentation.

6. **Surface placement should use the actual hit.** The wrapper already records outer/inner hit points, but places map entries using the legacy profile-distance divided by `sizeFactor` projection and rounding. Use interpolated physical surface intersections directly.

7. **First/last bone hits can span unrelated structures.** Multiple shell intersections, skull base, calcification or separated bone regions must not become one thickness interval. Classify intersections and validate the entry-side shell using outer/inner boundaries and intracranial context. Preserve uncertainty in ambiguous cases. Avoid closing large anatomical openings just to make the mask continuous.

8. **Angle names differ between branches.** `calculate_insertion_angle` returns beam-to-outer and beam-to-inner normal angles. In `calculate_tran_skull_info`, `inner_angle` is the angle between the outer and inner normal estimates. These quantities cannot share one ambiguous field. Nor can the two unsigned incidence angles recover the full relative orientation of two surfaces for refraction.

9. **Thickness is path-dependent.** A central-ray skull traversal length is not automatically normal skull thickness. Record both only when each has been independently defined and calculated. Layer lengths based on fabricated short-profile partitions may differ from sampled bone length.

10. **The legacy transmission model needs an independent audit.** Examples include fixed water/focal distances, empirical attenuation constants, incomplete propagation of shear-mode branches, complex critical-angle handling and a `UseLegacyW21Term` option that defaults to retaining the historical cross-layer summation term. Squaring a pressure-amplitude ratio alone should not be presented as a general energy-transmission estimate. Keep this branch outside the first release of window guidance.

## Proposed planning pipeline

### 1. Load MRI and skull data separately

Keep anatomical MRI for target selection and CT/pCT for skull geometry. Also allow imported outer/inner skull segmentations, with geometry-only results if calibrated intensities are absent.

For native CT, preserve HU calibration and spatial metadata. For pCT, store its model/version, source MRI, output units and coordinate transform. Importing an existing pCT is the first integration step; MRI-to-pCT generation is a separate model dependency. CT-to-MRI image synthesis is unnecessary: register the two volumes and transform the target.

Conventional T1 MRI intensity cannot simply be thresholded as CT. MRI-only support should use a suitable validated skull segmentation/pCT method. With an imported outer surface alone, report outer incidence only, without inventing inner angles, HU or SDR.

### 2. Use one physical planning frame

Use the application's MRI RAS+ millimetre convention. Retain each source image's full voxel-to-world affine. Name registration transforms by direction:

`point_mri = T_mri_from_ct @ A_ct @ voxel_ct`

`voxel_ct = inverse(A_ct) @ inverse(T_mri_from_ct) @ point_mri`

Use homogeneous coordinates in these equations. When CT and MRI already share a verified physical frame, registration can be identity; matching array sizes is insufficient evidence. Start with rigid registration for same-subject CT/MRI, with overlay review of skull/brain landmarks. Prefer native-resolution skull analysis with transformed targets over unnecessary repeated interpolation. Any crop must update the affine. Transform surface normals with the inverse transpose of the linear transform, then normalise.

### 3. Prepare separate skull boundaries once

Create a reviewed cranial bone segmentation with editable thresholds and modest morphology in millimetres. Derive outer and inner boundaries using exterior/intracranial context. Skull openings, incomplete field of view and complex skull-base topology can prevent reliable automatic separation; mark those regions unknown or exclude them, and allow corrected masks.

Extract separate meshes or boundary point sets. Use local boundary-plane fits or gently smoothed surface normals at a documented physical scale. Estimate normal confidence from residuals, point support, eigenvalue separation and stability across neighbourhood scales. Cache surfaces and spatial search structures independently of the target. Rendering can be decimated; calculations should retain adequate source resolution.

### 4. Search directions around a committed target

Add an explicit target object in MRI RAS+ mm. The present `MRIWorkspace.indices` represents a movable slice/cut cursor; browsing slices should not move a committed analysis target.

Generate an initial Fibonacci set in physical direction space, intersect paths with the skull, then refine around promising regions. Trial counts such as 5,000–20,000 initial directions are implementation starting points, not a runtime or accuracy claim. Assess convergence in window boundaries and candidate ranks before choosing defaults. Replace the historical array-axis trimming with anatomical/access constraints in RAS coordinates.

For an entry point `p`, target `t` and unit outward surface normal `n`:

`d = (t - p) / norm(t - p)`

`theta_outer = degrees(acos(clip(abs(dot(d, n)), 0, 1)))`

Here 0 degrees means perpendicular to the skull and 90 degrees means grazing. If displaying angle to the tangent plane, label it separately as `90 - theta_outer`. Before using the absolute dot product, validate inward ray orientation and valid outer-to-inner crossing order so an invalid approach cannot look acceptable.

Calculate `theta_inner_geometric` using the same straight candidate direction at the inner boundary. Label it geometric: it is not the incidence of a refracted acoustic ray. Preserve the normal vectors, including orientation, for a future refraction model.

Sample CT/pCT along valid skull traversals using physical step lengths and subvoxel boundary interpolation. Return traversal length, target depth, mean native HU, an explicitly defined SDR estimator and validity. Start with the legacy SDR as an optional labelled comparison metric; do not assume it equals other published or vendor SDR definitions.

### 5. Find windows and feasible transducer poses

Filter directions using geometry confidence, a configured incidence limit, depth/focal reach, accessible scalp/contact regions and exclusion masks. Do not impose a universal transmission cutoff in degrees. Keep the angle map continuous and expose the protocol/model-specific threshold.

Group neighbouring acceptable candidates on the skull mesh into connected patches. Report each window's extent, angle distribution, confidence and candidate poses. Check robustness to plausible target/registration/normal perturbations and prefer patches with margin to their boundaries. Keep unknown regions grey and separate from known unfavourable angles. If no candidates meet constraints, report that result rather than selecting the least bad point and colouring it preferred.

For each promising direction, align the calibrated transducer's local +Z toward the target and calculate its origin from the measured focal point:

`origin_mri = target_mri - R_mri_from_transducer @ field.peak`

This follows the current `AcousticPlacement.focus_at` convention. It avoids copying the MATLAB script's 75 mm focal distance: the current configured transducer is 64 mm aperture with nominal 64 mm focal distance. Evaluate roll where relevant, coupling distance, scalp/housing clearance, focal reach and exclusions; nominal focal length alone does not establish feasible contact.

For shortlisted poses, sample many rays from the calibrated emitting aperture toward the focus. At each skull hit, calculate its own incidence and valid traversal. Report central angle, high-percentile angle, invalid-ray fraction and fraction meeting the configured angle criterion, with aperture weights only when justified by calibration. A good central ray alone does not establish a usable window for the full 64 mm aperture. This remains a geometric aperture check, not coherent acoustic simulation.

Rank feasible windows transparently: valid coverage and angle robustness first; secondary traversal length/SDR metrics only when valid. Retain component metrics so users can understand tradeoffs. Avoid a score labelled transmission percentage without a validated acoustic model.

### 6. Display planning and live geometry

Add a Skull analysis panel: load CT/pCT/masks, inspect alignment and segmentation, set target, calculate, adjust the angle criterion, and select a candidate window. Overlay an angle-coloured skull surface, window boundaries, target, trajectory, surface normal, aperture footprint and transducer preview. Slice overlays should show skull boundaries and the selected path for anatomical checking.

Display candidate metrics with source modality, geometry validity and registration status. A selected candidate can update the manual transducer preview; it should not itself initiate sonication.

For live guidance, transform the actual tracked transducer pose into MRI space, trace its actual central/aperture rays and calculate entry position, incidence, beam-to-target alignment, lateral miss and measured free-field peak displacement. Live rays may not aim exactly at the target. Invalidate live metrics when tracking is stale or required transforms are unavailable. Existing MRI/head placement is an assumed offset with possible display scaling; retain preview labelling until measured rigid registration and marker-to-transducer calibration are available. Compute planning angles on unscaled image geometry.

The existing measured focal-volume overlay remains a measured free-field reference. Skull geometry analysis does not turn it into a predicted intracranial focal field.

## Integration with the Python application

| File/module | Proposed responsibility |
| --- | --- |
| `mri_volume.py` plus new `skull_volume.py` | Reuse spatial loading concepts; add modality, native affine, intensity calibration and segmentation provenance. Do not route CT through MRI-specific assumptions without adapting them. |
| New `image_registration.py` | Explicit CT-to-MRI and MRI-to-head rigid transforms, quality/provenance, transform tests and overlay inspection. SimpleITK is already available. |
| New `skull_analysis.py` | Pure numerical boundary/normal preparation, target rays, intersections, metrics, connected windows and cached results. NumPy/SciPy can support initial calculations; select a mesh backend if profiling shows a need. |
| New `skull_planning.py` | Calibrated candidate pose construction and aperture/clearance evaluation. |
| New `skull_dialog.py` or panel | Analysis controls, segmentation/alignment review, candidate table and asynchronous progress/cancellation. |
| `mri_workspace.py` | Committed target state and signals; independent CT layer; invalidate results when target or inputs change. Existing loading workers provide a pattern, but analysis needs cancellation and stale-result protection. |
| `mri_3d.py` | Separate skull mesh and window/trajectory overlays in RAS+ mm. Existing volume intensity preview is not a segmented skull surface. |
| `acoustic_scene.py` | Consume `AcousticController.mri_pose` for live evaluation and selected manual previews. Replace assumed mapping for measured navigation use. |
| `acoustic_field.py` | Supply calibrated emitting geometry, local +Z direction and `field.peak`; avoid duplicated nominal-distance constants. |

Store an analysis result with target RAS coordinates, source identifiers, image affines, registration, segmentation settings/version, threshold definition, sampling resolution, outer/inner normals, metrics, validity flags, window membership and selected pose. Cache keys must change when any relevant input changes. Save compact numeric arrays plus JSON metadata and optional CSV summaries without modifying input images or adding intensity markers.

## Implementation and validation sequence

1. Geometry planner: import CT/pCT or masks, commit target, preserve geometry, extract boundaries, calculate outer/inner geometric incidence and traversal length, and display connected candidate patches.
2. Feasible-pose assessment: integrate calibrated focus, aperture rays, scalp/coupling clearance and target/registration perturbation checks. Add HU/SDR only with confirmed scaling and definitions.
3. Live guidance: measured MRI-to-head registration and transducer attachment calibration, then compare actual pose with selected windows using freshness checks.
4. Optional acoustic refinement: evaluate shortlisted poses with a separately validated layered or wave model, including refraction, attenuation and mode conversion. Compare against hydrophone/skull measurements before quantitative transmission claims.

Validation should include analytical planes and concentric spheres, off-centre targets, anisotropic/oblique images, crop/affine round trips, HU versus declared HU+1000 equivalence, missing/ambiguous boundaries, multiple intersections and short profiles. A centred spherical-shell target must give approximately 0-degree incidence; rotating or resampling the same geometry should preserve the physical result within stated discretisation error. Aperture cases must include a favourable central ray with unfavourable peripheral rays. Use representative paired MRI/CT data to review alignment, surface error, normal stability and window robustness, then physical phantom tests for tracking guidance. Set acceptance tolerances before implementation from the intended image resolution and navigation error budget.

Use the MATLAB code as a comparison reference for carefully controlled synthetic profiles and original plane fits. Do not require agreement with placeholder 60-degree angles, marker/padding artefacts or known legacy transmission terms. Record intended deviations explicitly. The external `hounsfield2density` implementation is not included in the project folder; identify its exact installed version before attempting density parity.

## Supporting primary sources

- [Classical and learned MR to pseudo-CT mappings for accurate transcranial ultrasound simulation](https://pmc.ncbi.nlm.nih.gov/articles/PMC7616982/): evaluates MRI-to-pCT approaches and explains the skull-contrast limitations of conventional T1 MRI relative to ZTE. Supports importing validated pCT rather than CT-style thresholding of raw MRI.
- [Effect of skull porosity on ultrasound transmission and wave mode conversion at large incidence angles](https://pubmed.ncbi.nlm.nih.gov/36810723/): shows why incidence guidance is useful but a single universal angle beyond which all energy disappears is not a sufficient physical model; porosity and wave-mode conversion affect behaviour.
- [Acoustic properties across the human skull](https://pmc.ncbi.nlm.nih.gov/articles/PMC8642838/): experimental evidence for variation in skull acoustic behaviour. Supports retaining local geometry/properties and separately validating acoustic prediction.
- [k-Wave hounsfield2density documentation](https://www.k-wave.org/documentation/hounsfield2density.php): expects CT Hounsfield units; its optional sound-speed mapping is described for soft tissue. The project's separate empirical skull speed relation needs its own provenance and validation.
