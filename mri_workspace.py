"""Linked anatomical MRI views beside the live tracking scene."""
import numpy as np
from PySide6 import QtCore, QtWidgets as Qt
import pyqtgraph as pg

from mri_volume import load_mri,dicom_series,load_dicom_series
from mri_3d import MRI3DView,PLANE_AXES


PLANES={'axial':(2,0,1,('R','L','A','P')),
        'coronal':(1,0,2,('R','L','S','I')),
        'sagittal':(0,1,2,('A','P','S','I'))}


class SliceBox(pg.ViewBox):
    def __init__(self, pane):
        super().__init__(enableMenu=False)
        self.pane=pane
    def mouseDragEvent(self,event,axis=None):
        # Keep the anatomical image centered; clicks and wheel navigation remain.
        event.accept()
    def wheelEvent(self,event,axis=None):
        if self.pane.volume is not None and not event.modifiers() & QtCore.Qt.ControlModifier:
            self.pane.step(1 if event.delta()>0 else -1)
            event.accept()
        else:
            super().wheelEvent(event,axis)


class SlicePane(Qt.QFrame):
    selected=QtCore.Signal(str,object)
    sliced=QtCore.Signal(str,int)
    def __init__(self,plane):
        super().__init__()
        self.plane=plane
        self.volume=None
        self.indices=[0,0,0]
        self.setMinimumSize(80,0)
        layout=Qt.QVBoxLayout(self)
        layout.setContentsMargins(4,4,4,4)
        layout.setSpacing(2)
        self.title=Qt.QLabel(plane.upper())
        self.title.setStyleSheet('color:#67dfe1; font-size:11px;')
        heading=Qt.QHBoxLayout()
        heading.addWidget(self.title)
        self.counter=Qt.QLabel('—')
        self.counter.setAlignment(QtCore.Qt.AlignRight)
        heading.addWidget(self.counter)
        layout.addLayout(heading)
        self.stack=Qt.QStackedWidget()
        self.stack.setFixedHeight(120)
        placeholder=Qt.QLabel('Load an MRI volume\nto display this view')
        placeholder.setAlignment(QtCore.Qt.AlignCenter)
        placeholder.setStyleSheet('background:#080f1a; color:#71869d;')
        self.stack.addWidget(placeholder)
        self.canvas=pg.GraphicsLayoutWidget()
        self.canvas.setBackground('#080f1a')
        self.canvas.ci.layout.setContentsMargins(0,0,0,0)
        self.box=SliceBox(self)
        self.canvas.addItem(self.box)
        self.box.setAspectLocked(True)
        self.box.invertY(True)
        self.image=pg.ImageItem(axisOrder='row-major')
        self.box.addItem(self.image)
        self.vertical=pg.InfiniteLine(angle=90,pen=pg.mkPen('#65d7d9',width=1))
        self.horizontal=pg.InfiniteLine(angle=0,pen=pg.mkPen('#65d7d9',width=1))
        self.vertical.setZValue(2)
        self.horizontal.setZValue(2)
        self.box.addItem(self.vertical)
        self.box.addItem(self.horizontal)
        self.field_contours = {}
        self.canvas.scene().sigMouseClicked.connect(self.click)
        self.stack.addWidget(self.canvas)
        layout.addWidget(self.stack,1)
        self.orientation=Qt.QLabel('')
        self.orientation.setStyleSheet('color:#9db5cf;font-size:10px;')
        self.orientation.setAlignment(QtCore.Qt.AlignCenter)
        self.orientation.hide()
        row=Qt.QHBoxLayout()
        self.slider=Qt.QSlider(QtCore.Qt.Horizontal)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(lambda value:self.sliced.emit(self.plane,value))
        row.addWidget(self.slider,1)
        layout.addLayout(row)

    def clear(self):
        self.volume=None
        self.stack.setCurrentIndex(0)
        self.slider.setEnabled(False)
        self.counter.setText('—')
        self.orientation.setText('')
        self.image.clear()
        self.show_field_sections({})

    def show_field_sections(self, sections):
        _, horizontal, vertical, _ = PLANES[self.plane]
        for threshold, curve in self.field_contours.items():
            curve.setVisible(threshold in sections)
        for threshold, (points, color) in sections.items():
            if threshold not in self.field_contours:
                curve = pg.PlotCurveItem(pen=pg.mkPen(color, width=1.5), connect='pairs')
                self.box.addItem(curve, ignoreBounds=True)
                curve.setZValue(1)
                self.field_contours[threshold] = curve
            curve = self.field_contours[threshold]
            shape, spacing = self.volume.data.shape, self.volume.spacing
            x = (shape[horizontal] - .5 - points[:, horizontal]) * spacing[horizontal]
            y = (shape[vertical] - .5 - points[:, vertical]) * spacing[vertical]
            curve.setData(x, y)
            curve.show()

    def show_slice(self,volume,indices,levels,reset=False):
        self.volume=volume
        self.indices=list(indices)
        axis,haxis,vaxis,labels=PLANES[self.plane]
        data,spacing=volume.slice(self.plane,indices)
        self.image.setImage(data,autoLevels=False,levels=levels)
        width,height=data.shape[1]*spacing[0],data.shape[0]*spacing[1]
        self.image.setRect(QtCore.QRectF(0,0,width,height))
        x=(volume.data.shape[haxis]-1-indices[haxis]+.5)*spacing[0]
        y=(volume.data.shape[vaxis]-1-indices[vaxis]+.5)*spacing[1]
        self.vertical.setValue(x)
        self.horizontal.setValue(y)
        self.slider.blockSignals(True)
        self.slider.setRange(0,volume.data.shape[axis]-1)
        self.slider.setValue(indices[axis])
        self.slider.blockSignals(False)
        self.slider.setEnabled(True)
        self.counter.setText(f'{indices[axis]+1}/{volume.data.shape[axis]}')
        left,right,top,bottom=labels
        self.orientation.setText(f'Left: {left}    Right: {right}    Top: {top}    Bottom: {bottom}')
        self.canvas.setToolTip(self.orientation.text()+' · Wheel: slices · Ctrl+wheel: zoom')
        self.stack.setCurrentIndex(1)
        if reset:
            self.box.setRange(QtCore.QRectF(0,0,width,height),padding=.025)

    def click(self,event):
        if self.volume is None or event.button()!=QtCore.Qt.LeftButton:
            return
        if not self.box.sceneBoundingRect().contains(event.scenePos()):
            return
        point=self.box.mapSceneToView(event.scenePos())
        _,haxis,vaxis,_=PLANES[self.plane]
        hspacing,vspacing=self.volume.spacing[haxis],self.volume.spacing[vaxis]
        h,v=int(np.floor(point.x()/hspacing)),int(np.floor(point.y()/vspacing))
        if not 0<=h<self.volume.data.shape[haxis] or not 0<=v<self.volume.data.shape[vaxis]:
            return
        indices=list(self.indices)
        indices[haxis]=self.volume.data.shape[haxis]-1-h
        indices[vaxis]=self.volume.data.shape[vaxis]-1-v
        self.selected.emit(self.plane,indices)

    def step(self,direction):
        axis=PLANES[self.plane][0]
        self.slider.setValue(self.slider.value()+direction)


