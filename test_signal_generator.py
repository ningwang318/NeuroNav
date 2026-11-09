"""Connection lifecycle tests; fake VISA only, never changes hardware outputs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import unittest

from signal_generator import DEFAULT_RESOURCE, SignalGenerator


class FakeInstrument:
    def __init__(self):
        self.commands = []
        self.closed = False
        self.fail = False
        self.frequency = 250000
        self.voltage = .1
        self.unit = 'VPP'
        self.error = '+0,"No error"'
        self.ignore_frequency = False
        self.state = {'OUTPut1': '0'}

    def write(self, command):
        self.commands.append(command)
        if command == 'TRIGger1':
            return
        name, value = command.split(' ', 1)
        self.state[name] = {'ON': '1', 'OFF': '0', 'TRIGgered': 'TRIG',
                            'IMMediate': 'IMM'}.get(value, value)
        if name == 'SOURce1:FREQuency' and not self.ignore_frequency:
            self.frequency = float(value)
        elif name == 'SOURce1:VOLTage':
            self.voltage = float(value)
        elif name == 'SOURce1:VOLTage:UNIT':
            self.unit = value

    def query(self, command):
        self.commands.append(command)
        if self.fail:
            raise TimeoutError('USB query timed out')
        responses = {'SOURce1:FREQuency?': str(self.frequency),
                     'SOURce1:VOLTage?': str(self.voltage),
                     'SOURce1:VOLTage:UNIT?': self.unit,
                     'SYSTem:ERRor?': self.error}
        if command in responses:
            return responses[command]
        if command[:-1] in self.state:
            return self.state[command[:-1]]
        return 'Agilent Technologies,33522B,MY52402081,1.0\n'

    def close(self):
        self.closed = True


class FakeManager:
    def __init__(self):
        self.instrument = FakeInstrument()
        self.closed = False

    def open_resource(self, address, **kwargs):
        self.address = address
        self.options = kwargs
        return self.instrument

    def close(self):
        self.closed = True


class ConnectionTests(unittest.TestCase):
    def test_apply_frequency_choices_and_voltage_endpoints(self):
        manager = FakeManager()
        generator = SignalGenerator(lambda: manager)
        generator.connect()
        try:
            for frequency in (250, 500, 826):
                for voltage in (100, 555, 990):
                    with self.subTest(frequency=frequency, voltage=voltage):
                        self.assertEqual(generator.apply_settings(frequency, voltage),
                                         (frequency, voltage))
                        self.assertEqual(manager.instrument.frequency, frequency * 1000)
                        self.assertEqual(manager.instrument.voltage, voltage / 1000)
            writes = [command for command in manager.instrument.commands
                      if not command.endswith('?')]
            self.assertTrue(all(command.startswith(('SOURce1:FREQuency ',
                                                    'SOURce1:VOLTage ' ,
                                                    'SOURce1:VOLTage:UNIT ')) for command in writes))
        finally:
            generator.disconnect()

    def test_invalid_settings_send_nothing(self):
        manager = FakeManager()
        generator = SignalGenerator(lambda: manager)
        generator.connect()
        try:
            for frequency, voltage in ((251, 100), (500, 99), (826, 991),
                                       (250, float('nan')), (250, float('inf'))):
                with self.subTest(frequency=frequency, voltage=voltage):
                    with self.assertRaises(ValueError):
                        generator.apply_settings(frequency, voltage)
            self.assertEqual(manager.instrument.commands, ['*IDN?'])
        finally:
            generator.disconnect()

    def test_apply_reports_instrument_errors_and_readback_mismatch(self):
        manager = FakeManager()
        generator = SignalGenerator(lambda: manager)
        generator.connect()
        try:
            manager.instrument.error = '-222,"Data out of range"'
            with self.assertRaisesRegex(RuntimeError, 'Data out of range'):
                generator.apply_settings(500, 100)
            manager.instrument.error = '+0,"No error"'
            manager.instrument.ignore_frequency = True
            with self.assertRaisesRegex(RuntimeError, 'readback'):
                generator.apply_settings(826, 100)
        finally:
            generator.disconnect()

    def test_connect_identify_disconnect_only_queries_identity(self):
        manager = FakeManager()
        generator = SignalGenerator(lambda: manager)
        self.assertIn('MY52402081', generator.connect())
        self.assertEqual(manager.address, DEFAULT_RESOURCE)
        self.assertEqual(manager.options, {'open_timeout': 3000})
        self.assertEqual(manager.instrument.timeout, 3000)
        generator.identify()
        generator.disconnect()
        generator.disconnect()
        self.assertEqual(manager.instrument.commands, ['*IDN?', '*IDN?'])
        self.assertTrue(manager.instrument.closed)
        self.assertTrue(manager.closed)
        self.assertFalse(generator.connected)

    def test_failed_handshake_releases_session_and_can_retry(self):
        manager = FakeManager()
        manager.instrument.fail = True
        generator = SignalGenerator(lambda: manager)
        with self.assertRaises(TimeoutError):
            generator.connect()
        self.assertTrue(manager.instrument.closed)
        self.assertTrue(manager.closed)
        self.assertFalse(generator.connected)
        replacement = FakeManager()
        generator._manager_factory = lambda: replacement
        generator.connect()
        generator.disconnect()
        self.assertTrue(replacement.closed)

    def test_open_failure_closes_manager(self):
        manager = FakeManager()
        def fail(*args, **kwargs):
            raise OSError('Device not present')
        manager.open_resource = fail
        generator = SignalGenerator(lambda: manager)
        with self.assertRaises(OSError):
            generator.connect()
        self.assertTrue(manager.closed)

    def test_bad_identity_and_blank_resource(self):
        manager = FakeManager()
        manager.instrument.query = lambda command: ''
        generator = SignalGenerator(lambda: manager)
        with self.assertRaises(ValueError):
            generator.connect(' ')
        with self.assertRaises(ValueError):
            generator.connect()
        self.assertTrue(manager.instrument.closed)
        self.assertTrue(manager.closed)

    def test_panel_async_connect_failure_reconnect_and_shutdown(self):
        from PySide6 import QtWidgets as Qt
        from ultrasound_panel import UltrasoundPanel
        app = Qt.QApplication.instance() or Qt.QApplication([])
        manager = FakeManager()
        panel = UltrasoundPanel(generator=SignalGenerator(lambda: manager))

        def wait_for(predicate):
            deadline = time.monotonic() + 3
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            self.assertTrue(predicate())

        try:
            self.assertFalse(panel.apply_button.isEnabled())
            self.assertEqual([panel.frequency.itemData(i) for i in range(panel.frequency.count())],
                             [250, 500, 826])
            self.assertEqual((panel.voltage.minimum(), panel.voltage.maximum()), (100, 990))
            panel.toggle_connection()
            self.assertFalse(panel.connect_button.isEnabled())
            wait_for(lambda: panel.connected)
            self.assertFalse(panel.address.isEnabled())
            self.assertTrue(panel.refresh_button.isEnabled())
            panel.frequency.setCurrentIndex(2)
            panel.voltage.setValue(990)
            self.assertEqual(manager.instrument.commands, ['*IDN?'])
            panel.apply_button.click()
            self.assertFalse(panel.apply_button.isEnabled())
            wait_for(lambda: panel.apply_button.isEnabled())
            self.assertIn('826 kHz · 990 mVpp (verified)', panel.status.text())
            self.assertEqual(manager.instrument.frequency, 826000)
            self.assertEqual(manager.instrument.voltage, .990)
            panel.voltage.setValue(100)
            self.assertIn('Selection changed', panel.status.text())
            manager.instrument.fail = True
            panel.run('identify')
            wait_for(lambda: not panel.connected)
            self.assertIn('timed out', panel.status.text())
            self.assertTrue(panel.address.isEnabled())
            manager.instrument.fail = False
            panel.toggle_connection()
            wait_for(lambda: panel.connected)
        finally:
            panel.shutdown()
            wait_for(lambda: not panel.thread.isRunning())
            panel.close()
        self.assertTrue(manager.closed)


if __name__ == '__main__':
    unittest.main()
