"""VISA connection and channel-one frequency/amplitude settings."""
import math

DEFAULT_RESOURCE = 'USB0::0x0957::0x2807::MY52402081::0::INSTR'
FREQUENCIES_KHZ = (250, 500, 826)


class SignalGenerator:
    def __init__(self, manager_factory=None):
        self._manager_factory = manager_factory
        self.manager = None
        self.instrument = None
        self.identity = ''
        self.session_owned = False

    @property
    def connected(self):
        return self.instrument is not None

    def connect(self, address=DEFAULT_RESOURCE):
        address = address.strip()
        if not address:
            raise ValueError('Enter a VISA resource address.')
        if self.connected:
            raise RuntimeError('Disconnect before opening another instrument.')
        factory = self._manager_factory
        if factory is None:
            try:
                import pyvisa
            except ImportError as exc:
                raise RuntimeError('PyVISA is missing. Install requirements.txt in the NeuroNav runtime.') from exc
            factory = pyvisa.ResourceManager
        try:
            self.manager = factory()
            self.instrument = self.manager.open_resource(address, open_timeout=3000)
            self.instrument.timeout = 3000
            self.instrument.write_termination = '\n'
            self.instrument.read_termination = '\n'
            return self.identify()
        except Exception:
            # Preserve the original connection failure if cleanup also fails.
            try:
                self.disconnect()
            except Exception:
                pass
            raise

    def identify(self):
        if not self.connected:
            raise RuntimeError('Signal generator is not connected.')
        identity = self.instrument.query('*IDN?').strip()
        if len(identity.split(',')) < 4 or not all(identity.split(',')[:3]):
            raise ValueError('The instrument returned an invalid *IDN? response.')
        self.identity = identity
        return identity

    def disconnect(self):
        try:
            if self.session_owned and self.connected:
                self.stop_output()
        finally:
            self._close_session()

    def _close_session(self):
        instrument, manager = self.instrument, self.manager
        self.instrument = self.manager = None
        self.identity = ''
        self.session_owned = False
        try:
            if instrument is not None:
                instrument.close()
        finally:
            if manager is not None:
                manager.close()

    def apply_settings(self, frequency_khz, voltage_mv):
        """Set CH1 frequency and amplitude in mVpp, then verify readback.

        Keeps the existing waveform, load, offset, burst and output state.
        A communication failure can leave settings partially applied.
        """
        if frequency_khz not in FREQUENCIES_KHZ:
            raise ValueError('Frequency must be 250, 500 or 826 kHz.')
        if not math.isfinite(voltage_mv) or not 100 <= voltage_mv <= 990:
            raise ValueError('Voltage must be between 100 and 990 mVpp.')
        if not self.connected:
            raise RuntimeError('Signal generator is not connected.')
        instrument = self.instrument
        instrument.write(f'SOURce1:FREQuency {frequency_khz * 1000:g}')
        instrument.write('SOURce1:VOLTage:UNIT VPP')
        instrument.write(f'SOURce1:VOLTage {voltage_mv / 1000:.9g}')
        frequency_hz = float(instrument.query('SOURce1:FREQuency?'))
        voltage_v = float(instrument.query('SOURce1:VOLTage?'))
        unit = instrument.query('SOURce1:VOLTage:UNIT?').strip().upper()
        error = instrument.query('SYSTem:ERRor?').strip()
        if error.split(',')[0].strip() not in ('0', '+0'):
            raise RuntimeError(f'Generator reported {error}')
        if (unit != 'VPP'
                or not math.isclose(frequency_hz, frequency_khz * 1000, rel_tol=1e-6)
                or not math.isclose(voltage_v, voltage_mv / 1000, rel_tol=1e-5, abs_tol=1e-6)):
            raise RuntimeError('Generator readback does not match the requested settings.')
        return frequency_hz / 1000, voltage_v * 1000

    def prepare_sonication(self, frequency_khz, voltage_mv, pattern):
        pattern.validate(frequency_khz)
        if not self.connected:
            raise RuntimeError('Signal generator is not connected.')
        # From this point shutdown must attempt output-off even if setup fails.
        self.session_owned = True
        self.stop_output()
        self.session_owned = True
        self.apply_settings(frequency_khz, voltage_mv)
        inst = self.instrument
        for command in ('SOURce1:BURSt:STATe OFF', 'SOURce1:SWEep:STATe OFF',
                        'SOURce1:AM:STATe OFF', 'SOURce1:FM:STATe OFF',
                        'SOURce1:PM:STATe OFF', 'SOURce1:FSKey:STATe OFF',
                        'SOURce1:SUM:STATe OFF', 'SOURce1:FUNCtion SIN',
                        'SOURce1:VOLTage:OFFSet 0', 'SOURce1:BURSt:MODE TRIGgered',
                        'SOURce1:BURSt:PHASe 0', 'TRIGger1:DELay 0'):
            inst.write(command)
        cycles = pattern.cycles(frequency_khz)
        inst.write(f'SOURce1:BURSt:NCYCles {cycles}')
        if pattern.mode == 'pulsed':
            inst.write(f'SOURce1:BURSt:INTernal:PERiod {pattern.period_s:.12g}')
        # CW is one hardware-limited, uninterrupted sine packet. No repeated gaps.
        source = 'BUS' if pattern.mode == 'continuous' else 'IMMediate'
        inst.write(f'TRIGger1:SOURce {source}')
        inst.write('SOURce1:BURSt:STATe ON')
        checks = {'SOURce1:FUNCtion?': 'SIN', 'SOURce1:BURSt:STATe?': '1',
                  'SOURce1:BURSt:MODE?': 'TRIG',
                  'TRIGger1:SOURce?': 'BUS' if pattern.mode == 'continuous' else 'IMM'}
        for command, expected in checks.items():
            if inst.query(command).strip().upper() != expected:
                raise RuntimeError(f'Wave-pattern readback mismatch: {command}')
        if int(float(inst.query('SOURce1:BURSt:NCYCles?'))) != cycles:
            raise RuntimeError('Burst cycle-count readback mismatch.')
        if pattern.mode == 'pulsed' and not math.isclose(
                float(inst.query('SOURce1:BURSt:INTernal:PERiod?')), pattern.period_s, rel_tol=1e-6):
            raise RuntimeError('Burst period readback mismatch.')
        if float(inst.query('SOURce1:VOLTage:OFFSet?')) != 0:
            raise RuntimeError('DC offset readback is not zero.')
        error = inst.query('SYSTem:ERRor?').strip()
        if error.split(',')[0].strip() not in ('0', '+0'):
            raise RuntimeError(f'Generator reported {error}')

    def enable_sonication(self, continuous=False):
        if not self.connected:
            raise RuntimeError('Signal generator is not connected.')
        self.session_owned = True
        self.instrument.write('OUTPut1 ON')
        if continuous:
            self.instrument.write('TRIGger1')

    def pause_output(self):
        self.instrument.write('OUTPut1 OFF')

    def stop_output(self):
        if not self.connected:
            raise RuntimeError('Signal generator is not connected; output-off is unconfirmed.')
        self.session_owned = True
        self.instrument.write('OUTPut1 OFF')
        if self.instrument.query('OUTPut1?').strip() != '0':
            raise RuntimeError('Generator output-off could not be verified.')
        self.session_owned = False
