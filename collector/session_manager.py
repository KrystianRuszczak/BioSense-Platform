from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from shared.models import event_message, metrics_message, telemetry_message
from shared.protocol import UiFlag

class SessionAction(Enum):
    NONE = "none"
    STARTED = "started"
    STOPPED = "stopped"

@dataclass(frozen=True, slots=True)
class SessionInfo:
    session_id: str
    device_id: str
    started_at_utc: datetime
    directory: Path
    start_sample: int | None = None
    start_transition_observed: bool = False

@dataclass(frozen=True, slots=True)
class SessionUpdate:
    action: SessionAction
    session: SessionInfo | None
    transition_sample: int | None = None

class SessionManager:
    def __init__(self, data_root: Path, device_id: str) -> None:
        if not device_id: raise ValueError("device_id cannot be empty")

        if not all(character.isalnum() or character in "_-" for character in device_id):
            raise ValueError("device_id may contain only letters, digits, underscores and hyphens")

        self._device_id = device_id
        self._session_root = data_root / "sessions"
        self._active_session: SessionInfo | None = None
        self._state_initialized = False
        self._last_session_active = False

    @property
    def active_session(self,) -> SessionInfo | None:
        return self._active_session

    @property
    def is_session_active(self) -> bool: 
        return self._active_session is not None

    def process(self, message: telemetry_message) -> SessionUpdate:
        state = self._extract_session_state(message)

        if state is None: return SessionUpdate(action=SessionAction.NONE, session=self._active_session)

        ui_flags, sample = state

        session_active = bool(ui_flags & UiFlag.SESSION_ACTIVE)

        if not self._state_initialized:
            self._state_initialized = True
            self._last_session_active = session_active

            if session_active: 
                return self._start_session(started_at_utc=message.received_at_utc, start_sample=sample, start_transition_observed=False)

            return SessionUpdate(action=SessionAction.NONE, session=None)

        if (session_active == self._last_session_active):
            return SessionUpdate(action=SessionAction.NONE, session=self._active_session)

        self._last_session_active = session_active

        if session_active: return self._start_session(started_at_utc=message.received_at_utc, start_sample=sample, start_transition_observed=True)

        return self._stop_session(stop_sample=sample)

    @staticmethod
    def _extract_session_state(message: telemetry_message) -> tuple[UiFlag, int] | None:
        if isinstance(message, (metrics_message, event_message)):
            return (UiFlag(message.ui_flags), message.sample)
        return None

    def _start_session(self, started_at_utc: datetime, start_sample: int, start_transition_observed: bool) -> SessionUpdate:
        if self._active_session is not None:
            return SessionUpdate(action=SessionAction.NONE, session=self._active_session,)

        started_at = started_at_utc.astimezone(timezone.utc)

        timestamp = started_at.strftime("%Y%m%dT%H%M%S.%fZ")

        session_id = f"{timestamp}_{self._device_id}"

        session = SessionInfo(session_id=session_id, device_id=self._device_id, started_at_utc=started_at, directory=(self._session_root / session_id), start_sample=start_sample, start_transition_observed=start_transition_observed)

        self._active_session = session

        return SessionUpdate(action=SessionAction.STARTED, session=session, transition_sample=start_sample)

    def _stop_session(self, stop_sample: int) -> SessionUpdate:
        stopped_session = self._active_session
        self._active_session = None

        if stopped_session is None: return SessionUpdate(action=SessionAction.NONE, session=None)

        return SessionUpdate(action=SessionAction.STOPPED, session=stopped_session, transition_sample=stop_sample)