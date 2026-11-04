"""Tracking records shared by every source (SI units, xyzw quaternions)."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Body:
    id: int
    position: tuple
    quaternion: tuple
    mean_error_m: float
    valid: bool


@dataclass(frozen=True)
class Frame:
    number: int
    received: float
    markers: tuple
    bodies: tuple
    unlabeled_count: int


@dataclass
class Models:
    names: dict = field(default_factory=dict)
    cameras: list = field(default_factory=list)
