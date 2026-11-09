"""Wave-pattern validation, scheduling and output lifecycle; no physical instrument."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import time
from unittest.mock import patch
from dataclasses import replace

from sonication import SonicationPattern, SessionClock
from signal_generator import SignalGenerator
from test_signal_generator import FakeManager


class PatternTests(unittest.TestCase):
    def test_example_and_equivalent_prf(self):
        duty = SonicationPattern()
        prf = replace(duty, timing='prf', timing_value=50)
        for frequency in (250, 500, 826):
            duty.validate(frequency)
            prf.validate(frequency)
            self.assertEqual(duty.period_s, .020)
            self.assertEqual(duty.period_s, prf.period_s)
            self.assertEqual(duty.cycles(frequency), frequency * 10)

    def test_one_minute_has_ten_sonications_and_five_second_intervals(self):
        clock = SessionClock(SonicationPattern(), 0)
        starts, stops = [0], []
        for second in range(1, 61):
            action = clock.advance(second)
            if action == 'on':
                starts.append(second)
            elif action == 'off':
                stops.append(second)
        self.assertEqual(starts, list(range(0, 60, 6)))
        self.assertEqual(stops, list(range(1, 60, 6)))
        self.assertEqual(clock.phase, 'complete')
        self.assertIsNone(clock.advance(100))

    def test_late_timer_does_not_catch_up_or_restart_after_session(self):
        clock = SessionClock(SonicationPattern(), 0)
        self.assertEqual(clock.advance(20), 'off')
        self.assertIsNone(clock.advance(24.999))
        self.assertEqual(clock.advance(25), 'on')
        self.assertEqual(clock.advance(61), 'complete')

    def test_last_sonication_truncated_at_session_end(self):
        clock = SessionClock(replace(SonicationPattern(), session_s=6.5), 0)
        self.assertEqual(clock.advance(1), 'off')
        self.assertEqual(clock.advance(6), 'on')
        self.assertEqual(clock.deadline, 6.5)
        self.assertEqual(clock.advance(6.5), 'complete')

    def test_invalid_patterns(self):
        for values in ({'timing_value': 100}, {'burst_ms': 0}, {'session_s': 0},
                       {'interval_s': -1}, {'sonication_s': .001},
                       {'timing': 'prf', 'timing_value': 101},
                       {'session_s': float('nan')},
                       {'mode': 'continuous', 'continuous_s': 10.001},
                       {'mode': 'continuous', 'continuous_s': 0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(SonicationPattern(), **values).validate(250)

    def test_cw_is_single_hardware_bounded_sine_without_repeated_gaps(self):
        for frequency in (250, 500, 826):
            manager = FakeManager()
            generator = SignalGenerator(lambda: manager)
            generator.connect()
            pattern = SonicationPattern(mode='continuous', continuous_s=10)
            try:
                generator.prepare_sonication(frequency, 100, pattern)
                self.assertEqual(manager.instrument.state['OUTPut1'], '0')
                self.assertEqual(manager.instrument.state['SOURce1:FUNCtion'], 'SIN')
                self.assertEqual(manager.instrument.state['TRIGger1:SOURce'], 'BUS')
                cycles = int(manager.instrument.state['SOURce1:BURSt:NCYCles'])
                self.assertEqual(cycles / (frequency * 1000), 10)
                generator.enable_sonication(continuous=True)
                self.assertEqual(manager.instrument.commands.count('TRIGger1'), 1)
                generator.disconnect()
                self.assertEqual(manager.instrument.state['OUTPut1'], '0')
            finally:
                generator.disconnect()


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets as Qt
        cls.app = Qt.QApplication.instance() or Qt.QApplication([])

    def make_worker(self):
        from ultrasound_panel import GeneratorWorker
        manager = FakeManager()
        worker = GeneratorWorker(SignalGenerator(lambda: manager))
        worker.execute('connect', 'fake')
        self.addCleanup(worker.stop)
        return worker, manager.instrument

    def start_worker(self, worker):
        payload = (250, 100, SonicationPattern(), 40)
        worker.execute('confirm', payload)
        worker.execute('start', payload)

    def test_worker_session_on_off_and_completion(self):
        worker, inst = self.make_worker()
        results = []
        worker.session.connect(lambda active, message: results.append((active, message)))
        with patch('ultrasound_panel.time.monotonic', return_value=0):
            self.start_worker(worker)
        self.assertEqual(inst.state['OUTPut1'], '1')
        self.assertEqual(inst.state['SOURce1:BURSt:NCYCles'], '2500')
        self.assertEqual(float(inst.state['SOURce1:BURSt:INTernal:PERiod']), .02)
        for timestamp, output in ((1, '0'), (5.9, '0'), (6, '1'), (60, '0')):
            with patch('ultrasound_panel.time.monotonic', return_value=timestamp):
                worker.tick()
            self.assertEqual(inst.state['OUTPut1'], output)
        self.assertIsNone(worker.clock)
        self.assertFalse(worker.timer.isActive())
        self.assertIn('complete', results[-1][1])

    def test_stop_cancels_preparation_without_enabling_output(self):
        worker, inst = self.make_worker()
        worker.cancel.set()
        self.start_worker(worker)
        self.assertNotIn('OUTPut1 ON', inst.commands)
        self.assertIsNone(worker.clock)

    def test_stop_and_shutdown_disable_output(self):
        for action in ('stop_output', 'shutdown'):
            worker, inst = self.make_worker()
            self.start_worker(worker)
            self.assertEqual(inst.state['OUTPut1'], '1')
            if action == 'shutdown':
                worker.stop()
            else:
                worker.execute(action, None)
            self.assertEqual(inst.state['OUTPut1'], '0')
            self.assertIsNone(worker.clock)

    def test_setup_failure_never_enables_and_off_failure_is_explicit(self):
        worker, inst = self.make_worker()
        result = []
        worker.result.connect(lambda connected, identity, message: result.append(message))
        inst.error = '-222,"Bad parameter"'
        self.start_worker(worker)
        self.assertNotIn('OUTPut1 ON', inst.commands)
        self.assertFalse(worker.generator.connected)
        worker, inst = self.make_worker()
        worker.result.connect(lambda connected, identity, message: result.append(message))
        self.start_worker(worker)
        def fail(command):
            raise OSError('USB lost')
        inst.write = fail
        worker.execute('stop_output', None)
        self.assertIn('OUTPUT OFF UNCONFIRMED', result[-1])
        self.assertFalse(worker.generator.connected)

    def test_panel_modes_locking_stop_and_shutdown(self):
        from ultrasound_panel import UltrasoundPanel
        manager = FakeManager()
        panel = UltrasoundPanel(generator=SignalGenerator(lambda: manager))

        def wait_for(predicate):
            deadline = time.monotonic() + 3
            while not predicate() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertTrue(predicate())

        try:
            self.assertIn('10 ms ON / 10 ms OFF', panel.preview.text())
            panel.timing_mode.setCurrentIndex(1)
            self.assertEqual(panel.timing_value.value(), 50)
            panel.timing_mode.setCurrentIndex(0)
            self.assertEqual(panel.timing_value.value(), 50)
            panel.mode.setCurrentIndex(1)
            self.assertEqual(panel.continuous_duration.maximum(), 10)
            self.assertIn('Uninterrupted', panel.preview.text())
            panel.toggle_connection()
            wait_for(lambda: panel.connected)
            self.assertFalse(panel.start_button.isEnabled())
            panel.confirm_button.click()
            wait_for(lambda: panel.start_button.isEnabled())
            panel.start_button.click()
            wait_for(lambda: panel.active and not panel.busy)
            self.assertFalse(panel.frequency.isEnabled())
            self.assertFalse(panel.start_button.isEnabled())
            self.assertTrue(panel.stop_button.isEnabled())
            panel.stop_button.click()
            wait_for(lambda: not panel.active and not panel.busy)
            self.assertEqual(manager.instrument.state['OUTPut1'], '0')
            self.assertFalse(panel.start_button.isEnabled())
            panel.confirm_button.click()
            wait_for(lambda: panel.start_button.isEnabled())
            panel.start_button.click()
            wait_for(lambda: panel.active and not panel.busy)
        finally:
            panel.shutdown()
            wait_for(lambda: not panel.thread.isRunning())
            panel.close()
        self.assertEqual(manager.instrument.state['OUTPut1'], '0')
        self.assertTrue(manager.closed)


if __name__ == '__main__':
    unittest.main()
