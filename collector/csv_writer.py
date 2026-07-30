from __future__ import annotations

import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from collector.ecg_continuity import (
    EcgContinuityStatus,
    EcgContinuityTracker,
    ecg_continuity_stats,
)
from collector.gsr_continuity import GsrContinuityStatus, GsrContinuityTracker, gsr_continuity_stats
from collector.session_manager import SessionInfo
from collector.session_boundary import SessionBoundaryTracker, session_boundary_stats, GsrSessionBoundaryTracker, gsr_session_boundary_stats
from shared.models import (
    ecg_batch_message,
    gsr_batch_message,
    event_message,
    metrics_message,
    telemetry_message,
)

LOGGER = logging.getLogger(__name__)
SESSION_FORMAT_VERSION = 3

METRICS_FIELDS = [
    "received_at_utc",
    "protocol_version",
    "device",
    "sample",
    "ecg_bpm",
    "leads_off",
    "gsr",
    "gsr_resistance_ohm",
    "gsr_conductance_us",
    "gsr_valid",
    "gsr_calibration_raw",
    "batt_v",
    "batt_percent",
    "temp_amb",
    "temp_obj",
    "max_bpm",
    "spo2",
    "spo2_valid",
    "max_finger",
    "ui_flags",
]

EVENT_FIELDS = [
    "received_at_utc",
    "protocol_version",
    "device",
    "sample",
    "event",
    "ui_flags",
]

ECG_FIELDS = [
    "received_at_utc",
    "protocol_version",
    "device",
    "sample",
    "sample_rate",
    "device_time_s",
    "raw",
    "filtered",
    "rpeak",
    "leads_off",
]

GSR_FIELDS = [
    "received_at_utc",
    "protocol_version",
    "device",
    "sample",
    "sample_step",
    "sample_rate",
    "device_time_s",
    "calibration_raw",
    "raw",
    "resistance_ohm",
    "conductance_us",
    "valid",
]

