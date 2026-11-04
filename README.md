# NeuroNav tracking workspace — v0.2

A Windows Python desktop viewer for the OptiTrack Trio 3, with a standalone
tracking service using the licensed Motive API.

## Open the application

Double-click **Launch NeuroNav.cmd** in this folder. It opens the 3D workspace
and automatically starts the local Motive API tracking service. **Close the
Motive application first**: only one tracking host can own the cameras.

The default source is **Standalone · Motive API**. The Motive application does
not run in this mode. Motive's installed DLLs, drivers and valid license are
still required. This is not a replacement for the licensed tracking engine.

The Calibration and Asset profile fields select project-local copies under
`config/`. Copies were made from the existing ProgramData configuration; the
service does not explicitly save or overwrite the selected files. Use the
browse buttons to select another exported `.mcal` or `.motive` file. Re-export
them from Motive when calibration, marker geometry or assets change. The
provided calibration's suitability and physical accuracy have not been measured.

On this computer the GUI runtime is installed at
`%TEMP%\neuro-nav-01a18756-env`. The source remains in this project folder.
If Windows clears that temporary runtime, recreate it using the setup below.

## Standalone architecture

`app.py` starts `motive_service.py` as an isolated child process. The child loads
the installed Windows x64 Motive API, initializes the licensed engine, loads
calibration and profile, waits for camera discovery, then calls `Update()` and
reads reconstructed markers and rigid-body transforms. The service disables
NatNet and VRPN streaming after loading the profile. It sends JSON frame and
model messages to the viewer over a local standard-output pipe. No network
ports are needed for this connection.

The adapter uses `ctypes` with scalar and pointer signatures checked against
the installed `MotiveAPI.h`, resolving MSVC exports from the DLL. It passes no
C++ STL values across the language boundary. Tested installation: Motive
3.3.4.1, API build 1445. Other SDK builds require compatibility verification.
The service keeps Motive's Qt5 dependencies separate from the viewer's Qt6.

**Stop tracking** and normal application exit request `Shutdown()` so the cameras
are released. If the child fails to exit within 15 seconds, the viewer terminates
its own child. Startup failures and camera-in-use errors are shown in the UI;
native diagnostics go to `motive-service.log`. Body IDs in this source are SDK
indices, not NatNet streaming IDs. Asset names provide the displayed identity.

Frame counters are local successful-`Update()` counters, not hardware timestamps.
Frame age measures time since the viewer parsed a message; it cannot reveal all
camera, solver or pipe buffering delay. Absolute end-to-end latency is unmeasured.

## Optional NatNet source

To use the earlier network receiver, stop tracking and select **Network ·
NatNet**. This optional source requires Motive running. Under **Settings → Streaming**:

Keep Motive in **Live** mode for NatNet only.

- NatNet: enabled.
- Local Interface: loopback.
- Transmission Type: Multicast.
- Labeled Markers, Unlabeled Markers, Asset Markers, Rigid Bodies: enabled.
- Up Axis: Y-Axis.
- Default command port: 1510; data port: 1511.
- Multicast group: 239.255.42.99.

The initial inspection on 29 October 2026 found these settings already enabled,
three calibrated cameras, and rigid bodies called **Head** and **Transducer**.
Neither body had a valid tracked pose during verification. Marker observations
were received live, with counts varying as visibility changed.

If Motive runs on a different computer, configure its server address and the
local receiving interface explicitly. This first version uses multicast only.
The NatNet source uses Motive's server. The default API source reads cameras
directly. Neither source currently provides camera calibration or rigid-body
creation controls inside NeuroNav.

## What the view shows

### Three-marker reference plane

Open **Reference plane → Capture / recalibrate three-marker plane…**. Keep
exactly the three reference markers visible and stationary, then choose
**Capture current three markers and save**. Capture averages two seconds of
observations (at least 45 distinct receiver frames, independent of display refresh), rejects movement over
3 mm and per-marker RMS jitter over 1 mm, and checks triangle geometry.
These thresholds are capture rejection settings, not accuracy guarantees.

