from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

class LiveSessionState(Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    FINALIZING = "finalizing"
    
@dataclass(frozen=True, slots=True)
class live_session_snapshot:
    generated_at_utc: datetime
    state: LiveSessionState
    started_at_utc: datetime | None
    stopped_at_utc: datetime | None
    last_duration_s: float | None
    last_integrity_complete: bool | None
    
class LiveSessionStore:
    def __init__(self):
        self._lock = threading.Lock()
        
        self._state = LiveSessionState.INACTIVE
        self._started_at_utc: datetime | None = None
        self._stopped_at_utc: datetime | None = None
        
        self._last_duration_s: float | None = None
        self._last_integrity_complete: bool | None = None
        
    def snapshot(self) -> live_session_snapshot:
        with self._lock:
            return live_session_snapshot(
                generated_at_utc=datetime.now(timezone.utc),
                state=self._state,
                started_at_utc=self._started_at_utc,
                stopped_at_utc=self._stopped_at_utc,
                last_duration_s=self._last_duration_s,
                last_integrity_complete=self._last_integrity_complete,
            )
            
    def start(self, started_at_utc: datetime) -> None:
        started_at = started_at_utc.astimezone(timezone.utc)
        
        with self._lock:
            self._state = LiveSessionState.ACTIVE
            self._started_at_utc = started_at
            self._stopped_at_utc = None
            
    def begin_finalizing(self, stopped_at_utc: datetime) -> None:
        stopped_at = stopped_at_utc.astimezone(timezone.utc)
        
        with self._lock:
            self._state = LiveSessionState.FINALIZING
            self._stopped_at_utc = stopped_at
            
    def finish(self, *, integrity_complete: bool, finished_at_utc: datetime | None = None) -> None:
        finished_at = finished_at_utc.astimezone(timezone.utc) if finished_at_utc is not None else datetime.now(timezone.utc)
        
        with self._lock:
            if self._started_at_utc is not None:
                duration_end = self._stopped_at_utc if self._stopped_at_utc is not None else finished_at
                
                self._last_duration_s = max(0.0, (duration_end - self._started_at_utc).total_seconds())
                
            self._last_integrity_complete = integrity_complete
            
            self._state = LiveSessionState.INACTIVE