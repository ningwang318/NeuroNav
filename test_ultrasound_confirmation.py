"""Amplifier display, confirmation interlock and messages using fake hardware."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import unittest

from PySide6 import QtWidgets as Qt
from signal_generator import SignalGenerator
from ultrasound_panel import UltrasoundPanel, GeneratorWorker
from sonication import SonicationPattern
from test_signal_generator import FakeManager


class ConfirmationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Qt.QApplication.instance() or Qt.QApplication([])

    def setUp(self):
        self.manager = FakeManager()
        self.panel = UltrasoundPanel(generator=SignalGenerator(lambda: self.manager))

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    def tearDown(self):
        self.panel.shutdown()
        self.wait_for(lambda: not self.panel.thread.isRunning())
        self.panel.close()

    def connect(self):
        self.panel.toggle_connection()
        self.wait_for(lambda: self.panel.connected and not self.panel.busy)

    def confirm(self):
        self.panel.confirm_button.click()
        self.wait_for(lambda: self.panel.start_button.isEnabled())

    def test_gain_calculation_is_millivolts_to_volts(self):
        self.assertEqual(self.panel.amplifier.currentData(), 40)
        self.assertEqual(self.panel.amplified_voltage.text(), '4 Vpp (calculated, 40×)')
        self.panel.voltage.setValue(990)
        self.assertEqual(self.panel.amplified_voltage.text(), '39.6 Vpp (calculated, 40×)')
        self.panel.amplifier.setCurrentIndex(1)
        self.assertEqual(self.panel.amplified_voltage.text(), '0.99 Vpp (calculated, 1×)')

    def test_start_requires_confirmation_and_confirm_does_not_enable_output(self):
        self.connect()
        self.panel.run('start')
        self.assertNotIn('OUTPut1 ON', self.manager.instrument.commands)
        self.assertIn('Please connect and confirm', self.panel.status.text())
        self.confirm()
        self.assertEqual(self.manager.instrument.state['OUTPut1'], '0')
        self.assertNotIn('OUTPut1 ON', self.manager.instrument.commands)
        self.assertIn('confirmed', self.panel.status.text())
        self.assertFalse(hasattr(self.panel, 'messages'))
        self.panel.start_button.click()
        self.wait_for(lambda: self.panel.active and not self.panel.busy)
        self.panel.stop_button.click()
        self.wait_for(lambda: not self.panel.active and not self.panel.busy)
        self.assertFalse(self.panel.start_button.isEnabled())

    def test_every_input_change_invalidates_confirmation(self):
        self.connect()
        changes = [lambda: self.panel.frequency.setCurrentIndex(1),
                   lambda: self.panel.voltage.setValue(150),
                   lambda: self.panel.amplifier.setCurrentIndex(1),
                   lambda: self.panel.burst.setValue(11),
                   lambda: self.panel.timing_mode.setCurrentIndex(1),
                   lambda: self.panel.timing_value.setValue(40),
                   lambda: self.panel.sonication_duration.setValue(2),
                   lambda: self.panel.sonication_interval.setValue(6),
                   lambda: self.panel.session_duration.setValue(90),
                   lambda: self.panel.mode.setCurrentIndex(1),
                   lambda: self.panel.continuous_duration.setValue(2)]
        for change in changes:
            self.confirm()
            change()
            self.assertFalse(self.panel.start_button.isEnabled())
            self.wait_for(lambda: self.panel.worker.confirmed is None)
        self.assertIn('Settings changed', self.panel.status.text())

    def test_confirmation_error_is_logged_and_start_stays_locked(self):
        self.connect()
        self.manager.instrument.error = '-222,"Bad parameter"'
        self.panel.confirm_button.click()
        self.wait_for(lambda: not self.panel.busy)
        self.assertFalse(self.panel.start_button.isEnabled())
        self.assertIn('Confirm failed', self.panel.status.text())
        self.assertNotIn('OUTPut1 ON', self.manager.instrument.commands)

    def test_worker_refuses_unconfirmed_or_changed_payload(self):
        for confirmed, changed_gain in ((False, False), (True, True)):
            manager = FakeManager()
            worker = GeneratorWorker(SignalGenerator(lambda: manager))
            worker.execute('connect', 'fake')
            payload = (250, 100, SonicationPattern(), 40)
            if confirmed:
                worker.execute('confirm', payload)
            worker.execute('start', (*payload[:3], 1) if changed_gain else payload)
            self.assertNotIn('OUTPut1 ON', manager.instrument.commands)
            worker.stop()


if __name__ == '__main__':
    unittest.main()
