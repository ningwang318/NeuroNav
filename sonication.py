"""Validated wave patterns and monotonic session timing, independent of Qt/VISA."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SonicationPattern:
    mode: str = 'pulsed'
    burst_ms: float = 10
    timing: str = 'duty'
    timing_value: float = 50
    sonication_s: float = 1
    interval_s: float = 5
    session_s: float = 60
    continuous_s: float = 1

    def validate(self, frequency_khz):
        if self.mode not in ('pulsed', 'continuous'):
            raise ValueError('Select pulsed or continuous sonication.')
        if self.mode == 'continuous':
            if not math.isfinite(self.continuous_s) or not .001 <= self.continuous_s <= 10:
                raise ValueError('Continuous duration must be between 0.001 and 10 seconds.')
            return
        values = (self.burst_ms, self.timing_value, self.sonication_s,
                  self.interval_s, self.session_s)
        if not all(math.isfinite(value) for value in values):
            raise ValueError('Timing values must be finite.')
        if self.burst_ms <= 0 or self.timing_value <= 0:
            raise ValueError('Burst duration and duty cycle / PRF must be positive.')
        if self.timing not in ('duty', 'prf'):
            raise ValueError('Select duty cycle or PRF.')
        if self.timing == 'duty' and self.timing_value >= 100:
            raise ValueError('Pulsed duty cycle must be below 100%; use continuous mode for CW.')
        if self.sonication_s <= 0 or self.session_s <= 0 or self.interval_s < 0:
            raise ValueError('Durations must be positive and interval must be nonnegative.')
        cycles = self.cycles(frequency_khz)
        if not 1 <= cycles <= 100_000_000:
            raise ValueError('Burst duration exceeds the generator cycle-count range.')
        actual_burst = cycles / (frequency_khz * 1000)
        if not 1e-6 <= self.period_s <= 8000 or self.period_s <= actual_burst + 1e-6:
            raise ValueError('Burst off time must exceed 1 µs; check duty cycle / PRF.')
        if actual_burst > min(self.sonication_s, self.session_s):
            raise ValueError('A burst must fit within the sonication and session duration.')

    @property
    def period_s(self):
        return (self.burst_ms / 1000 / (self.timing_value / 100)
                if self.timing == 'duty' else 1 / self.timing_value)

    def cycles(self, frequency_khz):
        seconds = self.continuous_s if self.mode == 'continuous' else self.burst_ms / 1000
        # Never exceed the requested continuous duration through rounding.
        return math.floor(seconds * frequency_khz * 1000) if self.mode == 'continuous' else round(seconds * frequency_khz * 1000)

    @property
    def duration_s(self):
        return self.continuous_s if self.mode == 'continuous' else self.session_s


class SessionClock:
    """No catch-up output: delayed timers stop first, then wait the full off interval."""
    def __init__(self, pattern, now):
        self.pattern = pattern
        self.end = now + pattern.duration_s
        self.phase = 'on'
        self.deadline = min(self.end, now + pattern.sonication_s)

    def advance(self, now):
        if self.phase == 'complete':
            return None
        if now >= self.end:
            self.phase = 'complete'
            return 'complete'
        if self.pattern.mode == 'continuous' or now < self.deadline:
            return None
        if self.phase == 'on':
            self.phase = 'off'
            self.deadline = now + self.pattern.interval_s
            return 'off'
        self.phase = 'on'
        self.deadline = min(self.end, now + self.pattern.sonication_s)
        return 'on'
