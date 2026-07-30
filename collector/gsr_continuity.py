from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from shared.models import gsr_batch_message


class GsrContinuityStatus(Enum):
    FIRST_BATCH = auto()
    CONTIGUOUS = auto()
    GAP = auto()
    NON_MONOTONIC = auto()
    MISALIGNED = auto()
    CONFIGURATION_CHANGED = auto()


@dataclass(frozen=True, slots=True)
class gsr_continuity_res:
    status: GsrContinuityStatus
    expected_sample_start: int | None
    actual_sample_start: int
    missing_samples: int = 0
    previous_sample_step: int | None = None
    previous_sample_rate: int | None = None


@dataclass(frozen=True, slots=True)
class gsr_continuity_stats:
    received_batches: int
    received_samples: int
    gap_count: int
    missing_samples: int
    non_monotonic_batches: int
    misaligned_batches: int
    configuration_changes: int
    first_sample_start: int | None
    last_sample_end: int | None
    next_expected_sample_start: int | None
    sample_step: int | None
    sample_rate: int | None

    @property
    def is_complete(self) -> bool:
        return self.missing_samples == 0 and self.non_monotonic_batches == 0 and self.misaligned_batches == 0 and self.configuration_changes == 0


class GsrContinuityTracker:
    def __init__(self) -> None:
        self._received_batches = 0
        self._received_samples = 0
        self._gap_count = 0
        self._missing_samples = 0
        self._non_monotonic_batches = 0
        self._misaligned_batches = 0
        self._configuration_changes = 0
        self._first_sample_start: int | None = None
        self._last_sample_end: int | None = None
        self._next_expected_sample_start: int | None = None
        self._sample_step: int | None = None
        self._sample_rate: int | None = None

    @property
    def stats(self) -> gsr_continuity_stats:
        return gsr_continuity_stats(
            received_batches=self._received_batches,
            received_samples=self._received_samples,
            gap_count=self._gap_count,
            missing_samples=self._missing_samples,
            non_monotonic_batches=self._non_monotonic_batches,
            misaligned_batches=self._misaligned_batches,
            configuration_changes=self._configuration_changes,
            first_sample_start=self._first_sample_start,
            last_sample_end=self._last_sample_end,
            next_expected_sample_start=self._next_expected_sample_start,
            sample_step=self._sample_step,
            sample_rate=self._sample_rate,
        )

    def observe(
        self,
        message: gsr_batch_message,
    ) -> gsr_continuity_res:
        self._received_batches += 1
        self._received_samples += message.count

        expected = self._next_expected_sample_start
        actual = message.sample_start

        if expected is None:
            self._first_sample_start = actual
            self._accept_batch(message)

            return gsr_continuity_res(
                status=GsrContinuityStatus.FIRST_BATCH,
                expected_sample_start=None,
                actual_sample_start=actual,
            )

        self._configuration_changed = (self._sample_step != message.sample_step or self._sample_rate != message.sample_rate)

        if self._configuration_changed:
            previous_sample_step = self._sample_step
            previous_sample_rate = self._sample_rate

            self._configuration_changes += 1
            self._accept_batch(message)

            return gsr_continuity_res(
                status=GsrContinuityStatus.CONFIGURATION_CHANGED,
                expected_sample_start=expected,
                actual_sample_start=actual,
                previous_sample_step=previous_sample_step,
                previous_sample_rate=previous_sample_rate,
            )

        if actual == expected:
            self._accept_batch(message)

            return gsr_continuity_res(
                status=GsrContinuityStatus.CONTIGUOUS,
                expected_sample_start=expected,
                actual_sample_start=actual,
            )

        if actual > expected:
            difference = actual - expected

            if difference % message.sample_step != 0:
                self._misaligned_batches += 1
                self._accept_batch(message)

                return gsr_continuity_res(
                    status=GsrContinuityStatus.MISALIGNED,
                    expected_sample_start=expected,
                    actual_sample_start=actual,
                )

            missing_samples = difference // message.sample_step

            self._gap_count += 1
            self._missing_samples += missing_samples
            self._accept_batch(message)

            return gsr_continuity_res(
                status=GsrContinuityStatus.GAP,
                expected_sample_start=expected,
                actual_sample_start=actual,
                missing_samples=missing_samples,
            )

        self._non_monotonic_batches += 1

        if self._last_sample_end is None or message.sample_end > self._last_sample_end:
            self._last_sample_end = message.sample_end

        if message.sample_end >= expected:
            self._next_expected_sample_start = message.sample_end + message.sample_step

        return gsr_continuity_res(
            status=GsrContinuityStatus.NON_MONOTONIC,
            expected_sample_start=expected,
            actual_sample_start=actual,
        )

    def _accept_batch(self, message:gsr_batch_message) -> None:
        self._sample_step = message.sample_step
        self._sample_rate = message.sample_rate
        self._last_sample_end = message.sample_end
        self._next_expected_sample_start = message.sample_end + message.sample_step
