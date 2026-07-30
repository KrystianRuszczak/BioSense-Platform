from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias

@dataclass(frozen=True, slots=True)
class status_message:
    received_at_utc: datetime
    device: str
    online: bool

@dataclass(frozen=True, slots=True)
class metrics_message:
    received_at_utc: datetime

    protocol_version: int
    device: str
    sample: int

    ecg_bpm: int
    leads_off: bool

    gsr:int
    gsr_resistance_ohm: float
    gsr_conductance_us: float
    gsr_valid: bool
    gsr_calibration_raw: int
    batt_v: float
    batt_percent: int
    temp_amb: float
    temp_obj: float
    max_bpm: int
    spo2: float
    spo2_valid: bool
    max_finger: bool

    ui_flags: int

@dataclass(frozen=True, slots=True)
class event_message:
    received_at_utc: datetime

    protocol_version: int
    device: str
    sample: int

    event: int
    ui_flags: int

@dataclass(frozen=True, slots=True)
class ecg_batch_message:
    received_at_utc: datetime

    protocol_version: int
    device: str

    sample_start: int
    sample_rate: int

    raw: tuple[int, ...]
    filtered: tuple[int, ...]
    rpeak: tuple[bool, ...]
    leads_off: tuple[bool, ...]

    @property
    def count(self) -> int:
        return len(self.raw)

    @property
    def sample_end(self) -> int:
        return self.sample_start + self.count - 1

@dataclass(frozen=True, slots=True)
class gsr_batch_message:
    received_at_utc: datetime

    protocol_version: int
    device: str

    sample_start: int
    sample_step: int
    sample_rate: int
    calibration_raw: int

    raw: tuple[int, ...]
    resistance_ohm: tuple[float, ...]
    conductance_us: tuple[float, ...]
    valid: tuple[bool, ...]

    @property
    def count(self) -> int:
        return len(self.raw)

    @property
    def sample_end(self) -> int:
        return (self.sample_start + (self.count - 1) * self.sample_step)

telemetry_message: TypeAlias = (
    status_message
    | metrics_message
    | event_message
    | ecg_batch_message
    | gsr_batch_message
)