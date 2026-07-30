from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from shared.models import (
    ecg_batch_message,
    gsr_batch_message,
    event_message,
    metrics_message,
    status_message,
    telemetry_message,
)


@dataclass(frozen=True, slots=True)
class live_ecg_sample:
    received_at_utc: datetime
    sample: int
    sample_rate: int
    raw: int
    filtered: int
    rpeak: bool
    leads_off: bool

@dataclass(frozen=True, slots=True)
class live_gsr_sample:
    received_at_utc: datetime
    sample: int
    sample_step: int
    sample_rate: int
    calibration_raw: int
    raw: int
    resistance_ohm: float
    conductance_us: float
    valid: bool

@dataclass(frozen=True, slots=True)
class live_telemetry_snapshot:
    generated_at_utc: datetime
    device_id: str
    status: status_message | None
    metrics: metrics_message | None
    events: tuple[event_message, ...]
    ecg_samples: tuple[live_ecg_sample, ...]
    gsr_samples: tuple[live_gsr_sample, ...] = ()
    metrics_history: tuple[metrics_message, ...] = ()


class LiveTelemetryStore:
    def __init__(
        self,
        device_id: str,
        ecg_window_s: float = 10.0,
        event_limit: int = 100,
        metrics_window_s: float = 600.0,
        gsr_window_s: float = 60.0, 
    ) -> None:
        if not device_id:
            raise ValueError("device_id must not be empty")

        if ecg_window_s <= 0:
            raise ValueError("ecg_window_S must be greater than zero")

        if event_limit <= 0:
            raise ValueError("event_limit must be greater than zero")

        if metrics_window_s <= 0:
            raise ValueError("metrics_window_s must be greater than zero")

        if gsr_window_s <= 0:
            raise ValueError("gsr_window_s must be greater than zero")

        self._device_id = device_id
        self._ecg_window_s = ecg_window_s
        self._gsr_window_s = gsr_window_s
        self._event_limit = event_limit
        self._metrics_window_s = metrics_window_s
        self._lock = threading.Lock()
        self._status: status_message | None = None
        self._metrics: metrics_message | None = None
        self._metrics_history: deque[metrics_message] = deque()
        self._events: deque[event_message] = deque(maxlen=event_limit)
        self._ecg_samples: deque[live_ecg_sample] = deque()
        self._ecg_sample_rate: int | None = None
        self._gsr_samples: deque[live_gsr_sample] = deque()
        self._gsr_sample_rate: int | None = None
        self._gsr_sample_step: int | None = None
        self._gsr_calibration_raw: int | None = None
        

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def ecg_window_s(self) -> float:
        return self._ecg_window_s

    @property
    def event_limit(self) -> int:
        return self._event_limit

    @property
    def metrics_window_s(self) -> float:
        return self._metrics_window_s

    @property
    def gsr_window_s(self) -> float:
        return self._gsr_window_s

    def update(
        self,
        message: telemetry_message,
    ) -> None:

        if message.device != self._device_id:
            raise ValueError(
                f"Telemetry device does not match the live store: expected={self._device_id}, actual={message.device}"
            )

        with self._lock:
            if isinstance(message, status_message):
                self._status = message
            elif isinstance(message, metrics_message):
                self._append_metrics(message)
            elif isinstance(message, event_message):
                self._events.append(message)
            elif isinstance(message, ecg_batch_message):
                self._append_ecg_batch(message)
            elif isinstance(message, gsr_batch_message):
                self._append_gsr_batch(message)

    def snapshot(self) -> live_telemetry_snapshot:
        with self._lock:
            return live_telemetry_snapshot(
                generated_at_utc=datetime.now(timezone.utc),
                device_id=self._device_id,
                status=self._status,
                metrics=self._metrics,
                events=tuple(self._events),
                ecg_samples=tuple(self._ecg_samples),
                metrics_history=tuple(self._metrics_history),
                gsr_samples=tuple(self._gsr_samples),
            )

    def clear_ecg(self) -> None:
        with self._lock:
            self._ecg_samples.clear()
            self._ecg_sample_rate = None

    def clear_gsr(self) -> None:
        with self._lock:
            self._clear_gsr_unlocked()

    def _clear_gsr_unlocked(self) -> None:
        self._gsr_samples.clear()
        self._gsr_sample_rate = None
        self._gsr_sample_step = None
        self._gsr_calibration_raw = None

    def _append_metrics(self, message: metrics_message) -> None:
        self._metrics = message
        self._metrics_history.append(message)

        cutoff_time = message.received_at_utc - timedelta(seconds=self._metrics_window_s)

        while (self._metrics_history and self._metrics_history[0].received_at_utc < cutoff_time):
            self._metrics_history.popleft()

    def _append_ecg_batch(self, message: ecg_batch_message) -> None:
        if (
            self._ecg_sample_rate is not None
            and self._ecg_sample_rate != message.sample_rate
        ):
            self._ecg_samples.clear()

        self._ecg_sample_rate = message.sample_rate

        for idx in range(message.count):
            self._ecg_samples.append(
                live_ecg_sample(
                    received_at_utc=message.received_at_utc,
                    sample=message.sample_start + idx,
                    sample_rate=message.sample_rate,
                    raw=message.raw[idx],
                    filtered=message.filtered[idx],
                    rpeak=message.rpeak[idx],
                    leads_off=message.leads_off[idx],
                )
            )

        sample_limit = max(
            1,
            math.ceil(self._ecg_window_s * message.sample_rate),
        )

        while len(self._ecg_samples) > sample_limit:
            self._ecg_samples.popleft()

    def _append_gsr_batch(self, message: gsr_batch_message) -> None:
        configuration_changed = (
            (
                self._gsr_sample_rate is not None and self._gsr_sample_rate != message.sample_rate
            )
            or (
                self._gsr_sample_step is not None and self._gsr_sample_step != message.sample_step
            )
            or (
                self._gsr_calibration_raw is not None and self._gsr_calibration_raw != message.calibration_raw
            )
        )

        if configuration_changed:
            self._clear_gsr_unlocked()

        self._gsr_sample_rate = message.sample_rate
        self._gsr_sample_step = message.sample_step
        self._gsr_calibration_raw = message.calibration_raw

        for idx in range(message.count):
            self._gsr_samples.append(
                live_gsr_sample(
                    received_at_utc=message.received_at_utc,
                    sample=message.sample_start + idx * message.sample_step,
                    sample_step=message.sample_step,
                    sample_rate=message.sample_rate,
                    calibration_raw=message.calibration_raw,
                    raw=message.raw[idx],
                    resistance_ohm=message.resistance_ohm[idx],
                    conductance_us=message.conductance_us[idx],
                    valid=message.valid[idx],
                )
            )

        sample_limit = max(
            1,
            math.ceil(self._gsr_window_s * message.sample_rate),
        )

        while len(self._gsr_samples) > sample_limit:
            self._gsr_samples.popleft()
