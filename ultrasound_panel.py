"""Compact, asynchronous signal-generator connection panel."""
from PySide6 import QtCore, QtGui, QtWidgets as Qt
import time
import threading

from signal_generator import DEFAULT_RESOURCE, FREQUENCIES_KHZ, SignalGenerator
from sonication import SonicationPattern, SessionClock
from ui_controls import TypedNumber


class SonicationDiagram(Qt.QWidget):
    """Show session windows and zoom into the second window when available."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(110)
        self.hide()

    def show_pattern(self, pattern, frequency):
        self.pattern = pattern
        self.frequency = frequency
        self.show()
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor('#0b1726'))
        painter.setFont(QtGui.QFont('Segoe UI', 8))
        pattern = self.pattern
        left, width = 9, max(1, self.width() - 18)
        highlight = QtGui.QColor('#9bdcf4')

        def caption(y, text):
            painter.setPen(QtGui.QColor('#dceaf6'))
            painter.drawText(left, y, text)

        def timeline(y, span, windows, *, pulses=False, selected=None):
            painter.setPen(QtGui.QPen(QtGui.QColor('#7093ad'), 1))
            painter.drawLine(left, y, left + width, y)
            for index, (start, end) in enumerate(windows):
                x = left + width * start / span
                right = left + width * end / span
                if pulses:
                    painter.fillRect(QtCore.QRectF(x, y - 8, max(.75, right - x), 16),
                                     QtGui.QColor('#275f7d'))
                    painter.setPen(QtGui.QPen(highlight, 1))
                    for tick in range(int(x) + 2, int(right), 4):
                        painter.drawLine(tick, y - 6, tick, y + 6)
                else:
                    rect = QtCore.QRectF(x, y - 8, max(.75, right - x), 16)
                    painter.fillRect(rect, QtGui.QColor('#4c9dc0'))
                    if index == selected:
                        painter.setPen(QtGui.QPen(highlight, 2))
                        painter.drawRect(rect)

        if pattern.mode == 'continuous':
            duration = pattern.cycles(self.frequency) / (1000 * self.frequency)
            caption(18, f'Continuous wave · {duration:g} s ON')
            timeline(43, duration * 1.15, [(0, duration)])
            caption(72, 'Sine carrier throughout the ON window')
            return

        cycle = pattern.sonication_s + pattern.interval_s
        span = min(pattern.session_s, 2 * cycle)
        on = min(span, pattern.sonication_s)
        windows = [(0, on)]
        if cycle < span:
            windows.append((cycle, min(span, cycle + pattern.sonication_s)))
        caption(15, 'Pulse pattern')
        selected = len(windows) - 1
        start, end = windows[selected]
        timeline(32, span, windows, selected=selected)
        painter.setPen(QtGui.QPen(highlight, 1.5, QtCore.Qt.DashLine))
        painter.drawLine(QtCore.QLineF(left + width * start / span, 42, left, 66))
        painter.drawLine(QtCore.QLineF(left + width * end / span, 42, left + width, 66))
        period = pattern.period_s
        burst = pattern.cycles(self.frequency) / (1000 * self.frequency)
        zoom = min(end - start, 3 * period)
        caption(79, 'Pulse zoom')
        timeline(99, zoom, [(i * period, min(zoom, i * period + burst))
                             for i in range(3) if i * period < zoom], pulses=True)


class GeneratorWorker(QtCore.QObject):
    result = QtCore.Signal(bool, str, str)
    stopped = QtCore.Signal()
    session = QtCore.Signal(bool, str)
    confirmation = QtCore.Signal(object)

    def __init__(self, generator=None):
        super().__init__()
        self.generator = generator or SignalGenerator()
        self.clock = None
        self.cancel = threading.Event()
        self.confirmed = None
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.tick)

    @QtCore.Slot(str, object)
    def execute(self, action, payload):
        try:
            if self.clock is not None and action not in ('stop_output', 'disconnect'):
                raise RuntimeError('Stop the current sonication before changing settings.')
            if action in ('connect', 'disconnect', 'apply', 'stop_output', 'confirm'):
                self.confirmed = None
                self.confirmation.emit(None)
            if action == 'connect':
                identity = self.generator.connect(payload)
                self.result.emit(True, identity, 'Connected · identification verified')
            elif action == 'identify':
                identity = self.generator.identify()
                self.result.emit(True, identity, 'Connected · identification refreshed')
            elif action == 'apply':
                frequency, voltage = self.generator.apply_settings(*payload)
                self.result.emit(True, self.generator.identity,
                                 f'Applied CH1: {frequency:g} kHz · {voltage:g} mVpp (verified)')
            elif action == 'disconnect':
                self.finish_session('Stopped')
                self.generator.disconnect()
                self.result.emit(False, '', 'Disconnected')
            elif action == 'confirm':
                frequency, voltage, pattern, gain = payload
                if gain not in (1, 40):
                    raise ValueError('Select a supported amplifier gain.')
                self.generator.prepare_sonication(frequency, voltage, pattern)
                if self.cancel.is_set():
                    self.finish_session('Confirmation cancelled · output OFF', force=True)
                    self.result.emit(True, self.generator.identity, 'Confirmation cancelled. Please confirm settings again.')
                    return
                self.confirmed = payload
                self.confirmation.emit(payload)
                self.result.emit(True, self.generator.identity,
                                 'Frequency, voltage and pulse pattern confirmed · ready to start.')
            elif action == 'start':
                if self.confirmed is None or payload != self.confirmed:
                    raise ValueError('Please confirm frequency, voltage and pulse pattern before starting.')
                self.confirmed = None
                self.confirmation.emit(None)
                frequency, voltage, pattern, gain = payload
                self.generator.prepare_sonication(frequency, voltage, pattern)
                if self.cancel.is_set():
                    self.finish_session('Stopped before output enabled', force=True)
                    self.result.emit(True, self.generator.identity, 'Stopped · output OFF (verified)')
                    return
                self.generator.enable_sonication(pattern.mode == 'continuous')
                self.clock = SessionClock(pattern, time.monotonic())
                self.timer.start()
                self.session.emit(True, 'Sonication ON')
                self.result.emit(True, self.generator.identity, 'Session started · CH1 sine wave')
            elif action == 'stop_output':
                self.finish_session('Stopped · output OFF', force=True)
                self.result.emit(True, self.generator.identity, 'Stopped · output OFF (verified)')
            else:
                raise ValueError(f'Unknown generator action: {action}')
        except Exception as exc:
            self.handle_error(action, exc)

    def handle_error(self, action, exc):
        self.confirmed = None
        self.confirmation.emit(None)
        self.timer.stop()
        self.clock = None
        uncertain = ''
        if self.generator.session_owned:
            try:
                self.generator.stop_output()
            except Exception as stop_error:
                uncertain = f' OUTPUT OFF UNCONFIRMED: {stop_error}. Use the generator front panel.'
        try:
            self.generator.disconnect()
        except Exception as close_error:
            uncertain += f' Cleanup failed: {close_error}'
        self.session.emit(False, f'Session failed.{uncertain}')
        detail = ('Settings may be partially applied; check the generator.' if action == 'apply'
                  else 'Check USB, VISA driver, and whether another application holds the instrument.')
        self.result.emit(False, '', f'{action.capitalize()} failed: {exc}\n{detail}{uncertain}')

    @QtCore.Slot()
    def invalidate_confirmation(self):
        self.confirmed = None
        self.confirmation.emit(None)

    def finish_session(self, message, force=False):
        self.confirmed = None
        self.confirmation.emit(None)
        self.timer.stop()
        if self.clock is not None or self.generator.session_owned or force:
            self.generator.stop_output()
        self.clock = None
        self.session.emit(False, message)

    @QtCore.Slot()
    def tick(self):
        if self.clock is None:
            return
        try:
            if self.cancel.is_set():
                self.finish_session('Stopped · output OFF')
                return
            now = time.monotonic()
            action = self.clock.advance(now)
            if action == 'complete':
                self.finish_session('Session complete · output OFF')
                return
            if action == 'off':
                self.generator.pause_output()
                # Off interval begins once the output-off command has returned.
                self.clock.deadline = time.monotonic() + self.clock.pattern.interval_s
            elif action == 'on':
                # Never emit catch-up sonications after the session deadline.
                if time.monotonic() >= self.clock.end:
                    self.finish_session('Session complete · output OFF')
                    return
                self.generator.enable_sonication()
                self.clock.deadline = min(self.clock.end, time.monotonic() + self.clock.pattern.sonication_s)
            remaining = max(0, self.clock.end - time.monotonic())
            phase = 'Sonication ON' if self.clock.phase == 'on' else 'Interval OFF'
            remaining_text = f'{remaining:.1f}'.rstrip('0').rstrip('.')
            self.session.emit(True, f'{phase} · {remaining_text} s remaining')
        except Exception as exc:
            self.handle_error('sonication', exc)

    @QtCore.Slot()
    def stop(self):
        try:
            self.finish_session('Stopped · output OFF')
            self.generator.disconnect()
        except Exception as exc:
            self.handle_error('shutdown', exc)
        finally:
            self.stopped.emit()


class UltrasoundPanel(Qt.QWidget):
    request = QtCore.Signal(str, object)
    stop_requested = QtCore.Signal()
    shutdown_finished = QtCore.Signal()
    invalidate_requested = QtCore.Signal()

    def __init__(self, parent=None, generator=None):
        super().__init__(parent)
        self.connected = False
        self.closing = False
        self.active = False
        self.busy = False
        self.confirmed_snapshot = None
        self.pending_snapshot = None
        layout = Qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(QtCore.Qt.AlignTop)
        layout.setSpacing(5)
        self.setStyleSheet('''
            QPushButton { padding:6px; }
            QSpinBox, QDoubleSpinBox { padding:4px; }
        ''')
        title = Qt.QLabel('ULTRASOUND')
        title.setStyleSheet('color:#67dfe1;')
        layout.addWidget(title)
        layout.addWidget(Qt.QLabel('Signal generator'))
        self.address = Qt.QLineEdit(DEFAULT_RESOURCE)
        self.address.setToolTip(DEFAULT_RESOURCE)
        self.address.setAccessibleName('Signal generator VISA resource')
        self.address.hide()
        buttons = Qt.QHBoxLayout()
        self.connect_button = Qt.QPushButton('Connect generator')
        self.connect_button.clicked.connect(self.toggle_connection)
        self.refresh_button = Qt.QPushButton('Identify')
        self.refresh_button.setEnabled(False)
        self.refresh_button.clicked.connect(lambda: self.run('identify'))
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.refresh_button)
        layout.addLayout(buttons)
        self.status = Qt.QLabel('Disconnected')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        amplifier_form = Qt.QFormLayout()
        amplifier_form.setVerticalSpacing(4)
        self.amplifier = Qt.QComboBox()
        self.amplifier.addItem('40× amplifier', 40)
        self.amplifier.addItem('No amplifier (1×)', 1)
        amplifier_form.addRow('Power amplifier', self.amplifier)
        layout.addLayout(amplifier_form)
        form = Qt.QFormLayout()
        form.setVerticalSpacing(4)
        self.frequency = Qt.QComboBox()
        for value in FREQUENCIES_KHZ:
            self.frequency.addItem(f'{value} kHz', value)
        self.frequency.setAccessibleName('Channel 1 frequency')
        self.voltage = Qt.QSpinBox()
        self.voltage.setRange(100, 990)
        self.voltage.setSuffix(' mVpp')
        self.voltage.setValue(100)
        self.voltage.setAccessibleName('Channel 1 voltage in millivolts peak-to-peak')
        form.addRow('Frequency · CH1', self.frequency)
        form.addRow('Before amplification', self.voltage)
        self.amplified_voltage = Qt.QLabel()
        self.amplified_voltage.setWordWrap(True)
        form.addRow('After amplification', self.amplified_voltage)
        layout.addLayout(form)
        self.apply_button = Qt.QPushButton('Apply frequency and voltage')
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(lambda: self.run('apply'))
        layout.addWidget(self.apply_button)
        self.frequency.currentIndexChanged.connect(self.selection_changed)
        self.voltage.valueChanged.connect(self.selection_changed)
        self.mode = Qt.QComboBox()
        self.mode.addItems(['Pulsed sonication', 'Continuous sonication (CW)'])
        layout.addWidget(self.mode)
        self.pattern_pages = Qt.QStackedWidget()
        pulsed = Qt.QWidget()
        pulsed_form = Qt.QFormLayout(pulsed)
        pulsed_form.setVerticalSpacing(4)
        pulsed_form.setContentsMargins(0, 0, 0, 0)

        def number(minimum, maximum, value, suffix, decimals=3):
            widget = TypedNumber()
            widget.setDecimals(decimals)
            widget.setRange(minimum, maximum)
            widget.setValue(value)
            widget.setSuffix(suffix)
            return widget

        self.burst = number(.001, 10000, 10, ' ms')
        self.timing_mode = Qt.QComboBox()
        self.timing_mode.addItems(['Duty cycle (%)', 'PRF (Hz)'])
        self.timing_value = number(.001, 99.999, 50, ' %')
        self.sonication_duration = number(.001, 86400, 1, ' s')
        self.sonication_interval = number(0, 86400, 5, ' s')
        self.session_duration = number(.001, 86400, 60, ' s')
        for title, widget in [('Burst ON', self.burst), ('Timing input', self.timing_mode),
                              ('Duty cycle / PRF', self.timing_value),
                              ('Sonication duration', self.sonication_duration),
                              ('Interval OFF', self.sonication_interval),
                              ('Session duration', self.session_duration)]:
            pulsed_form.addRow(title, widget)
        self.pattern_pages.addWidget(pulsed)
        continuous = Qt.QWidget()
        continuous_form = Qt.QFormLayout(continuous)
        continuous_form.setContentsMargins(0, 0, 0, 0)
        self.continuous_duration = number(.001, 10, 1, ' s')
        continuous_form.addRow('CW duration (max 10 s)', self.continuous_duration)
        self.pattern_pages.addWidget(continuous)
        self.pattern_pages.setSizePolicy(Qt.QSizePolicy.Preferred, Qt.QSizePolicy.Maximum)
        layout.addWidget(self.pattern_pages)
        self.mode.currentIndexChanged.connect(self.change_pattern_mode)
        self.change_pattern_mode(0)
        self.timing_mode.currentIndexChanged.connect(self.change_timing_mode)
        self.diagram = SonicationDiagram()
        layout.addWidget(self.diagram)
        self.preview = Qt.QLabel()
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        for widget in (self.burst, self.timing_value, self.sonication_duration,
                       self.sonication_interval, self.session_duration, self.continuous_duration):
            widget.valueChanged.connect(self.update_preview)
        self.mode.currentIndexChanged.connect(self.update_preview)
        self.frequency.currentIndexChanged.connect(self.update_preview)
        self.voltage.valueChanged.connect(self.update_preview)
        self.amplifier.currentIndexChanged.connect(self.update_preview)
        self.start_button = Qt.QPushButton('Start sonication')
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(lambda: self.run('start'))
        self.stop_button = Qt.QPushButton('STOP · output OFF')
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet('background:#7a2936; color:white; font-weight:bold;')
        self.stop_button.clicked.connect(lambda: self.run('stop_output'))
        self.confirm_button = Qt.QPushButton('Confirm frequency, voltage and pattern')
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(lambda: self.run('confirm'))
        layout.addWidget(self.confirm_button)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        self.amplifier.currentIndexChanged.connect(self.selection_changed)
        for widget in (self.burst, self.timing_value, self.sonication_duration,
                       self.sonication_interval, self.session_duration, self.continuous_duration):
            widget.valueChanged.connect(self.selection_changed)
        self.mode.currentIndexChanged.connect(self.selection_changed)
        self.timing_mode.currentIndexChanged.connect(self.selection_changed)
        self.address.textChanged.connect(self.selection_changed)
        self.update_amplified_voltage()
        self.update_preview()
        self.thread = QtCore.QThread(self)
        self.worker = GeneratorWorker(generator)
        self.worker.moveToThread(self.thread)
        self.request.connect(self.worker.execute)
        self.stop_requested.connect(self.worker.stop)
        self.worker.result.connect(self.show_result)
        self.worker.session.connect(self.show_session)
        self.worker.confirmation.connect(self.show_confirmation)
        self.invalidate_requested.connect(self.worker.invalidate_confirmation)
        self.worker.stopped.connect(self.thread.quit, QtCore.Qt.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.shutdown_finished)
        self.thread.start()

    def toggle_connection(self):
        self.run('disconnect' if self.connected else 'connect')

    def run(self, action):
        if self.closing:
            return
        if self.busy and action != 'stop_output':
            return
        if action in ('start', 'confirm'):
            try:
                payload = self.current_snapshot()
                payload[2].validate(payload[0])
            except ValueError as exc:
                self.status.setText(str(exc))
                self.log_message(str(exc), 'ERROR')
                self.invalidate_confirmation()
                return
            if not self.connected or (action == 'start' and payload != self.confirmed_snapshot):
                self.log_message('Please connect and confirm frequency, voltage and pulse pattern before starting.', 'WARNING')
                self.invalidate_confirmation()
                return
            if action == 'confirm':
                self.pending_snapshot = payload
            else:
                self.confirmed_snapshot = None
            self.worker.cancel.clear()
        elif action == 'stop_output':
            self.worker.cancel.set()
        self.busy = True
        self.connect_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.frequency.setEnabled(False)
        self.voltage.setEnabled(False)
        self.address.setEnabled(False)
        self.start_button.setEnabled(False)
        self.confirm_button.setEnabled(False)
        self.amplifier.setEnabled(False)
        self.mode.setEnabled(False)
        self.pattern_pages.setEnabled(False)
        # Stop stays available while a start/configuration request is pending.
        self.stop_button.setEnabled(self.connected and action != 'stop_output')
        self.status.setText({'connect': 'Connecting…', 'disconnect': 'Disconnecting…',
                             'identify': 'Identifying…', 'apply': 'Applying CH1 settings…',
                             'confirm': 'Confirming frequency, voltage and pulse pattern…',
                             'start': 'Preparing sonication…', 'stop_output': 'Stopping output…'}[action])
        self.voltage.interpretText()
        if action not in ('start', 'confirm'):
            if action != 'identify':
                self.invalidate_confirmation()
            payload = ((self.frequency.currentData(), self.voltage.value())
                       if action == 'apply' else self.address.text())
        self.request.emit(action, payload)

    def current_snapshot(self):
        self.voltage.interpretText()
        return (self.frequency.currentData(), self.voltage.value(), self.make_pattern(),
                self.amplifier.currentData())

    def update_amplified_voltage(self):
        gain = self.amplifier.currentData()
        self.amplified_voltage.setText(f'{self.voltage.value() * gain / 1000:g} Vpp (calculated, {gain}×)')

    def log_message(self, message, level='INFO'):
        # Keep current errors/prompts visible without a separate message history.
        self.status.setText(message)

    def invalidate_confirmation(self):
        self.confirmed_snapshot = None
        self.pending_snapshot = None
        self.start_button.setEnabled(False)
        if hasattr(self, 'worker'):
            self.invalidate_requested.emit()

    @QtCore.Slot(object)
    def show_confirmation(self, snapshot):
        if snapshot is None:
            self.confirmed_snapshot = None
        elif snapshot == self.pending_snapshot and snapshot == self.current_snapshot():
            self.confirmed_snapshot = snapshot
            self.pending_snapshot = None

    def make_pattern(self):
        for widget in self.pattern_pages.findChildren(Qt.QDoubleSpinBox):
            widget.interpretText()
        return SonicationPattern(
            mode='continuous' if self.mode.currentIndex() else 'pulsed',
            burst_ms=self.burst.value(), timing='prf' if self.timing_mode.currentIndex() else 'duty',
            timing_value=self.timing_value.value(), sonication_s=self.sonication_duration.value(),
            interval_s=self.sonication_interval.value(), session_s=self.session_duration.value(),
            continuous_s=self.continuous_duration.value())

    def change_timing_mode(self, index):
        old = self.timing_value.value()
        self.timing_value.setRange(.001, 1_000_000 if index else 99.999)
        self.timing_value.setSuffix(' Hz' if index else ' %')
        converted = old / (self.burst.value() / 1000) / 100 if index else old * self.burst.value() / 1000 * 100
        self.timing_value.setValue(converted)
        self.update_preview()

    def change_pattern_mode(self, index):
        self.pattern_pages.setCurrentIndex(index)
        self.pattern_pages.setFixedHeight(self.pattern_pages.currentWidget().sizeHint().height())

    def update_preview(self):
        try:
            pattern = self.make_pattern()
            frequency = self.frequency.currentData()
            pattern.validate(frequency)
            voltage = self.voltage.value()
            gain = self.amplifier.currentData()
            carrier = f'{frequency:g} kHz sine · {voltage:g} mVpp CH1 · {gain:g}× gain\n{voltage * gain / 1000:g} Vpp after gain (calculated)'
            if pattern.mode == 'continuous':
                duration = pattern.cycles(frequency) / (1000 * frequency)
                text = f'Uninterrupted sine wave for {duration:g} s, then OFF.'
            else:
                burst_ms = pattern.cycles(frequency) / frequency
                text = (f'{burst_ms:g} ms ON / {pattern.period_s * 1000 - burst_ms:g} ms OFF · '
                        f'{1 / pattern.period_s:g} Hz PRF · {burst_ms / (pattern.period_s * 10):g}% duty\n'
                        f'{pattern.sonication_s:g} s sonication · {pattern.interval_s:g} s interval · '
                        f'{pattern.session_s:g} s session')
            self.diagram.show_pattern(pattern, frequency)
            self.preview.setText(text + '\n' + carrier)
            self.diagram.setAccessibleName('Sonication timing diagram')
            self.diagram.setAccessibleDescription(self.preview.text())
        except ValueError as exc:
            self.diagram.hide()
            self.preview.setText(str(exc))

    @QtCore.Slot(bool, str)
    def show_session(self, active, message):
        self.active = active
        if not active:
            self.log_message(message, 'ERROR' if 'failed' in message.lower() else 'INFO')
        if not self.closing and not self.busy:
            self.update_controls()

    def update_controls(self):
        idle = not self.active and not self.busy
        self.address.setEnabled(idle and not self.connected)
        self.connect_button.setEnabled(idle)
        self.refresh_button.setEnabled(idle and self.connected)
        self.apply_button.setEnabled(idle and self.connected)
        self.frequency.setEnabled(idle)
        self.voltage.setEnabled(idle)
        self.mode.setEnabled(idle)
        self.pattern_pages.setEnabled(idle)
        self.amplifier.setEnabled(idle)
        self.confirm_button.setEnabled(idle and self.connected)
        self.start_button.setEnabled(idle and self.connected and self.confirmed_snapshot is not None)
        self.stop_button.setEnabled(self.connected)

    def selection_changed(self):
        was_confirmed = self.confirmed_snapshot is not None
        self.invalidate_confirmation()
        self.update_amplified_voltage()
        if self.connected and self.apply_button.isEnabled():
            self.status.setText('Selection changed · please confirm frequency, voltage and pulse pattern.')
        if was_confirmed:
            self.log_message('Settings changed. Please confirm frequency, voltage and pulse pattern again.', 'WARNING')

    @QtCore.Slot(bool, str, str)
    def show_result(self, connected, identity, message):
        self.connected = connected
        self.busy = False
        if not connected:
            self.invalidate_confirmation()
        self.log_message(message, 'ERROR' if 'failed' in message.lower() else 'INFO')
        if connected and self.confirmed_snapshot is None and not self.active:
            self.log_message('Please confirm frequency, voltage and pulse pattern before starting.', 'PROMPT')
        if self.closing:
            if 'OUTPUT OFF UNCONFIRMED' in message:
                Qt.QMessageBox.warning(self, 'Ultrasound output off unconfirmed', message)
            return
        self.connect_button.setText('Disconnect generator' if connected else 'Connect generator')
        self.update_controls()
        self.status.setText(message)
        self.address.setToolTip(identity or 'Signal generator VISA resource')

    def shutdown(self):
        """Queue cleanup after any pending I/O; caller defers closing until finished."""
        if not self.thread.isRunning():
            return True
        if not self.closing:
            self.closing = True
            self.worker.cancel.set()
            self.setEnabled(False)
            self.stop_requested.emit()
        return False
