"""Reviewable experimental T1 skull morphology planning interface."""
import csv
from dataclasses import asdict
import json
import numpy as np
from PySide6 import QtCore, QtWidgets as Qt
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from skull_analysis import SkullOptions, analyze_t1, window_labels, demo_t1, angle_colors
from charm_cache import analyze_mri_skull, saved_charm_info


def surface_slice_segments(vertices, faces, axis, cut):
    """Intersect supported triangles with a slice, retaining anatomical holes."""
    triangles = vertices[faces]
    distances = triangles[:,:,axis]-cut
    crossings = []
    valid = []
    for a,b in ((0,1),(1,2),(2,0)):
        da,db=distances[:,a],distances[:,b]
        mask=((da <= 0)&(db > 0))|((db <= 0)&(da > 0))
        fraction=np.divide(da,da-db,out=np.zeros_like(da),where=mask)
        crossings.append(triangles[:,a]+fraction[:,None]*(triangles[:,b]-triangles[:,a]))
        valid.append(mask)
    valid=np.stack(valid,axis=1)
    crossings=np.stack(crossings,axis=1)
    supported=valid.sum(axis=1)==2
    return crossings[supported][valid[supported]].reshape(-1,3)


class SkullDialog(Qt.QDialog):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.setWindowTitle('Skull morphology · MRI planning')
        self.resize(740, 610)
        self.result = None
        self.worker = None
        self.volume = None
        self.target = None
        self.items = []
        self.review_after_job = False
        self.previous_result = None
        layout = Qt.QVBoxLayout(self)
        note = Qt.QLabel('T1 low-signal-band estimates · upper calvarium only\n'
            'Review cyan inner / orange outer boundaries against MRI. Dark CSF and other tissues can resemble bone. '
            'Thickness is an MRI band estimate; this does not predict transmission or establish safe delivery.')
        note.setWordWrap(True)
        note.setStyleSheet('color:#f6bb6a')
        layout.addWidget(note)
        self.source_note=note
        self.cache_label=Qt.QLabel('No saved CHARM segmentation selected.')
        self.cache_label.setWordWrap(True)
        layout.addWidget(self.cache_label)
        row = Qt.QHBoxLayout()
        self.target_button = Qt.QPushButton('Set target at MRI crosshair')
        self.demo_button = Qt.QPushButton('Load synthetic skull demo')
        row.addWidget(self.target_button); row.addWidget(self.demo_button)
        layout.addLayout(row)
        self.target_label = Qt.QLabel('No committed target. Select a brain point in the MRI, then set target.')
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)
        self.crosshair_label = Qt.QLabel('')
        self.crosshair_label.setWordWrap(True)
        layout.addWidget(self.crosshair_label)
        form = Qt.QFormLayout()
        self.dark = Qt.QDoubleSpinBox(); self.dark.setRange(.05, .7); self.dark.setSingleStep(.02); self.dark.setValue(.28)
        self.minimum = Qt.QDoubleSpinBox(); self.minimum.setRange(.5, 10); self.minimum.setValue(1.5); self.minimum.setSuffix(' mm')
        self.maximum = Qt.QDoubleSpinBox(); self.maximum.setRange(2, 25); self.maximum.setValue(12); self.maximum.setSuffix(' mm')
        self.angle = Qt.QSpinBox(); self.angle.setRange(1, 80); self.angle.setValue(25); self.angle.setSuffix('°')
        form.addRow('Low-signal fraction of MRI intensity range', self.dark)
        form.addRow('Minimum candidate band thickness', self.minimum)
        form.addRow('Maximum candidate band thickness', self.maximum)
        form.addRow('Candidate angle criterion (editable, not a safety limit)', self.angle)
        layout.addLayout(form)
        row = Qt.QHBoxLayout()
        self.calculate = Qt.QPushButton('Calculate insertion map at crosshair')
        self.cancel = Qt.QPushButton('Cancel'); self.cancel.setEnabled(False)
        self.export = Qt.QPushButton('Export CSV + provenance…'); self.export.setEnabled(False)
        row.addWidget(self.calculate); row.addWidget(self.cancel); row.addWidget(self.export)
        layout.addLayout(row)
        self.display_mode = Qt.QComboBox()
        self.display_mode.addItems(['Insertion angles · changes with target', 'MRI boundaries · fixed skull anatomy'])
        layout.addWidget(self.display_mode)
        self.review = Qt.QCheckBox('I have reviewed the candidate boundaries against the MRI')
        layout.addWidget(self.review)
        self.status = Qt.QLabel('Boundary estimates are unreviewed. 0° = perpendicular; 90° = grazing.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.table = Qt.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['Patch', 'Outer °', 'Inner °', 'Path mm', 'Normal mm', 'Fit RMS mm', 'Apex–skull mm'])
        self.table.setSelectionBehavior(Qt.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(Qt.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(Qt.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(Qt.QHeaderView.Stretch)
        layout.addWidget(self.table)
        self.legend = Qt.QLabel('Angle map: 0° green · 15° cyan · 30° yellow · 60–90° red · Grey = unknown\n'
            'White outlines = candidate windows; yellow line = selected target path.\n'
            'Boundary mode: orange outer / cyan inner. Review records inspection; it does not hide results.')
        self.legend.setWordWrap(True); layout.addWidget(self.legend)
        self.target_button.clicked.connect(self.commit_target)
        self.demo_button.clicked.connect(self.load_demo)
        self.calculate.clicked.connect(self.calculate_at_crosshair)
        self.cancel.clicked.connect(self.cancel_job)
        self.export.clicked.connect(self.save)
        self.review.toggled.connect(self.refresh)
        self.display_mode.currentIndexChanged.connect(self.refresh)
        workspace.skull_contour.toggled.connect(self.redraw_contour)
        workspace.skull_opacity.valueChanged.connect(self.redraw_contour)
        self.angle.valueChanged.connect(self.refresh)
        for control in (self.dark, self.minimum, self.maximum):
            control.valueChanged.connect(self.settings_changed)
        self.table.itemSelectionChanged.connect(self.select_row)
        self.finished.connect(lambda *_: self.cancel_job())
        workspace.volume_changed.connect(self.volume_changed)
        self.update_source_status()

    def load_demo(self):
        self.workspace.set_volume(demo_t1())
        self.commit_target()

    def commit_target(self):
        if self.workspace.volume is None:
            self.status.setText('Load a full-head T1 MRI first.')
            return
        previous = self.result
        reviewed = self.review.isChecked() and previous is not None and self.volume is self.workspace.volume
        new_target = self.workspace.volume.physical(self.workspace.indices)
        if previous is not None and np.allclose(new_target, self.target):
            self.status.setText('Target is unchanged. Move the crosshair to a different brain point, then apply it.')
            return
        self.invalidate()
        self.previous_result = previous
        self.volume = self.workspace.volume
        self.target = new_target
        self.target_label.setText('Committed target RAS+ mm: '+', '.join(f'{v:.2f}' for v in self.target)+
                                  ' · Slice browsing will not move this target.')
        self.crosshair_changed()
        if previous is not None:
            self.start(preserve_review=reviewed)
        else:
            self.status.setText('Target set. Choose Calculate insertion map at crosshair to calculate its angles and paths.')

    def volume_changed(self, volume):
        self.invalidate()
        self.volume = self.target = None
        self.previous_result = None
        self.target_label.setText('MRI changed. Set a new target at the MRI crosshair.')
        self.crosshair_changed()
        self.update_source_status()

    def update_source_status(self):
        volume=self.workspace.volume
        cached=False
        try:
            info=saved_charm_info(volume) if volume is not None else None
            if info is not None:
                cached=True
                self.cache_label.setText('Saved CHARM segmentation: '+str(info[0])+'\nReusing stored boundaries; CHARM will not be rerun.')
            else:
                self.cache_label.setText('No saved CHARM segmentation beside this MRI · experimental T1 band estimator.')
        except (ValueError, OSError, KeyError) as exc:
            cached=True
            self.cache_label.setText('Saved CHARM result unavailable: '+str(exc))
        self.source_note.setText(('MRI-derived skull morphology · anatomically screened cranium\n' if cached else
                                  'MRI-derived skull morphology · upper calvarium only\n')+
            ('Saved CHARM bone labels and boundary geometry. Inspect inner/outer boundaries against MRI. ' if cached else
             'Experimental low-signal bands; dark CSF and other tissues can resemble bone. ')+
            'Thickness is an MRI estimate; this does not predict transmission or establish safe delivery.')
        for control in (self.dark,self.minimum,self.maximum):
            control.setEnabled(self.worker is None and not cached)

    def settings_changed(self):
        self.invalidate()
        self.previous_result = None
        self.status.setText('Extraction settings changed. Recalculate and review boundaries.')

    def calculate_at_crosshair(self):
        """The primary calculation action always uses the visibly selected point."""
        if self.worker is not None:
            return
        volume = self.workspace.volume
        if volume is None:
            self.status.setText('Load a full-head T1 MRI first.')
            return
        point = volume.physical(self.workspace.indices)
        if self.volume is not volume or self.target is None or not np.allclose(point, self.target):
            self.commit_target()
            # Applying a changed target to an existing result starts the job.
            if self.worker is not None:
                return
        self.start(preserve_review=self.review.isChecked())

    def clear_overlays(self):
        view = self.workspace.volume_view
        for item in self.items:
            if item in view.items:
                view.removeItem(item)
        self.items = []
        for pane in self.workspace.panes.values():
            if hasattr(pane, 'skull_outer'):
                pane.skull_outer.setData([], [])
                pane.skull_inner.setData([], [])

    def invalidate(self):
        self.cancel_job()
        self.result = None
        self.review.setChecked(False)
        self.export.setEnabled(False)
        self.table.setRowCount(0)
        self.clear_overlays()
        self.workspace.skull_status.hide()

    def cancel_job(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.status.setText('Cancelling analysis…')

    def start(self, checked=False, preserve_review=False):
        if self.worker is not None:
            return
        if self.target is None or self.volume is not self.workspace.volume:
            self.status.setText('Set a target on the current full-head T1 MRI first.')
            return
        self.invalidate()
        self.review_after_job = preserve_review
        from mri_workspace import LoadWorker
        options = SkullOptions(dark_fraction=self.dark.value(), min_band_mm=self.minimum.value(), max_band_mm=self.maximum.value())
        source, target = self.volume, self.target.copy()
        worker = LoadWorker(lambda: analyze_mri_skull(source, target, options,
                                             worker.isInterruptionRequested), ())
        self.worker = worker
        self.workspace.jobs.add(worker)
        self.calculate.setEnabled(False); self.cancel.setEnabled(True)
        self.target_button.setEnabled(False); self.demo_button.setEnabled(False)
        for control in (self.dark, self.minimum, self.maximum):
            control.setEnabled(False)
        self.status.setText('Loading saved CHARM geometry or estimating T1 boundaries; calculating target paths…')
        worker.result.connect(lambda result: self.receive(worker, result))
        worker.failure.connect(lambda message: self.status.setText('Analysis unavailable: '+message))
        worker.finished.connect(lambda: self.worker_finished(worker))
        worker.start()

    def receive(self, worker, result):
        if not worker.isInterruptionRequested() and self.volume is self.workspace.volume:
            self.result = result
            self.review.blockSignals(True)
            self.review.setChecked(self.review_after_job)
            self.review.blockSignals(False)
            self.refresh()

    def worker_finished(self, worker):
        interrupted = worker.isInterruptionRequested()
        self.workspace.finished(worker)
        self.worker = None
        self.calculate.setEnabled(True); self.cancel.setEnabled(False)
        self.target_button.setEnabled(True); self.demo_button.setEnabled(True)
        for control in (self.dark, self.minimum, self.maximum):
            control.setEnabled(True)
        if interrupted:
            self.status.setText('Analysis cancelled; recalculate when ready.')
        self.crosshair_changed()
        self.update_source_status()

    def crosshair_changed(self):
        volume = self.workspace.volume
        if volume is None:
            self.crosshair_label.setText('')
            return
        point = volume.physical(self.workspace.indices)
        changed = self.target is not None and not np.allclose(point, self.target)
        text = 'Current crosshair RAS+ mm: '+', '.join(f'{v:.2f}' for v in point)
        if changed:
            text += f' · {np.linalg.norm(point-self.target):.1f} mm from committed target. Apply to update analysis.'
        self.crosshair_label.setText(text)
        self.crosshair_label.setStyleSheet('color:#f6bb6a' if changed else 'color:#9bb0c7')
        self.target_button.setText('Apply crosshair target && recalculate' if changed and self.result is not None
                                   else 'Set target at MRI crosshair')
        if self.result is not None:
            state = 'reviewed' if self.review.isChecked() else 'UNREVIEWED estimates'
            mode = ('GREEN TRANSDUCER ACCESS' if self.result.transducer_access is not None else 'INSERTION ANGLES') if self.display_mode.currentIndex() == 0 else 'FIXED MRI BOUNDARIES'
            target = ', '.join(f'{v:.1f}' for v in self.result.target)
            suffix = ' · Crosshair moved: Calculate to apply new target' if changed else ''
            self.workspace.skull_status.setText(f'Skull · {self.result.method} · {mode} · target RAS ({target}) mm · {state}'+suffix)
            self.workspace.skull_status.show()

    def refresh(self, *args):
        if self.result is None:
            return
        r = self.result
        reviewed = self.review.isChecked()
        filtered=r.transducer_access is not None
        self.display_mode.setItemText(0,'Transducer-accessible entries · green' if filtered else 'Insertion angles · no transducer access data')
        patches = window_labels(r, self.angle.value())
        self.rows = np.flatnonzero(patches >= 0)
        self.rows = self.rows[np.argsort(np.maximum(r.outer_angle[self.rows], r.inner_angle[self.rows]))][:200]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for row, i in enumerate(self.rows):
            values = [str(patches[i]), f'{r.outer_angle[i]:.1f}', f'{r.inner_angle[i]:.1f}',
                      f'{r.path_mm[i]:.2f}', f'{r.normal_mm[i]:.2f}', f'{r.fit_error_mm[i]:.2f}',
                      f'{r.transducer_standoff_mm[i]:.2f}' if r.transducer_standoff_mm is not None else '—']
            for col, value in enumerate(values):
                self.table.setItem(row, col, Qt.QTableWidgetItem(value))
        self.table.blockSignals(False)
        self.export.setEnabled(True)
        n = np.isfinite(r.path_mm).sum()
        count = len(np.unique(patches[patches >= 0]))
        eligible = np.sum(r.eligible(self.angle.value()))
        worst = np.maximum(r.outer_angle, r.inner_angle)
        median = float(np.nanmedian(worst))
        difference = ''
        old = self.previous_result
        if old is not None and not np.allclose(old.target, r.target):
            movement = np.linalg.norm(old.target-r.target)
            old_count = np.sum(old.eligible(self.angle.value()))
            difference = f' Target moved {movement:.1f} mm; points within criterion {old_count} → {eligible}.'
        self.status.setText(('SYNTHETIC DEMO · ' if r.synthetic else '')+r.method+' · '+
            f'{len(r.outer)} boundary points (skull shape stays fixed); {n} supported target paths. '+
            f'{eligible}/{n} within {self.angle.value()}°; median worst-interface angle {median:.1f}°.'+difference+' '+
            f'{count} connected candidate windows; showing up to 200 paths. '+
            ('Boundaries marked reviewed.' if reviewed else 'UNREVIEWED estimates: inspect boundaries using MRI boundaries mode.'))
        if filtered:
            t=r.transducer_geometry
            self.status.setText(self.status.text()+f' Transducer Ø{t["diameter_mm"]:g} mm / focus {t["focus_mm"]:g} mm: '
                f'{r.transducer_access.sum()} aperture-supported entries; only entries also within {self.angle.value()}° are shown in green. '
                'Shallow targets allow outward standoff. Sampled emitting aperture only; housing and refraction are not modelled.')
            if not eligible:
                self.status.setText(self.status.text()+' No reachable entries meet these criteria for this target.')
            self.legend.setText('Green = transducer-reachable entries within the angle criterion. Other entries are hidden.\n'
                'White outlines = candidate windows; yellow line = selected target path. Opacity/visibility controls are beneath the 3D view.')
        else:
            self.legend.setText('Angle map: 0° green · 15° cyan · 30° yellow · 60–90° red · Grey = unknown\n'
                'No transducer-access filter: saved CHARM anatomy is required for scalp clearance.\n'
                'Boundary mode: orange outer / cyan inner. White outlines = candidate windows.')
        self.draw()
        self.update_slices()
        if self.table.rowCount():
            self.table.selectRow(0)
        self.crosshair_changed()

    def add(self, item):
        self.workspace.volume_view.addItem(item)
        self.items.append(item)

    def draw(self, selected=None):
        self.clear_overlays()
        r = self.result
        if r is None:
            return
        angle_mode = self.display_mode.currentIndex() == 0
        opacity=self.workspace.skull_opacity.value()/100
        visible=self.workspace.skull_contour.isChecked() and opacity > 0
        colors = angle_colors(r) if angle_mode else np.tile([1., .55, .12, 1.], (len(r.outer), 1))
        colors[:,3] *= opacity
        faces=r.faces
        if r.transducer_access is not None:
            mask=r.eligible(self.angle.value())
            faces=faces[np.all(mask[faces],axis=1)]
            colors=np.tile([.15,.85,.3,opacity],(len(r.outer),1))
        if visible:
            self.add(gl.GLMeshItem(vertexes=r.outer, faces=faces, vertexColors=colors,
                                   smooth=True, computeNormals=False, drawEdges=False, glOptions='translucent'))
            if not angle_mode:
                self.add(gl.GLMeshItem(vertexes=r.inner, faces=faces, color=(.1, .85, 1., opacity*.7),
                                       smooth=True, computeNormals=False, drawEdges=False, glOptions='translucent'))
        self.add(gl.GLScatterPlotItem(pos=r.target[None], color=(1., .2, .7, 1.), size=12))
        if angle_mode and visible:
            labels = window_labels(r, self.angle.value())
            faces = r.faces[np.all(labels[r.faces] >= 0, axis=1)]
            edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
            edges, counts = np.unique(edges, axis=0, return_counts=True)
            boundary = edges[counts == 1]
            if len(boundary):
                self.add(gl.GLLinePlotItem(pos=r.outer[boundary].reshape(-1, 3), mode='lines',
                                          color=(1., 1., 1., opacity), width=1.5, glOptions='translucent'))
        if selected is not None:
            self.add(gl.GLLinePlotItem(pos=np.vstack([r.target, r.outer[selected]]), color=(1., 1., .3, 1.), width=3))
            self.add(gl.GLLinePlotItem(pos=np.vstack([r.outer[selected], r.outer[selected]+12*r.outer_normals[selected]]),
                                      color=(1., .6, .1, 1.), width=2))

    def redraw_contour(self, *args):
        row=self.table.currentRow()
        selected=self.rows[row] if self.result is not None and 0 <= row < len(self.rows) else None
        self.draw(selected)
        self.update_slices()

    def update_slices(self):
        if self.result is None:
            return
        from mri_workspace import PLANES
        r = self.result
        volume = self.workspace.volume
        if volume is not self.volume:
            return
        faces=r.faces
        if r.transducer_access is not None:
            mask=r.eligible(self.angle.value())
            faces=faces[np.all(mask[faces],axis=1)]
        for plane, pane in self.workspace.panes.items():
            if not hasattr(pane, 'skull_outer'):
                pane.skull_outer = pg.PlotDataItem(connect='pairs')
                pane.skull_inner = pg.PlotDataItem(connect='pairs')
                pane.box.addItem(pane.skull_outer); pane.box.addItem(pane.skull_inner)
            axis, h, v, _ = PLANES[plane]
            cut = volume.physical(self.workspace.indices)[axis]
            alpha=round(255*self.workspace.skull_opacity.value()/100)
            for points, item, color in [(r.outer, pane.skull_outer, (255,140,32,alpha)),
                                        (r.inner, pane.skull_inner, (32,223,255,alpha))]:
                if not self.workspace.skull_contour.isChecked() or not alpha:
                    item.setData([],[])
                    continue
                segments=surface_slice_segments(points,faces,axis,cut)
                voxels = (segments-volume.affine[:3, 3])/volume.spacing
                if r.transducer_access is not None:
                    color=(38,217,77,alpha)
                item.setPen(pg.mkPen(color,width=1.5))
                item.setData((volume.data.shape[h]-1-voxels[:, h]+.5)*volume.spacing[h],
                             (volume.data.shape[v]-1-voxels[:, v]+.5)*volume.spacing[v])

    def select_row(self):
        row = self.table.currentRow()
        if self.result is not None and 0 <= row < len(self.rows):
            self.draw(self.rows[row])
            self.update_slices()

    def save(self):
        if self.result is None:
            return
        path, _ = Qt.QFileDialog.getSaveFileName(self, 'Export experimental skull estimates', 'skull_morphology.csv', 'CSV (*.csv)')
        if not path:
            return
        from pathlib import Path
        r = self.result
        try:
            with open(path, 'w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow(['outer_R_mm', 'outer_A_mm', 'outer_S_mm', 'outer_angle_deg', 'inner_geometric_angle_deg',
                                 'estimated_path_mm', 'estimated_normal_thickness_mm', 'fit_rms_mm', 'candidate_patch',
                                 'transducer_accessible', 'apex_to_outer_skull_standoff_mm'])
                labels = window_labels(r, self.angle.value()) if self.review.isChecked() else np.full(len(r.outer), -1)
                for i, point in enumerate(r.outer):
                    writer.writerow([*point, r.outer_angle[i], r.inner_angle[i], r.path_mm[i], r.normal_mm[i], r.fit_error_mm[i], labels[i],
                                     bool(r.transducer_access[i]) if r.transducer_access is not None else '',
                                     r.transducer_standoff_mm[i] if r.transducer_standoff_mm is not None else ''])
            metadata = dict(method=r.method, saved_segmentation=r.segmentation_path, source=r.source_name,
                synthetic=r.synthetic, target_ras_mm=r.target.tolist(), center_ras_mm=r.center.tolist(),
                options=asdict(r.options), reviewed=self.review.isChecked(), angle_criterion_deg=self.angle.value(),
                affine_ras_mm=self.volume.affine.tolist(), scope=self.result.coverage_scope,
                transducer_geometry=r.transducer_geometry)
            Path(path).with_suffix('.json').write_text(json.dumps(metadata, indent=2, allow_nan=False), encoding='utf-8')
            self.status.setText('Exported MRI estimates and provenance: '+path)
        except OSError as exc:
            self.status.setText('Export failed: '+str(exc))

    def closeEvent(self, event):
        self.cancel_job()
        super().closeEvent(event)