The triangle centre becomes the origin. P1/P2/P3 are identified by their
opposite-edge lengths, from shortest to longest. X points P1→P2, Y is the
plane normal, and Z=X×Y. The default normal faces towards tracker +Y; the
dialog provides a normal-reversal checkbox. The reference plane is XZ, and
all three averaged markers have local Y=0. Minimum marker separation is
20 mm, triangle altitude is at least 10 mm, and side lengths must differ by
at least 2 mm for unambiguous geometric identification.

The main XYZ axes move to the captured centroid and rotate with the reference
basis. The grid is moved and rotated onto the same XZ plane. The 3D scene keeps
its tracker-space view, so cameras and observed markers do not jump when the
reference changes. Pose-table positions and orientations are expressed relative
to the new XYZ frame. P1–P3 saved labels and the gold triangle are hidden by
default; **Reference plane → Show saved triangle and P1–P3 labels** enables the
optional stored-geometry overlay. The reference remains fixed even if the
markers move or disappear. This is a
navigation coordinate alignment, not a new camera calibration, head-tracking
reference or MRI registration.

**Reference plane → Hide stationary reference markers** is enabled by default.
While a reference is active, this display filter hides up to three observations
within 2 mm of the saved marker positions, with at most one match per position.
Other observations remain visible. It does not remove markers from acquisition,
rigid-body solving, capture or diagnostics. Matching uses position, not persistent
marker identity. Uncheck the option to show them again. The status reports the
detected and hidden counts separately. The saved XYZ frame remains fixed if the
physical markers are removed.

Geometry, raw averaged marker positions, local marker positions, axes, edge
lengths, timestamp, capture jitter and configuration provenance are stored in
`config/reference_plane.json`. The original geometry template is preserved
when recalibrating, so repeated recalibrations do not gradually redefine its
side lengths. Choose **Recalibrate from saved triangle** to capture that shape
again at its current placement; the edge-length mismatch limit is 2 mm.
Matching three side lengths checks geometric compatibility, not unique object
identity: keep other reflective objects out of the capture.

After app restart the saved transform is **not automatically applied**. Use
**Reference plane → Use saved XYZ coordinates** to explicitly restore it when
the setup has not changed, or recalibrate from the saved triangle if it has.
The `--use-saved-reference` launch option explicitly restores it at startup. Choose
**Reference plane → Use original tracker coordinates** to return to the
camera calibration coordinate frame. Both operations leave the vendor camera
calibration files untouched.

Live reference capture was verified on 29 October 2026 at 13:43 UTC: 225
receiver frames, side lengths 248.796 / 199.478 / 150.146 mm, and per-marker
capture RMS jitter 0.027–0.033 mm. These are short stationary-capture stability
measurements, not absolute positioning accuracy. The saved plane was confirmed
active in the viewer. All 24 receiver, transform, geometry and capture-workflow
tests passed, including a synthetic GUI save/apply test.

- A connected Trio-style camera bar with three lens assemblies, IR-diode rings,
  shaded housing and a single label, anchored to the camera poses from the
  selected tracking engine. Individual camera XYZ glyphs are omitted. Housing
  dimensions and material details are approximate visual geometry, not measured
  case surfaces or robot collision geometry. Three near-collinear camera poses
  are required for the joined bar; other arrangements use separate housings.
- Currently reconstructed markers, shown in cyan.
- Valid rigid-body origins and orientation axes: X red, Y green, Z blue.
- A table with position in millimetres, quaternion in xyzw order, and the
  streamed rigid-body fit error. Fit error is not an absolute accuracy estimate.
- Frame number, local receive rate, frame age, and tracking validity.

API and Motive coordinates are right-handed and Y-up. The renderer internally maps
`(x, y, z)` to `(x, -z, y)` for its Z-up view, without changing the table values.
The 100 mm floor grid and camera glyph sizes are display references. Camera
fields of view, calibrated overlap volume, and an accuracy-qualified tracking
boundary are **not** provided by this viewer.

In the optional NatNet source, stored marker-set coordinates can be emitted even when an asset is untracked.
These are deliberately excluded from the live point cloud. Model-filled and
occluded labeled markers are excluded. In API mode, the viewer uses reconstructed
markers with at least two contributing camera rays. Untracked rigid-body poses are hidden
and their table values replaced with dashes. After 500 ms without a decoded
frame, all live observations are hidden and the interface reports stale data.
Camera poses remain as reference geometry. Frame age measures local time since
receipt, not camera-to-screen latency. Motive remains responsible for the
validity of its calibration and any stream scaling; use the default scale 1.

