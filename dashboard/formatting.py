from __future__ import annotations

from datetime import datetime

from collector.live_store import live_telemetry_snapshot
from collector.live_health import LiveDeviceState, evaluate_live_health
from shared.protocol import UiFlag, UI_EVENT_MARKER

EVENT_NAMES = {
    UI_EVENT_MARKER: "M",
    2: "Session toggle",
    3: "Mute toggle",
    4: "Measurement reset",
    5: "Diagnostics",
}

SensorStatus = tuple[str, str]

def format_optional_number(value: int | float | None, suffix: str = "", precision: int = 0,) -> str:
    if value is None:
        return "-"

    if isinstance(value, float):
        formatted = f"{value:.{precision}f}"
    else:
        formatted = str(value)

    return f"{formatted}{suffix}"

def format_timestamp(value: datetime | None,) -> str:
    if value is None:
        return "No data received"

    local_time = value.astimezone()

    return local_time.strftime("%d.%m.%Y, %H:%M:%S")

def device_status(snapshot: live_telemetry_snapshot) -> tuple[str, str]:
    health = evaluate_live_health(snapshot)

    if health.device_state is LiveDeviceState.UNKNOWN: 
        return ("NO STATUS", "status-badge status-unknown")

    if health.device_state is LiveDeviceState.ONLINE:
        return ("ONLINE", "status-badge status-online")

    if health.device_state is LiveDeviceState.STALE:
        return ("STALE", "status-badge status-stale")
    
    return ("OFFLINE", "status-badge status-offline")

def session_status(snapshot: live_telemetry_snapshot) -> tuple[str, str]:
    state_message = snapshot.metrics

    if snapshot.events:
        latest_event = snapshot.events[-1]

        if (state_message is None or latest_event.received_at_utc >= state_message.received_at_utc):
            state_message = latest_event

    if state_message is None:
        return ("UNKNOWN", "status-badge status-unknown")

    active = bool(state_message.ui_flags & int(UiFlag.SESSION_ACTIVE))

    if active:
        return ("ACTIVE", "status-badge session-active")

    return ("INACTIVE", "status-badge session-inactive")

def _metrics_sensor_unavailable(snapshot: live_telemetry_snapshot,) -> SensorStatus | None:
    metrics = snapshot.metrics

    if metrics is None:
        return ("NO DATA", "status-badge status-unknown")

    health = evaluate_live_health(snapshot=snapshot)

    if health.device_state is LiveDeviceState.OFFLINE:
        return ("OFFLINE", "status-badge status-offline")

    if health.metrics_stale:
        return ("STALE", "status-badge status-stale")

    return None

def ecg_sensor_status(snapshot: live_telemetry_snapshot) -> SensorStatus:
    unavailable = _metrics_sensor_unavailable(snapshot)

    if unavailable is not None:
        return unavailable

    metrics = snapshot.metrics

    assert metrics is not None

    health = evaluate_live_health(snapshot)

    if health.ecg_stale:
        return ("SIGNAL LOST", "status-badge status-stale")

    if metrics.leads_off:
        return ("LEADS OFF", "status-badge status-warning")

    return ("CONNECTED", "status-badge status-online")

def gsr_sensor_status(snapshot: live_telemetry_snapshot,) -> SensorStatus:
    unavailable = _metrics_sensor_unavailable(snapshot)

    if unavailable is not None:
        return unavailable

    metrics = snapshot.metrics

    assert metrics is not None

    if not metrics.gsr_valid:
        return ("INVALID", "status-badge status-warning")

    return ("VALID", "status-badge status-online")

def pulse_sensor_status(snapshot: live_telemetry_snapshot) -> SensorStatus:
    unavailable = _metrics_sensor_unavailable(snapshot)

    if unavailable is not None:
        return unavailable

    metrics = snapshot.metrics

    assert metrics is not None

    if not metrics.max_finger:
        return ("NO FINGER", "status-badge status-warning")

    if not metrics.spo2_valid:
        return ("ACQUIRING", "status-badge status-warning")

    return ("VALID", "status-badge status-online")

def newest_received_at(snapshot: live_telemetry_snapshot,) -> datetime | None:
    candidates: list[datetime] = []

    if snapshot.status is not None:
        candidates.append(snapshot.status.received_at_utc)

    if snapshot.metrics is not None:
        candidates.append(snapshot.metrics.received_at_utc)

    if snapshot.events:
        candidates.append(snapshot.events[-1].received_at_utc)

    if snapshot.ecg_samples:
        candidates.append(snapshot.ecg_samples[-1].received_at_utc)

    if snapshot.gsr_samples:
        candidates.append(snapshot.gsr_samples[-1].received_at_utc)

    if not candidates:
        return None

    return max(candidates)

def format_event_rows(snapshot: live_telemetry_snapshot, limit: int = 8,) -> list[dict[str, str]]:
    if limit <= 0:
        raise ValueError("Limit must be greater than zero")

    rows: list[dict[str, str]] = []

    for event in reversed(snapshot.events[-limit:]):
        rows.append(
            {
                "time": format_timestamp(event.received_at_utc),
                "event": EVENT_NAMES.get(event.event, f"Event {event.event}"),
                "sample": str(event.sample),
            }
        )

    return rows