class AnatomicalPanel(Qt.QWidget):
    """Compact, fixed stack whose image canvases stay square as height changes."""
    def __init__(self):
        super().__init__()
        self.setFixedWidth(160)
        self.panes=[]
        layout=Qt.QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(2)
        layout.setAlignment(QtCore.Qt.AlignTop)

    def add_pane(self,pane):
        self.panes.append(pane)
        self.layout().addWidget(pane)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not self.panes:
            return
        chrome=[pane.layout().sizeHint().height()-pane.stack.height() for pane in self.panes]
        side=min(210,max(72,(self.height()-sum(chrome)-4)//len(self.panes)))
        self.setFixedWidth(side+8)
        for pane,extra in zip(self.panes,chrome):
            pane.stack.setFixedHeight(side)
            pane.setFixedHeight(side+extra)


class LoadWorker(QtCore.QThread):
    result=QtCore.Signal(object)
    failure=QtCore.Signal(str)
    def __init__(self,function,args):
        super().__init__()
        self.function,self.args=function,args
    def run(self):
        try:
            result=self.function(*self.args)
            if not self.isInterruptionRequested():
                self.result.emit(result)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failure.emit(str(exc))


class MRIWorkspace(Qt.QWidget):
    volume_changed=QtCore.Signal(object)
    slice_geometry_changed=QtCore.Signal()
    def __init__(self,tracking_view):
        super().__init__()
        self.tracking_view=tracking_view
        self.volume=None
        self.indices=[0,0,0]
        self.jobs=set()
        self.close_callback=None
        outer=Qt.QVBoxLayout(self)
        outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(5)
        self.setStyleSheet('QPushButton { padding:5px 8px; } QSpinBox, QDoubleSpinBox { padding:3px 5px; }')
        buttons=Qt.QHBoxLayout()
        self.open_button=Qt.QPushButton('Load MRI…')
        self.open_button.setObjectName('primary')
        self.dicom_button=Qt.QPushButton('DICOM folder…')
        self.clear_button=Qt.QPushButton('Unload MRI')
        self.transducer_button=Qt.QPushButton('Transducer…')
        self.skull_button=Qt.QPushButton('Skull analysis…')
        self.skull_dialog=None
        self.panes_check=Qt.QCheckBox('Show MRI views')
        self.panes_check.setChecked(True)
        for widget in [self.open_button,self.dicom_button,self.clear_button,self.transducer_button,self.skull_button]:
            buttons.addWidget(widget)
        buttons.addStretch()
        buttons.addWidget(self.panes_check)
        outer.addLayout(buttons)
        self.acoustic_status=Qt.QLabel('Ultrasound transducer and focal field')
        self.acoustic_status.setStyleSheet('color:#e0b77e;font-size:10px;')
        self.acoustic_status.setWordWrap(True)
        outer.addWidget(self.acoustic_status)
        self.open_button.clicked.connect(self.open_nifti)
        self.dicom_button.clicked.connect(self.open_dicom)
        self.clear_button.clicked.connect(self.clear)
        self.skull_button.clicked.connect(self.open_skull_analysis)
        self.panes_check.toggled.connect(self.toggle_panes)
        self.metadata=Qt.QLabel('No MRI loaded · NIfTI (.nii / .nii.gz), FreeSurfer (.mgz / .mgh), or DICOM · Files stay local')
        self.metadata.setWordWrap(True)
        self.metadata.setStyleSheet('color:#9bb0c7;font-size:11px;')
        outer.addWidget(self.metadata)
        self.progress=Qt.QProgressBar()
        self.progress.setRange(0,0)
        self.progress.setMaximumHeight(5)
        self.progress.hide()
        outer.addWidget(self.progress)
        contrast=Qt.QHBoxLayout()
        contrast.addWidget(Qt.QLabel('Window'))
        from ui_controls import CompactNumber
        self.width=CompactNumber()
        self.width.setRange(.000001,1e12)
        self.width.setDecimals(3)
        contrast.addWidget(self.width)
        contrast.addWidget(Qt.QLabel('Level'))
        self.level=CompactNumber()
        self.level.setRange(-1e12,1e12)
        self.level.setDecimals(3)
        contrast.addWidget(self.level)
        self.auto_button=Qt.QPushButton('Auto contrast')
        self.reset_button=Qt.QPushButton('Centre slices')
        contrast.addWidget(self.auto_button)
        contrast.addWidget(self.reset_button)
        contrast.addStretch()
        outer.addLayout(contrast)
        self.width.valueChanged.connect(self.render)
        self.level.valueChanged.connect(self.render)
        self.auto_button.clicked.connect(self.auto_contrast)
        self.reset_button.clicked.connect(self.centre)
        self.split=Qt.QSplitter(QtCore.Qt.Horizontal)
        self.right=AnatomicalPanel()
        right_layout=self.right.layout()
        tracking=Qt.QWidget()
        tracking_layout=Qt.QVBoxLayout(tracking)
        tracking_layout.setContentsMargins(4,4,4,4)
        tracking_layout.setSpacing(3)
        tracking_title=Qt.QLabel('LIVE TRACKING SCENE')
        tracking_title.setStyleSheet('color:#67dfe1;font-size:11px;')
        tracking_layout.addWidget(tracking_title)
        tracking_view.setMinimumHeight(150)
        tracking_layout.addWidget(tracking_view,1)
        self.left=tracking
        tracking.setMinimumWidth(200)
        self.panes={plane:SlicePane(plane) for plane in PLANES}
        three_panel=Qt.QWidget()
        three_layout=Qt.QVBoxLayout(three_panel)
        three_layout.setContentsMargins(4,4,4,4)
        three_layout.setSpacing(3)
        tools=Qt.QHBoxLayout()
        title=Qt.QLabel('3D MRI · MAIN VIEW')
        title.setStyleSheet('color:#67dfe1;font-size:11px;')
        self.fit_3d=Qt.QPushButton('Fit 3D')
        three_layout.addWidget(title)
        tools.addWidget(self.fit_3d)
        self.cut_axis=Qt.QComboBox()
        self.cut_axis.addItems(['Axial','Coronal','Sagittal'])
        tools.addWidget(self.cut_axis)
        self.cut_check=Qt.QCheckBox('Slice plane')
        self.cut_check.setChecked(True)
        tools.addWidget(self.cut_check)
        self.model_opacity=Qt.QSpinBox()
        self.slice_opacity=Qt.QSpinBox()
        for label,control,value in [('Model',self.model_opacity,35),('Slice',self.slice_opacity,65)]:
            control.setRange(5,100)
            control.setValue(value)
            control.setSuffix('%')
            control.setToolTip(label+' opacity: higher values are less transparent.')
            tools.addWidget(Qt.QLabel(label))
            tools.addWidget(control)
        self.skull_contour=Qt.QCheckBox('Show skull contour')
        self.skull_contour.setChecked(True)
        self.skull_contour.setToolTip('Show or hide skull surfaces and window outlines; the target remains visible.')
        self.skull_opacity=Qt.QSpinBox()
        self.skull_opacity.setRange(0,100)
        self.skull_opacity.setValue(25)
        self.skull_opacity.setSuffix('%')
        self.skull_opacity.setToolTip('Skull contour opacity: lower values make the brain easier to see.')
        tools.addWidget(self.skull_contour)
        tools.addWidget(Qt.QLabel('Opacity'))
        tools.addWidget(self.skull_opacity)
        tools.addStretch()
        self.mri_tools=Qt.QWidget()
        self.mri_tools.setLayout(tools)
        tools.setContentsMargins(4,0,4,0)
        outer.addWidget(self.mri_tools)
        self.three_stack=Qt.QStackedWidget()
        placeholder=Qt.QLabel('Load MRI to create the 3D working view\nScroll over the volume to reveal internal structures')
        placeholder.setAlignment(QtCore.Qt.AlignCenter)
        placeholder.setStyleSheet('color:#71869d;background:#080f1a;')
        placeholder.setMinimumHeight(190)
        self.three_stack.addWidget(placeholder)
        self.volume_view=MRI3DView()
        self.three_stack.addWidget(self.volume_view)
        three_layout.addWidget(self.three_stack,1)
        self.cut_slider=Qt.QSlider(QtCore.Qt.Horizontal)
        self.cut_slider.setEnabled(False)
        self.cut_info=Qt.QLabel('Wheel: cut in/out · Ctrl+wheel: zoom · Drag: rotate · Click cut face: crosshair')
        self.cut_info.setWordWrap(True)
        self.cut_info.setStyleSheet('color:#8ca3bc;font-size:10px;')
        self.cut_controls=Qt.QWidget()
        self.cut_controls.setSizePolicy(Qt.QSizePolicy.Ignored, Qt.QSizePolicy.Preferred)
        cut_layout=Qt.QVBoxLayout(self.cut_controls)
        cut_layout.setContentsMargins(4,0,4,0)
        cut_layout.setSpacing(3)
        cut_layout.addWidget(self.cut_slider)
        cut_layout.addWidget(self.cut_info)
        self.middle=three_panel
        self.slice_strip=right_layout
        for plane in ['axial','coronal','sagittal']:
            self.right.add_pane(self.panes[plane])
        self.split.addWidget(self.left)
        self.split.addWidget(self.middle)
        self.split.setStretchFactor(0,1)
        self.split.setStretchFactor(1,1)
        self.split.setSizes([600,650])
        scene_row=Qt.QHBoxLayout()
        scene_row.setSpacing(3)
        scene_row.addWidget(self.split,1)
        self.slice_column=Qt.QWidget()
        slice_layout=Qt.QVBoxLayout(self.slice_column)
        slice_layout.setContentsMargins(4,4,4,4)
        slice_layout.setSpacing(3)
        slice_title=Qt.QLabel('ANATOMICAL VIEWS')
        slice_title.setStyleSheet(tracking_title.styleSheet())
        slice_layout.addWidget(slice_title)
        slice_layout.addWidget(self.right,1)
        scene_row.addWidget(self.slice_column)
        outer.addLayout(scene_row,1)
        outer.addWidget(self.cut_controls)
        self.three_stack.installEventFilter(self)
        self.middle.installEventFilter(self)
        self.split.installEventFilter(self)
        self.cut_controls.installEventFilter(self)
        for pane in self.panes.values():
            pane.selected.connect(self.select)
            pane.sliced.connect(self.slice_changed)
        self.skull_status=Qt.QLabel('')
        self.skull_status.setWordWrap(True)
        self.skull_status.setStyleSheet('color:#f6bb6a;font-size:11px;')
        self.skull_status.hide()
        outer.addWidget(self.skull_status)
        self.cut_axis.currentIndexChanged.connect(self.render)
        self.model_opacity.valueChanged.connect(self.render)
        self.slice_opacity.valueChanged.connect(self.render)
        self.cut_check.toggled.connect(self.render)
        self.fit_3d.clicked.connect(self.volume_view.reset_view)
        self.cut_slider.valueChanged.connect(self.move_cut_slider)
        self.volume_view.scrolled.connect(self.scroll_3d)
        self.volume_view.selected.connect(lambda indices:self.select(self.active_plane(),indices))
        self.volume_view.failed.connect(self.failed_3d)
        self.enable_contrast(False)

    def eventFilter(self, watched, event):
        if watched in (self.three_stack, self.middle, self.split, self.cut_controls) and event.type() in (
                QtCore.QEvent.Move, QtCore.QEvent.Resize):
            QtCore.QTimer.singleShot(0, self.align_cut_controls)
        return super().eventFilter(watched, event)

    def align_cut_controls(self):
        left = self.three_stack.mapTo(self, QtCore.QPoint()).x() - self.cut_controls.x()
        right = self.cut_controls.width() - left - self.three_stack.width()
        self.cut_controls.layout().setContentsMargins(max(0, left), 0, max(0, right), 0)

    def toggle_panes(self,visible):
        self.middle.setVisible(visible)
        self.slice_column.setVisible(visible)
        self.mri_tools.setVisible(visible)
        self.cut_controls.setVisible(visible)

    def open_skull_analysis(self):
        if self.skull_dialog is None:
            from skull_dialog import SkullDialog
            self.skull_dialog=SkullDialog(self)
        self.skull_dialog.show()
        self.skull_dialog.raise_()
        self.skull_dialog.activateWindow()

    def enable_contrast(self,enabled):
        for widget in [self.width,self.level,self.auto_button,self.reset_button,self.clear_button,
                       self.cut_axis,self.cut_check,self.fit_3d,self.cut_slider,
                       self.model_opacity,self.slice_opacity]:
            widget.setEnabled(enabled)

    def active_plane(self):
        return self.cut_axis.currentText().lower()

    def scroll_3d(self,direction):
        if self.volume is None:
            return
        axis=PLANE_AXES[self.active_plane()]
        self.indices[axis]=int(np.clip(self.indices[axis]+direction,0,self.volume.data.shape[axis]-1))
        self.cut_check.blockSignals(True)
        self.cut_check.setChecked(True)
        self.cut_check.blockSignals(False)
        self.render()

    def move_cut_slider(self,value):
        if self.volume:
            self.indices[PLANE_AXES[self.active_plane()]]=value
            self.render()

    def failed_3d(self,message):
        self.cut_info.setText('3D rendering unavailable: '+message+' · The 2D views remain available.')

    def busy(self,enabled):
        self.progress.setVisible(enabled)
        for widget in [self.open_button,self.dicom_button,self.clear_button]:
            widget.setEnabled(not enabled and (widget is not self.clear_button or self.volume is not None))

    def run_job(self,function,args,callback,message):
        self.busy(True)
        self.metadata.setText(message)
        worker=LoadWorker(function,args)
        self.jobs.add(worker)
        worker.result.connect(lambda result:self.job_result(worker,result,callback))
        worker.failure.connect(self.failed)
        worker.finished.connect(lambda:self.finished(worker))
        worker.start()

    def job_result(self,worker,result,callback):
        if not worker.isInterruptionRequested():
            self.busy(False)
            callback(result)

    def finished(self,worker):
        self.jobs.discard(worker)
        worker.deleteLater()
        if not self.jobs and self.close_callback:
            callback,self.close_callback=self.close_callback,None
            QtCore.QTimer.singleShot(0,callback)

    def failed(self,message):
        self.busy(False)
        volume_status = 'Previous volume remains loaded.' if self.volume else 'No volume loaded.'
        self.metadata.setText(f'MRI load failed. {volume_status}\n{message}')

    def open_nifti(self):
        path,_=Qt.QFileDialog.getOpenFileName(self,'Load 3D MRI volume','',
            'MRI volumes (*.nii *.nii.gz *.mgz *.mgh);;FreeSurfer MRI (*.mgz *.mgh);;NIfTI MRI (*.nii *.nii.gz)')
        if path:
            self.load_path(path)

    def load_path(self,path):
        self.run_job(load_mri,(path,),self.set_volume,'Loading and orienting MRI…')

    def open_dicom(self):
        folder=Qt.QFileDialog.getExistingDirectory(self,'Select folder containing the DICOM slice files')
        if folder:
            self.run_job(dicom_series,(folder,),self.choose_series,'Finding DICOM series…')

    def choose_series(self,series):
        selected=series[0]
        if len(series)>1:
            label,ok=Qt.QInputDialog.getItem(self,'Choose MRI series','Series',[s['label'] for s in series],0,False)
            if not ok:
                self.metadata.setText('DICOM selection cancelled.')
                return
            selected=next(s for s in series if s['label']==label)
        self.run_job(load_dicom_series,(selected['files'],selected['label']),self.set_volume,'Loading DICOM volume…')

    def set_volume(self,volume):
        self.volume=volume
        self.volume_view.set_volume(volume)
        self.three_stack.setCurrentIndex(1)
        self.indices=[size//2 for size in volume.data.shape]
        shape=' × '.join(map(str,volume.data.shape))
        spacing=' × '.join(f'{v:.3g}' for v in volume.spacing)
        self.metadata.setText(f'{volume.name} · {shape} voxels · {spacing} mm\n{volume.note}')
        self.metadata.setStyleSheet('color:'+('#f6bb6a' if volume.synthetic or 'unspecified' in volume.note else '#9bb0c7')+';font-size:11px;')
        self.enable_contrast(True)
        self.panes_check.setChecked(True)
        self.auto_contrast()
        self.render(reset=True)
        self.volume_changed.emit(volume)
        QtCore.QTimer.singleShot(0,self.volume_view.reset_view)

    def auto_contrast(self):
        if self.volume is None:
            return
        low,high=self.volume.levels()
        self.width.blockSignals(True)
        self.level.blockSignals(True)
        self.width.setValue(high-low)
        self.level.setValue((low+high)/2)
        self.width.blockSignals(False)
        self.level.blockSignals(False)
        self.render()

    def centre(self):
        if self.volume:
            self.indices=[size//2 for size in self.volume.data.shape]
            self.render(reset=True)

    def select(self,plane,indices):
        self.indices=list(indices)
        self.set_active_plane(plane)
        self.render()

    def slice_changed(self,plane,value):
        self.indices[PLANES[plane][0]]=value
        self.set_active_plane(plane)
        self.render()

    def set_active_plane(self,plane):
        self.cut_axis.blockSignals(True)
        self.cut_axis.setCurrentIndex(['axial','coronal','sagittal'].index(plane))
        self.cut_axis.blockSignals(False)

    def render(self,*args,reset=False):
        if self.volume is None:
            return
        levels=(self.level.value()-self.width.value()/2,self.level.value()+self.width.value()/2)
        if hasattr(self.tracking_view,'set_phantom_mri'):
            self.tracking_view.set_phantom_mri(self.volume,levels)
        for pane in self.panes.values():
            pane.show_slice(self.volume,self.indices,levels,reset)
        plane=self.active_plane()
        self.volume_view.show_state(self.indices,levels,plane,self.cut_check.isChecked(),False,
                                    model_opacity=self.model_opacity.value()/100,
                                    slice_opacity=self.slice_opacity.value()/100)
        self.cut_slider.blockSignals(True)
        axis=PLANE_AXES[plane]
        self.cut_slider.setRange(0,self.volume.data.shape[axis]-1)
        self.cut_slider.setValue(self.indices[axis])
        description=f'{plane.title()} cut {self.indices[axis]+1}/{self.volume.data.shape[axis]}'
        self.cut_slider.blockSignals(False)
        preview='downsampled volume preview' if self.volume_view.preview.shape!=self.volume.data.shape else 'volume preview'
        self.cut_info.setText(f'{description} · Transparent background · {preview}\nClick a 2D view to select its plane · Wheel: slices · Ctrl+wheel: zoom · Drag: orbit')
        if self.skull_dialog is not None:
            self.skull_dialog.update_slices()
            self.skull_dialog.crosshair_changed()
        self.slice_geometry_changed.emit()

    def clear(self):
        self.volume=None
        if hasattr(self.tracking_view,'set_phantom_mri'):
            self.tracking_view.set_phantom_mri(None)
        self.volume_changed.emit(None)
        self.volume_view.clear_volume()
        self.three_stack.setCurrentIndex(0)
        for pane in self.panes.values():
            pane.clear()
        self.enable_contrast(False)
        self.metadata.setText('No MRI loaded · NIfTI (.nii / .nii.gz), FreeSurfer (.mgz / .mgh), or DICOM · Files stay local')

    def is_loading(self):
        return any(job.isRunning() for job in self.jobs)

    def finish_before_close(self,callback):
        self.close_callback=callback
        self.metadata.setText('Finishing the current file operation before closing…')
        for job in self.jobs:
            job.requestInterruption()
