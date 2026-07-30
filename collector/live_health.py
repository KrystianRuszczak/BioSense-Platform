from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from collector.live_store import live_telemetry_snapshot

DEVICE_STALE_TIMEOUT_S = 3.0
METRICS_STALE_TIMEOUT_S = 3.0
ECG_STALE_TIMEOUT_S = 1.0

class LiveDeviceState(Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"

@dataclass(frozen=True, slots=True)
class live_health_snapshot:
    device_state: LiveDeviceState
    metrics_stale: bool
    ecg_stale: bool
    latest_received_at: datetime | None
    metrics_age_s: float | None
    ecg_age_s: float | None

def evaluate_live_health(snapshot: live_telemetry_snapshot,) -> live_telemetry_snapshot:
    now = snapshot.generated_at_utc

    status_time = (snapshot.status.received_at_utc if snapshot.status is not None else None)

    metrics_time = (snapshot.metrics.received_at_utc if snapshot.metrics is not None else None)

    event_time = (snapshot.events[-1].received_at_utc if snapshot.events else None)

    ecg_time = (snapshot.ecg_samples[-1].received_at_utc if snapshot.ecg_samples else None)

    telemetry_times = [value for value in (metrics_time, event_time, ecg_time) if value is not None]

    latest_telemetry_time = (max(telemetry_times) if telemetry_times else None)

    all_times = [value for value in (status_time, latest_telemetry_time) if value is not None]

    latest_received_at = (max(all_times) if all_times else None)

    metrics_age_s = (max(0.0, (now - metrics_time).total_seconds()) if metrics_time is not None else None)

    ecg_age_s = (max(0.0, (now - ecg_time).total_seconds()) if ecg_time is not None else None)

    device_age_s = (max(0.0, (now - latest_received_at).total_seconds()) if latest_received_at is not None else None)

    offline_reported = (snapshot.status is not None and not snapshot.status.online and (latest_telemetry_time is None or snapshot.status.received_at_utc >= latest_telemetry_time))

    if offline_reported:
        device_state = LiveDeviceState.OFFLINE
    elif latest_received_at is None:
        device_state = LiveDeviceState.UNKNOWN
    elif (device_age_s is not None and device_age_s > DEVICE_STALE_TIMEOUT_S):
        device_state = LiveDeviceState.STALE
    else:
        device_state = LiveDeviceState.ONLINE

    return live_health_snapshot(
        device_state=device_state, 
        metrics_stale=(metrics_age_s is None or metrics_age_s > METRICS_STALE_TIMEOUT_S), 
        ecg_stale=(ecg_age_s is None or ecg_age_s > ECG_STALE_TIMEOUT_S),
        latest_received_at=latest_received_at,
        metrics_age_s=metrics_age_s,
        ecg_age_s=ecg_age_s
    )