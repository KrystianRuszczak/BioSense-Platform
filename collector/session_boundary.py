from __future__ import annotations
from dataclasses import dataclass

from shared.models import ecg_batch_message, gsr_batch_message

@dataclass(frozen=True, slots=True)
class session_boundary_stats:
    start_sample: int | None
    start_transition_observed: bool
    stop_sample: int | None

    first_ecg_sample: int | None
    last_ecg_sample: int | None

    first_batch_count: int | None
    last_batch_count: int | None

    start_offset_samples: int | None
    stop_offset_samples: int | None

    start_boundary_complete: bool
    stop_boundary_complete: bool

    @property
    def is_complete(self) -> bool:
        return (self.start_boundary_complete and self.stop_boundary_complete)

class SessionBoundaryTracker:
    def __init__(self, start_sample: int | None, start_transition_observed: bool) -> None:
        if (start_sample is not None and start_sample < 0):
            raise ValueError("start_sample must not be negative")

        self._start_sample = start_sample
        self._start_transition_observed = start_transition_observed

        self._stop_sample: int | None = None
        self._first_ecg_sample: int | None = None
        self._last_ecg_sample: int | None = None
        self._first_batch_count: int | None = None
        self._last_batch_count: int | None = None

    @property
    def stats(self) -> session_boundary_stats:
        start_offset = self._start_offset()
        stop_offset = self._stop_offset()

        first_batch_end = (None if (self._first_ecg_sample is None or self._first_batch_count is None) else (self._first_ecg_sample + self._first_batch_count - 1))

        last_batch_start = (None if (self._last_ecg_sample is None or self._last_batch_count is None) else (self._last_ecg_sample - self._last_batch_count + 1))

        start_complete = (
            self._start_transition_observed 
            and self._start_sample is not None 
            and self._first_ecg_sample is not None 
            and first_batch_end is not None
            and (self._first_ecg_sample <= self._start_sample <= first_batch_end)
        )

        stop_complete = (
            self._stop_sample is not None 
            and self._last_ecg_sample is not None 
            and last_batch_start is not None
            and (last_batch_start <= self._stop_sample <= self._last_ecg_sample)
        )

        return session_boundary_stats(
            start_sample=self._start_sample,
            start_transition_observed=self._start_transition_observed,
            stop_sample=self._stop_sample,
            first_ecg_sample=self._first_ecg_sample,
            last_ecg_sample=self._last_ecg_sample,
            first_batch_count=self._first_batch_count,
            last_batch_count=self._last_batch_count,
            start_offset_samples=start_offset,
            stop_offset_samples=stop_offset,
            start_boundary_complete=start_complete,
            stop_boundary_complete=stop_complete
        )

    def observe(self, message: ecg_batch_message,) -> None:
        if (self._first_ecg_sample is None or message.sample_start < self._first_ecg_sample):
            self._first_ecg_sample = message.sample_start
            self._first_batch_count = message.count

        if (self._last_ecg_sample is None or message.sample_end > self._last_ecg_sample):
            self._last_ecg_sample = message.sample_end
            self._last_batch_count = message.count

    def set_stop_sample(self, stop_sample: int | None) -> None:
        if (stop_sample is not None and stop_sample < 0):
            raise ValueError("stop_sample must not be negative")

        self._stop_sample = stop_sample

    def _start_offset(self) -> int | None:
        if (self._start_sample is None or self._first_ecg_sample is None):
            return None

        return self._first_ecg_sample - self._start_sample

    def _stop_offset(self) -> int | None:
        if (self._stop_sample is None or self._last_ecg_sample is None):
            return None

        return self._stop_sample - self._last_ecg_sample

@dataclass(frozen=True, slots=True)
class gsr_session_boundary_stats:
    start_sample: int | None
    start_transition_observed: bool
    stop_sample: int | None

    first_gsr_sample: int | None
    last_gsr_sample: int | None

    first_sample_step: int | None
    last_sample_step: int | None

    expected_first_gsr_sample: int | None
    expected_last_gsr_sample: int | None

    start_offset_samples: int | None
    stop_offset_samples: int | None

    start_boundary_complete: bool
    stop_boundary_complete: bool

    @property
    def is_complete(self) -> bool:
        return self.start_boundary_complete and self.stop_boundary_complete

class GsrSessionBoundaryTracker:
    def __init__(self, start_sample: int | None, start_transition_observed: bool,) -> None:
        if (start_sample is not None and start_sample < 0):
            raise ValueError("sample_start must not be negative")

        self._start_sample = start_sample
        self._start_transition_observed = start_transition_observed

        self._stop_sample: int | None = None
        self._first_gsr_sample: int | None = None
        self._last_gsr_sample: int | None = None
        self._first_sample_step: int | None = None
        self._last_sample_step: int | None = None

    @property
    def stats(self) -> gsr_session_boundary_stats:
        expected_first = self._expected_first_gsr_sample()
        expected_last = self._expected_last_gsr_sample()

        start_offset = (None if (expected_first is None or self._first_gsr_sample is None) else (self._first_gsr_sample - expected_first))
        stop_offset = (None if (expected_last is None or self._last_gsr_sample is None) else (expected_last - self._last_gsr_sample))

        start_complete = (
            self._start_transition_observed and expected_first is not None and self._first_gsr_sample is not None and self._last_gsr_sample is not None
            and (self._first_gsr_sample <= expected_first <= self._last_gsr_sample)
        )

        stop_complete = (
            expected_last is not None and self._first_gsr_sample is not None and self._last_gsr_sample is not None
            and (self._first_gsr_sample <= expected_last <= self._last_gsr_sample)
        )

        return gsr_session_boundary_stats(
            start_sample=self._start_sample,
            start_transition_observed=self._start_transition_observed,
            stop_sample=self._stop_sample,
            first_gsr_sample=self._first_gsr_sample,
            last_gsr_sample=self._last_gsr_sample,
            first_sample_step=self._first_sample_step,
            last_sample_step=self._last_sample_step,
            expected_first_gsr_sample=expected_first,
            expected_last_gsr_sample=expected_last,
            start_offset_samples=start_offset,
            stop_offset_samples=stop_offset,
            start_boundary_complete=start_complete,
            stop_boundary_complete=stop_complete
        )

    def observe(self, message: gsr_batch_message,) -> None:
        if (self._first_gsr_sample is None or message.sample_start < self._first_gsr_sample):
            self._first_gsr_sample = message.sample_start
            self._first_sample_step = message.sample_step

        if (self._last_gsr_sample is None or message.sample_end > self._last_gsr_sample):
            self._last_gsr_sample = message.sample_end
            self._last_sample_step = message.sample_step

    def set_stop_sample(self, stop_sample: int | None) -> None:
        if (stop_sample is not None and stop_sample < 0):
            raise ValueError("stop_sample must not be negative")

        self._stop_sample = stop_sample

    def _expected_last_gsr_sample(self,) -> int | None:
        if (self._stop_sample is None or self._last_gsr_sample is None or self._last_sample_step is None):
            return None
        
        return self._stop_sample - (self._stop_sample - self._last_gsr_sample) % self._last_sample_step

    def _expected_first_gsr_sample(self,) -> int | None:
        if (self._start_sample is None or self._first_gsr_sample is None or self._first_sample_step is None):
            return None

        return self._start_sample + (self._first_gsr_sample - self._start_sample) % self._first_sample_step