Drag to orbit, use the mouse wheel to zoom, and Ctrl + drag to pan. Use **Fit
scene** to frame the cameras, origin, and current observations. **Reset view**
returns to the ground-plane origin. **Save diagnostics** exports a local JSON
summary; it does not upload anything.

**Camera view** frames a close-up of the Trio-style bar to inspect the housing,
lenses and diode rings; **Fit scene** returns to the workspace overview.

## Approximate lab appearance

With a reference plane active, the viewer includes a static xArm-style robot
with white rounded links and grey joints, a silver optical breadboard, a black
tripod with telescopic legs and green accents, and a camera mounting head/cable.
These approximate the supplied setup photograph. The camera lens poses still
come from the tracker. Tripod dimensions, floor height, table dimensions, robot
base placement and the folded robot posture are estimated appearance geometry.
There is no joint telemetry, robot-base registration or collision model.

Use **View → Approximate robot, tripod and breadboard** to show or hide the
illustration. **Fit scene** frames the full setup with the robot to the left and
the camera/tripod to the right. The reference grid becomes a smaller patch at
the breadboard level; the dark floor is a separate visual backdrop. The three
reference markers remain hidden according to the existing display setting.

The breadboard has no legs: its underside sits directly on the same ground
level as the tripod feet. The tripod leg junction is close to the head,
leaving a short centre neck; the calibrated camera bar stays fixed.
The view uses the ground normal as its upright direction, avoiding a tilted
horizon. **Fit scene** and **Reset view** centre the breadboard while framing
the equipment; this changes only the viewing camera, not tracking coordinates.
The calibration grid and XYZ triad are hidden by default; the saved
reference still drives all coordinate calculations. Enable **Calibration grid
and XYZ** under Display only when that overlay is useful.

## Scope

This release implements live tracking and a local anatomical MRI viewer. It does not yet
perform patient registration, calibrate a transducer focus, estimate acoustic
exposure, move a robot, or trigger ultrasound. It makes no targeting-accuracy
or treatment-readiness claim.

## MRI workspace

The navigation area has three columns: live tracking on the left, the main
3D MRI volume in the middle, and axial/coronal/sagittal views stacked on the
right. The dividers are resizable. Use **Load MRI…** for a local `.nii` or `.nii.gz` volume, or
**DICOM folder…** for a folder containing standard individual slice files.
When multiple DICOM series are present, choose the intended 3D series. Files
are read locally; they are not uploaded to a service. No patient volume is
automatically loaded or copied into the project.

The views use radiological orientation: axial/coronal have patient R at screen
left, sagittal has A at screen left, and orientation captions label all edges.
Images are axis-aligned in RAS+ millimetres and displayed with their physical
pixel aspect ratio. Oblique/sheared inputs are linearly reformatted into true
anatomical planes. NIfTI metre/micron units are converted to millimetres; an
unknown unit produces a visible assumption notice. A NIfTI qform or sform is
required to establish orientation. DICOM LPS geometry is converted to RAS+,
using GDCM acquisition order rather than filenames. Mixed, duplicated or
irregularly spaced slices are rejected. Multi-frame DICOM, 4D time series and
standalone 2D screenshots are not supported. The viewer caps volume size at
128 million voxels.

Click an image to move linked crosshairs. Use its slider or mouse wheel to
change slices; Ctrl+wheel zooms. **Window**, **Level**, **Auto contrast** and
**Centre slices** control the view. **Show MRI views** hides the three image
panes when a larger tracking scene is preferred. **Unload MRI** releases the
current displayed volume. **Demo** uses a visibly labelled synthetic phantom;
it is not patient data or an anatomical atlas.

### Main 3D MRI working view

The center panel keeps a complete MRI model and one anatomical slice in the
same 3D scene. Choose **Axial**, **Coronal** or **Sagittal**, or click/scroll
in the corresponding 2D pane on the right. The cyan plane changes orientation
and position while the 3D camera stays fixed. Axial planes are horizontal in
MRI space; coronal and sagittal planes are vertical. Arbitrary cutting is no
longer exposed in the interface.