def format_utc_timestamp(value: datetime) -> str:
    timestamp = value.astimezone(timezone.utc)

    return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SessionCsvWriter:
    def __init__(
        self,
        session: SessionInfo,
        flush_interval_s: float = 1.0,
    ) -> None:
        if flush_interval_s <= 0:
            raise ValueError("flush_interval_s must be greater than zero")

        self._session = session
        self._flush_interval_s = flush_interval_s

        self._metrics_file: IO[str] | None = None
        self._events_file: IO[str] | None = None
        self._ecg_file: IO[str] | None = None
        self._gsr_file: IO[str] | None = None

        self._metrics_writer: csv.DictWriter | None = None
        self._events_writer: csv.DictWriter | None = None
        self._ecg_writer: csv.DictWriter | None = None
        self._gsr_writer: csv.DictWriter | None = None

        self._is_open = False
        self._last_flush_time = time.monotonic()

        self._metrics_written = 0
        self._events_written = 0
        self._ecg_samples_written = 0
        self._gsr_samples_written = 0

        self._ecg_continuity = EcgContinuityTracker()
        self._gsr_continuity = GsrContinuityTracker()

        self._session_boundary = SessionBoundaryTracker(start_sample=session.start_sample, start_transition_observed=session.start_transition_observed)
        self._gsr_session_boundary = GsrSessionBoundaryTracker(start_sample=session.start_sample, start_transition_observed=session.start_transition_observed)

    @property
    def gsr_session_boundary_integrity(self,) -> gsr_session_boundary_stats:
        return self._gsr_session_boundary.stats

    @property
    def session_boundary_integrity(self,) -> session_boundary_stats:
        return self._session_boundary.stats

    @property
    def session(self) -> SessionInfo:
        return self._session

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def metrics_written(self) -> int:
        return self._metrics_written

    @property
    def events_written(self) -> int:
        return self._events_written

    @property
    def ecg_samples_written(self) -> int:
        return self._ecg_samples_written

    @property
    def gsr_samples_written(self) -> int:
        return self._gsr_samples_written

    @property
    def ecg_integrity(self) -> ecg_continuity_stats:
        return self._ecg_continuity.stats

    @property
    def gsr_integrity(self) -> gsr_continuity_stats:
        return self._gsr_continuity.stats

    def open(self) -> None:
        if self._is_open:
            raise RuntimeError("The session CSV writer is already open")

        session_directory = self._session.directory

        session_directory.mkdir(
            parents=True,
            exist_ok=False,
        )

        self._write_metadata(session_directory / "metadata.json")

        try:
            self._metrics_file = (session_directory / "metrics.csv").open(
                mode="x",
                newline="",
                encoding="utf-8",
            )
            self._events_file = (session_directory / "events.csv").open(
                mode="x",
                newline="",
                encoding="utf-8",
            )
            self._ecg_file = (session_directory / "ecg.csv").open(
                mode="x",
                newline="",
                encoding="utf-8",
            )

            self._gsr_file = (session_directory / "gsr.csv").open(
                mode="x",
                newline="",
                encoding="utf-8",
            )

            self._metrics_writer = csv.DictWriter(
                self._metrics_file,
                fieldnames=METRICS_FIELDS,
            )
            self._events_writer = csv.DictWriter(
                self._events_file,
                fieldnames=EVENT_FIELDS,
            )
            self._ecg_writer = csv.DictWriter(
                self._ecg_file,
                fieldnames=ECG_FIELDS,
            )
            self._gsr_writer = csv.DictWriter(
                self._gsr_file,
                fieldnames=GSR_FIELDS,
            )

            self._metrics_writer.writeheader()
            self._events_writer.writeheader()
            self._ecg_writer.writeheader()
            self._gsr_writer.writeheader()

            self._is_open = True
            self._last_flush_time = time.monotonic()

        except Exception:
            self._close_files()
            raise

    def write(
        self,
        message: telemetry_message,
    ) -> bool:
        self._require_open()

        was_written = False

        if isinstance(message, metrics_message):
            self._write_metrics(message)
            was_written = True
        elif isinstance(message, event_message):
            self._write_event(message)
            was_written = True
        elif isinstance(message, ecg_batch_message):
            self._write_ecg_batch(message)
            was_written = True
        elif isinstance(message, gsr_batch_message):
            self._write_gsr_batch(message)
            was_written = True

        if was_written:
            self._flush_if_due()

        return was_written

    def flush(self) -> None:
        self._require_open()

        for file in self._open_files():
            file.flush()

        self._last_flush_time = time.monotonic()

    def set_stop_sample(self, stop_sample: int) -> None:
        self._require_open()

        self._session_boundary.set_stop_sample(stop_sample)
        self._gsr_session_boundary.set_stop_sample(stop_sample)

    def close(self, stop_sample: int | None = None) -> None:
        if not self._is_open:
            return

        if stop_sample is not None:
            self.set_stop_sample(stop_sample)

        try:
            self.flush()
            self._update_metadata()

        finally:
            self._close_files()
            self._metrics_writer = None
            self._events_writer = None
            self._ecg_writer = None
            self._gsr_writer = None

            self._is_open = False

    def _write_metadata(
        self,
        path: Path,
    ) -> None:
        metadata: dict[str, Any] = {
            "format_version": SESSION_FORMAT_VERSION,
            "session_id": self._session.session_id,
            "device_id": self._session.device_id,
            "started_at_utc": format_utc_timestamp(self._session.started_at_utc),
            "start_sample": self._session.start_sample,
            "start_transition_observed": self._session.start_transition_observed
        }

        with path.open(
            mode="x",
            encoding="utf-8",
        ) as file:
            json.dump(
                metadata,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.write("\n")

    def _update_metadata(self) -> None:
        ecg_stats = self._ecg_continuity.stats
        gsr_stats = self._gsr_continuity.stats
        boundary = self._session_boundary.stats
        gsr_boundary = self._gsr_session_boundary.stats

        ecg_continuity_complete = (ecg_stats.received_batches > 0 and ecg_stats.is_complete)
        gsr_continuity_complete = (gsr_stats.received_batches > 0 and gsr_stats.is_complete)

        ecg_stream_complete = (ecg_continuity_complete and boundary.is_complete)
        gsr_stream_complete = (gsr_continuity_complete and gsr_boundary.is_complete)

        metadata: dict[str, Any] = {
            "format_version": SESSION_FORMAT_VERSION,
            "session_id": self._session.session_id,
            "device_id": self._session.device_id,
            "started_at_utc": format_utc_timestamp(self._session.started_at_utc),
            "closed_at_utc": format_utc_timestamp(datetime.now(timezone.utc)),
            "record_counts": {
                "metrics": self._metrics_written,
                "events": self._events_written,
                "ecg_samples": self.ecg_samples_written,
                "gsr_samples": self.gsr_samples_written,
            },
            "ecg_integrity": {
                "received_batches": ecg_stats.received_batches,
                "received_samples": ecg_stats.received_samples,
                "gap_count": ecg_stats.gap_count,
                "missing_samples": ecg_stats.missing_samples,
                "non_monotonic_batches": ecg_stats.non_monotonic_batches,
                "first_sample_start": ecg_stats.first_sample_start,
                "last_sample_end": ecg_stats.last_sample_end,
                "next_expected_sample_start": ecg_stats.next_expected_sample_start,
                "is_complete": ecg_continuity_complete,
            },
            "gsr_integrity": {
                "received_batches": gsr_stats.received_batches,
                "received_samples": gsr_stats.received_samples,
                "gap_count": gsr_stats.gap_count,
                "missing_samples": gsr_stats.missing_samples,
                "non_monotonic_batches": gsr_stats.non_monotonic_batches,
                "misaligned_batches": gsr_stats.misaligned_batches,
                "configuration_changes": gsr_stats.configuration_changes,
                "first_sample_start": gsr_stats.first_sample_start,
                "last_sample_end": gsr_stats.last_sample_end,
                "next_expected_sample_start": gsr_stats.next_expected_sample_start,
                "sample_step": gsr_stats.sample_step,
                "sample_rate": gsr_stats.sample_rate,
                "continuity_complete": gsr_continuity_complete,
            },
            "session_boundary_integrity": {
                "start_sample": boundary.start_sample,
                "start_transition_observed": boundary.start_transition_observed,
                "stop_sample": boundary.stop_sample,
                "first_ecg_sample": boundary.first_ecg_sample,
                "last_ecg_sample": boundary.last_ecg_sample,
                "first_batch_count": boundary.first_batch_count,
                "last_batch_count": boundary.last_batch_count,
                "start_offset_samples": boundary.start_offset_samples,
                "stop_offset_samples": boundary.stop_offset_samples,
                "start_boundary_complete": boundary.start_boundary_complete,
                "stop_boundary_complete": boundary.stop_boundary_complete,
                "is_complete": boundary.is_complete
            },
            "gsr_boundary_integrity": {
                "start_sample": gsr_boundary.start_sample,
                "start_transition_observed": gsr_boundary.start_transition_observed,
                "stop_sample": gsr_boundary.stop_sample,
                "first_gsr_sample": gsr_boundary.first_gsr_sample,
                "last_gsr_sample": gsr_boundary.last_gsr_sample,
                "first_sample_step": gsr_boundary.first_sample_step,
                "last_sample_step": gsr_boundary.last_sample_step,
                "expected_first_gsr_sample": gsr_boundary.expected_first_gsr_sample,
                "expected_last_gsr_sample": gsr_boundary.expected_last_gsr_sample,
                "start_offset_samples": gsr_boundary.start_offset_samples,
                "stop_offset_samples": gsr_boundary.stop_offset_samples,
                "start_boundary_complete": gsr_boundary.start_boundary_complete,
                "stop_boundary_complete": gsr_boundary.stop_boundary_complete,
                "is_complete": gsr_boundary.is_complete
            },
            "session_integrity": {
                "ecg_complete": ecg_stream_complete,
                "gsr_complete": gsr_stream_complete,
                "is_complete": (ecg_stream_complete and gsr_stream_complete)
            }
        }

        path = self._session.directory / "metadata.json"

        with path.open(
            mode="w",
            encoding="utf-8",
        ) as file:
            json.dump(metadata, file, ensure_ascii=False, indent=2)

            file.write("\n")

    def _write_metrics(self, message: metrics_message) -> None:
        writer = self._metrics_writer

        if writer is None:
            raise RuntimeError("The metrics CSV writer is not initialized")

        writer.writerow(
            {
                "received_at_utc": format_utc_timestamp(message.received_at_utc),
                "protocol_version": message.protocol_version,
                "device": message.device,
                "sample": message.sample,
                "ecg_bpm": message.ecg_bpm,
                "leads_off": int(message.leads_off),
                "gsr": message.gsr,
                "gsr_resistance_ohm": message.gsr_resistance_ohm,
                "gsr_conductance_us": message.gsr_conductance_us,
                "gsr_valid": int(message.gsr_valid),
                "gsr_calibration_raw": message.gsr_calibration_raw,
                "batt_v": message.batt_v,
                "batt_percent": message.batt_percent,
                "temp_amb": message.temp_amb,
                "temp_obj": message.temp_obj,
                "max_bpm": message.max_bpm,
                "spo2": message.spo2,
                "spo2_valid": int(message.spo2_valid),
                "max_finger": int(message.max_finger),
                "ui_flags": message.ui_flags,
            }
        )

        self._metrics_written += 1

    def _write_event(
        self,
        message: event_message,
    ) -> None:
        writer = self._events_writer

        if writer is None:
            raise RuntimeError("The events CSV writer is not initialized")

        writer.writerow(
            {
                "received_at_utc": format_utc_timestamp(message.received_at_utc),
                "protocol_version": message.protocol_version,
                "device": message.device,
                "sample": message.sample,
                "event": message.event,
                "ui_flags": message.ui_flags,
            }
        )

        self._events_written += 1

    def _write_ecg_batch(
        self,
        message: ecg_batch_message,
    ) -> None:

        writer = self._ecg_writer

        if writer is None:
            raise RuntimeError("The ECG CSV writer is not initialized")

        self._session_boundary.observe(message)

        continuity = self._ecg_continuity.observe(message)

        if continuity.status is EcgContinuityStatus.GAP:
            LOGGER.warning(
                f"ECG gap detected in session {self._session.session_id}: expected_sample_start={continuity.expected_sample_start}, actual_sample_start={continuity.actual_sample_start}, missing_samples={continuity.missing_samples}"
            )
        elif continuity.status is EcgContinuityStatus.NON_MONOTONIC:
            LOGGER.warning(
                f"Non-monotonic ECG batch detected in session {self._session.session_id}: expected_sample_start={continuity.expected_sample_start}, actual_sample_start={continuity.actual_sample_start}"
            )

        for index in range(message.count):
            sample = message.sample_start + index

            writer.writerow(
                {
                    "received_at_utc": format_utc_timestamp(message.received_at_utc),
                    "protocol_version": message.protocol_version,
                    "device": message.device,
                    "sample": sample,
                    "sample_rate": message.sample_rate,
                    "device_time_s": sample / message.sample_rate,
                    "raw": message.raw[index],
                    "filtered": message.filtered[index],
                    "rpeak": int(message.rpeak[index]),
                    "leads_off": int(message.leads_off[index]),
                }
            )

        self._ecg_samples_written += message.count

    def _write_gsr_batch(
        self,
        message: gsr_batch_message,
    ) -> None:

        writer = self._gsr_writer

        if writer is None:
            raise RuntimeError("The GSR CSV writer is not initialized")

        continuity = self._gsr_continuity.observe(message)

        if continuity.status is GsrContinuityStatus.GAP:
            LOGGER.warning(
                f"GSR gap detected in session {self._session.session_id}: "
                f"expexted_sample_start={continuity.expected_sample_start}, "
                f"actual_sample_start={continuity.actual_sample_start}, "
                f"missing_samples={continuity.missing_samples}"
            )
        elif (continuity.status is GsrContinuityStatus.NON_MONOTONIC):
            LOGGER.warning(
                f"Non-monotonic GSR batch detected in session {self._session.session_id}: "
                f"expexted_sample_start={continuity.expected_sample_start}, "
                f"actual_sample_start={continuity.actual_sample_start}"
            )
        elif continuity.status is GsrContinuityStatus.MISALIGNED:
            LOGGER.warning(
                f"Misaligned GSR batch detected in session {self._session.session_id}: "
                f"expexted_sample_start={continuity.expected_sample_start}, "
                f"actual_sample_start={continuity.actual_sample_start}, "
                f"sample_step={message.sample_step}"
            )
        elif continuity.status is GsrContinuityStatus.CONFIGURATION_CHANGED:
            LOGGER.warning(
                f"GSR stream configuration changed in session {self._session.session_id}: "
                f"sample_step={continuity.previous_sample_step}->{message.sample_step}, "
                f"sample_rate{continuity.previous_sample_rate}->{message.sample_rate}"
            )

        self._gsr_session_boundary.observe(message)
        device_sample_rate_hz = (message.sample_rate * message.sample_step)

        for index in range(message.count):
            sample = message.sample_start + index * message.sample_step

            writer.writerow(
                {
                    "received_at_utc": format_utc_timestamp(message.received_at_utc),
                    "protocol_version": message.protocol_version,
                    "device": message.device,
                    "sample": sample,
                    "sample_step": message.sample_step,
                    "sample_rate": message.sample_rate,
                    "device_time_s": sample / device_sample_rate_hz,
                    "calibration_raw": message.calibration_raw,
                    "raw": message.raw[index],
                    "resistance_ohm": message.resistance_ohm[index],
                    "conductance_us": message.conductance_us[index],
                    "valid": int(message.valid[index]),
                }
            )

        self._gsr_samples_written += message.count

    def _flush_if_due(self) -> None:
        elapsed = time.monotonic() - self._last_flush_time

        if elapsed >= self._flush_interval_s:
            self.flush()

    def _require_open(
        self,
    ) -> None:
        if not self._is_open:
            raise RuntimeError("The session CSV writer is not open")

    def _open_files(self) -> tuple[IO[str], ...]:
        files = (
            self._metrics_file,
            self._events_file,
            self._ecg_file,
            self._gsr_file,
        )

        return tuple(file for file in files if file is not None)

    def _close_files(self) -> None:
        for file in self._open_files():
            file.close()

        self._metrics_file = None
        self._events_file = None
        self._ecg_file = None
        self._gsr_file = None

    def __enter__(self) -> SessionCsvWriter:
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
