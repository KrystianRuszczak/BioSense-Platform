from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from shared.models import ecg_batch_message


class EcgContinuityStatus(Enum):
    FIRST_BATCH = auto()
    CONTIGUOUS = auto()
    GAP = auto()
    NON_MONOTONIC = auto()


@dataclass(frozen=True, slots=True)
class ecg_continuity_res:
    status: EcgContinuityStatus
    expected_sample_start: int | None
    actual_sample_start: int
    missing_samples: int = 0


@dataclass(frozen=True, slots=True)
class ecg_continuity_stats:
    received_batches: int
    received_samples: int
    gap_count: int
    missing_samples: int
    non_monotonic_batches: int
    first_sample_start: int | None
    last_sample_end: int | None
    next_expected_sample_start: int | None

    @property
    def is_complete(self) -> bool:
        return self.missing_samples == 0 and self.non_monotonic_batches == 0


class EcgContinuityTracker:
    def __init__(self) -> None:
        self._received_batches = 0
        self._received_samples = 0
        self._gap_count = 0
        self._missing_samples = 0
        self._non_monotonic_batches = 0
        self._first_sample_start: int | None = None
        self._last_sample_end: int | None = None
        self._next_expected_sample_start: int | None = None

    @property
    def stats(self) -> ecg_continuity_stats:
        return ecg_continuity_stats(
            received_batches=self._received_batches,
            received_samples=self._received_samples,
            gap_count=self._gap_count,
            missing_samples=self._missing_samples,
            non_monotonic_batches=self._non_monotonic_batches,
            first_sample_start=self._first_sample_start,
            last_sample_end=self._last_sample_end,
            next_expected_sample_start=self._next_expected_sample_start,
        )

    def observe(
        self,
        message: ecg_batch_message,
    ) -> ecg_continuity_res:
        self._received_batches += 1
        self._received_samples += message.count

        expected = self._next_expected_sample_start
        actual = message.sample_start

        if expected is None:
            self._first_sample_start = actual
            self._last_sample_end = message.sample_end
            self._next_expected_sample_start = message.sample_end + 1

            return ecg_continuity_res(
                status=EcgContinuityStatus.FIRST_BATCH,
                expected_sample_start=None,
                actual_sample_start=actual,
            )

        if actual == expected:
            self._last_sample_end = message.sample_end
            self._next_expected_sample_start = message.sample_end + 1

            return ecg_continuity_res(
                status=EcgContinuityStatus.CONTIGUOUS,
                expected_sample_start=expected,
                actual_sample_start=actual,
            )

        if actual > expected:
            missing_samples = actual - expected

            self._gap_count += 1
            self._missing_samples += missing_samples
            self._last_sample_end = message.sample_end
            self._next_expected_sample_start = message.sample_end + 1

            return ecg_continuity_res(
                status=EcgContinuityStatus.GAP,
                expected_sample_start=expected,
                actual_sample_start=actual,
                missing_samples=missing_samples,
            )

        self._non_monotonic_batches += 1

        if self._last_sample_end is None or message.sample_end > self._last_sample_end:
            self._last_sample_end = message.sample_end

        if message.sample_end >= expected:
            self._next_expected_sample_start = message.sample_end + 1

        return ecg_continuity_res(
            status=EcgContinuityStatus.NON_MONOTONIC,
            expected_sample_start=expected,
            actual_sample_start=actual,
        )