Scroll over the 3D scene to advance the selected anatomical slice. Drag to
orbit, Ctrl+wheel to zoom, and **Fit 3D** to restore framing. Clicking the
slice image moves the linked crosshair. **Slice plane** toggles the image and
cyan outline while keeping the complete MRI visible.

The slice background is transparent, including low-intensity black areas,
and its tissue image is translucent. Separate **Model** and **Slice** opacity
controls adjust their visibility; larger percentages are less transparent.
Defaults are 35% model transfer opacity and 65% slice opacity. Model opacity
scales the volume-rendering transfer function, rather than defining one
uniform surface alpha. Contrast and source data remain independent of opacity.

The renderer uses an intensity-based volume preview (not a segmented brain
surface), capped at 192 samples per axis to bound GPU memory. The exposed cut
face and 2D views retain the loaded anatomical-grid resolution. Contrast controls
apply to all views. Moving the slice changes display geometry only; the MRI array is
unchanged. The 3D scene uses MRI RAS+ millimetres, suitable for a future acoustic
overlay after its coordinate transform is established. Acoustic overlays and
patient registration are not yet implemented. The MRI remains separate from
the tracking scene.

MRI coordinates are independent of the marker/reference/robot coordinates.
The interface displays **MRI ↔ tracker: NOT REGISTERED**. No spatial MRI
overlay or patient registration is implied by showing the images alongside
tracking. MRI viewing is a research prototype, not a diagnostic viewer.

MRI I/O and resampling run on a worker thread, while tracking continues. Closing
during a load waits for the current file operation to finish. MRI orientation,
spacing, DICOM ordering/rejection and linked navigation are covered by tests;
Tests cover generated MRI loading, physical transforms, fixed-camera anatomical
plane selection, transparent backgrounds, independent opacity controls,
cut-plane placement, picking and linked wheel navigation. Clinical/patient images have not yet
been tested in this session.

## Recreate the short-path runtime

The project folder's long name exceeds Windows' path limit for some Qt package
files. Use a short virtual environment path. On this computer, in PowerShell:

```powershell
$navBase = 'C:\Users\Labuser\miniconda3\envs\focusshift\python.exe'
$navEnv = "$env:TEMP\neuro-nav-01a18756-env"
& $navBase -m venv --system-site-packages --without-pip $navEnv
& $navBase -m pip --python $navEnv install -r requirements.txt
& "$navEnv\Scripts\python.exe" app.py
```

The GUI uses PySide6 Essentials, pyqtgraph and OpenGL. The API service uses
Python's standard library and installed vendor DLLs; the viewer-side adapter
uses NumPy for camera rotation conversion. The optional NatNet receiver understands the viewer-relevant frame
sections for NatNet 3.1 and 4.0–4.2; live hardware verification used **4.2**.
Later protocols fail visibly rather than being guessed. Camera descriptions are
re-requested every five seconds. Streamed skeletons and other asset types are
outside the viewer's scope.

## Verification

```powershell
& "$env:TEMP\neuro-nav-01a18756-env\Scripts\python.exe" -m unittest -v test_tracking test_api_tracking
& "$env:TEMP\neuro-nav-01a18756-env\Scripts\python.exe" app.py --smoke-seconds 5
```

On 29 October 2026 the standalone service processed 600 frames in five seconds
with Motive closed, loaded all three calibrated camera poses and both named
rigid bodies, and reconstructed up to three visible markers. The viewer adapter
then processed 504 frames with zero message errors and shut down cleanly.
The desktop viewer was visually verified receiving approximately 120 frames/s
through the API service. Head and Transducer remained untracked during these
tests, so valid live rigid-body pose accuracy has not been validated.
Fourteen unit tests passed. These tests do not establish physical tracking accuracy.

`probe_tracking.py` performs read-only NatNet diagnostics against local Motive and
saves raw server, model and frame packets under `diagnostics/`. These captures
are ignored by Git. It neither modifies Motive nor sends actuator commands.

References:

- [OptiTrack data streaming](https://docs.optitrack.com/v3.1/motive/data-streaming)
- [NatNet data types](https://docs.optitrack.com/v3.0/developer-tools/natnet-sdk/natnet-data-types)
- [Motive API quick start](https://docs.optitrack.com/developer-tools/motive-api/motive-api-quick-start-guide)



The right-hand anatomical views use a compact fixed-width stack with no resize dividers. MRI texture initialization explicitly activates the MRI view's OpenGL context, including when replacing a volume after the viewer is already open.


Anatomical image canvases stay square and fit the available vertical space. Dragging cannot pan or scale these images; clicks, slice sliders, wheel slicing, and Ctrl+wheel zoom remain available. Slice counters share the compact title row, with orientation details available in the image tooltip.


## Tracked MRI head model

The left tracking scene renders the complete loaded MRI head, using a bounded
128-sample-per-axis volume preview with the same intensity/contrast data as
the main MRI scene. The generic illustrated head has been replaced. Loading
or unloading MRI updates this model; slice navigation does not cut the left
model. Fresh Head rigid-body poses move and rotate it. Tracking loss hides it.

**Head phantom settings...** edits the selected rigid-body name and the
marker-to-head offset. The head coordinate origin is the center of its bottom,
+Y points superior/up and +Z points anterior/toward the nose. MRI RAS+ geometry
is mapped into this right-handed head frame using millimetres converted to
metres. By default, physical MRI dimensions preserve native proportions.
Display head bounds are inferred from non-background MRI intensities, not
from a segmented anatomical surface. The MRI's declared/assumed units still
apply. Turning off the MRI-size option allows manual dimension adjustment.

The initial upright placement assumes the marker rigid-body origin is 50 mm
above the head top and the nose points toward the camera side of the table.
Adopting a new MRI height preserves that top-to-marker distance. The left
scene is labelled **ASSUMED OFFSET**: showing the MRI at a tracked pose does
not establish a measured anatomical registration. A rigid-body pivot may
differ from the marker-cluster centroid. Enter measured XYZ offsets and
fixed-axis X/Y/Z rotations, or capture the current upright assumed placement,
then Apply to save config/head_phantom.json. A changed marker attachment or
pivot requires updating this transform. No robot or ultrasound control is
performed.

## Photo-fitted table appearance (29 October 2026)

The tabletop illustration is now estimated at 450 x 600 mm, with 25 mm
hole pitch and approximately 50 mm margins. A planar fit to the visible
hole grid in Weixin Image_20261029174521_116_2.jpg rectifies the ruler's
150 mm span to about 148 mm. The saved CS-200 marker separations provide
an additional scale check (approximately 150, 200 and 250 mm). The table
edges are aligned to the marker triangle instead of the camera direction.
Photo marker correspondence is P1 = right/front, P2 = left/rear,
P3 = left/front. The inferred reference-frame center is about X=33 mm,
Z=225 mm and yaw=-143.14 degrees.

The top surface is provisionally 18 mm below the sphere-center reference
plane, and thickness is provisionally 72 mm. These vertical dimensions
cannot be measured reliably from the single photograph. The table bottom
and tripod feet continue to share the ground level. The robot base is an
approximate 160 x 150 mm plate near the rear edge; its posture remains a
static illustration. These changes do not modify live marker positions,
head/MRI alignment, camera calibration, or robot control.

Use View > Table size and placement... to adjust dimensions, reference-frame
center/top/yaw, or the robot-base offsets. Apply saves config/table_layout.json.

## Measured ultrasound transducer and focal volumes

Open **Transducer...** beside the MRI loading buttons. The selected profile
is the supplied Sonic Concepts single-element 64 mm / 500 kHz measurement.
The original MAT amplitude array and exact three FIG meshes are bundled in
assets/acoustics/measured_500khz.npz. No analytical ellipsoid replaces the
measured contours, and disconnected side lobes are retained. Colors/alphas
are copied from the figure: -3 dB red (0.91,0,0,0.7), -6 dB yellow-green
(0.66,1,0.09,0.5), -12 dB cyan (0,0.7,1,0.3). Level assignments follow the
user's inner-to-outer contour description. Their thresholds are not
recomputed from the MAT amplitudes, whose physical units/dB convention are
unspecified. inspect_acoustic_fig.m records the read-only MATLAB extraction.

The MAT has shape 200 x 80 x 80 and contains no coordinate vectors. FIG axes
are unlabelled sample coordinates. Its first plotted coordinate corresponds
to MAT axis 0. Row 1 is far field; row 200 is near field. Enter positive
axial/lateral sample spacing and surface-to-last-slice distance, then confirm
them before enabling physical MRI overlays. In the local right-handed
transducer frame, +Z points downstream, apex is surface center, +X follows
the second MAT axis, and +Y opposes the third MAT axis. MATLAB mesh indices
are one-based; downstream distance is (200 - first_coordinate)*axial_spacing
plus the near-slice offset. The lateral origin is the sample-grid midpoint.
The field preview explicitly uses sample coordinates while calibration is
unknown. A separate device schematic preview is available; its bowl curvature
is illustrative until the radius of curvature is entered.

**Manual MRI preview** positions the transducer apex in MRI RAS millimetres,
with fixed-axis X/Y/Z rotation. The center-peak button previews the measured
peak at MRI center. **Advance along beam** moves the body and all contour
meshes together along local +Z, allowing intersection with the MRI. This is
labelled MANUAL MRI PREVIEW and is not mistaken for optical tracking. The
left scene maps that same geometry through the current head-marker placement
when a fresh head pose exists. The main MRI slice does not clip the acoustic
contours; they are drawn transparently above the MRI to aid visibility.

**Marker attachment** stores the rigid-body name and surface-apex translation
and rotation in marker-local coordinates. Tracked mode remains disabled until
those measurements are confirmed; fresh head and transducer poses are both
required for MRI overlay. It uses the current assumed head/MRI placement,
not a measured anatomical registration. Settings persist in
config/acoustic_placement.json and config/transducer_500khz.json. Additional
profiles using the same NPZ schema can be selected from config/transducer_*.json.

This displays measured relative free-field geometry. It does not calculate
skull propagation, in-situ pressure/dose, control robot motion or emit ultrasound.

Confirmed measurement metadata (30 October 2026): axial and both lateral
spacings are 0.5 mm; matrix row 200 lies at the surface; radius of curvature
and nominal focal distance are 64 mm. These values place the measured maximum
at local (-0.25, -0.25, 42.5) mm, 42.5 mm downstream. The peak is deliberately
not shifted to the nominal 64 mm focus. This discrepancy remains visible in
the control dialog and should be resolved against the acquisition origin
before relying on the absolute field placement. The exact supplied contours
and measured spatial offsets are preserved.

The MRI-preview-pose tab can also place the measured peak at the linked MRI
crosshair. Select the desired point in a 2D MRI pane, open Transducer...,
choose Preview measured peak at linked MRI crosshair, then Apply. This is a
manual planning preview only and sends no movement or exposure command.
OpenGL resources are shared across the main window and the local preview
dialog so their cached pyqtgraph shaders remain valid.

## Synchronized live transducer preview

Choose Live marker preview in Transducer... > MRI preview pose to move both
representations from the Transducer rigid body. This mode uses the entered
marker-local attachment provisionally; it does not mark that attachment as
measured/calibrated. With zero offsets, the surface apex is at the marker-body
pivot and the beam follows marker-local +Z. Enter the attachment specifications
when available, then use calibrated tracking mode.

The left scene displays the transducer directly in tracker coordinates and
replaces its XYZ axes. The middle scene uses the same instantaneous pose
transformed relative to the head/MRI frame. Its MRI volume and camera do not
move or auto-fit to the approaching transducer. A transducer outside the MRI
view appears when it comes into that fixed field of view. The left scene
continues to show live transducer motion if head tracking is lost; the middle
transducer is hidden until a fresh head pose returns. Loss of transducer
tracking hides both live representations. Manual mode remains available for
planning with the beam-advance control.

Axial reversal correction: at the user's request, Reverse axial matrix and
focal contours is now enabled for the 500 kHz profile. The working volume is
reversed along axis 0 and the FIG meshes are reflected at x -> 201 - x, with
triangle winding corrected. The current peak is (-0.25, -0.25, 57.0) mm.
The earlier 42.5 mm value describes the previous direction. Transducer pose,
marker attachment and lateral geometry are unchanged. Both scenes and the
local preview use the same corrected field. The reversal checkbox persists
in the transducer profile; the original supplied MAT/FIG files are untouched.
