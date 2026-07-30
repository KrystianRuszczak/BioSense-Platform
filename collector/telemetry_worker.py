from __future__ import annotations

import time
import logging
import queue
import threading
from pathlib import Path
from dataclasses import dataclass

from collector.csv_writer import SessionCsvWriter
from collector.live_store import LiveTelemetryStore
from collector.session_manager import SessionAction, SessionInfo, SessionManager
from shared.models import telemetry_message, ecg_batch_message, gsr_batch_message

LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class _pending_session_close:
    stop_sample:int
    deadline_monotonic: float

class TelemetryWorker:
    def __init__(
        self,
        input_queue: queue.Queue[telemetry_message],
        data_root: Path,
        device_id: str,
        live_store: LiveTelemetryStore,
        queue_poll_timeout_s: float = 0.25,
        session_close_timeout_s: float = 2.0
    ) -> None:
        if queue_poll_timeout_s <= 0:
            raise ValueError("queue_poll_timeout_s must be greater than zero")

        if session_close_timeout_s <= 0:
            raise ValueError("session_close_timeout_s must be greater than zero")

        self._input_queue = input_queue
        self._live_store = live_store
        self._session_manager = SessionManager(data_root=data_root, device_id=device_id)
        self._queue_poll_timeout_s = queue_poll_timeout_s
        self._writer: SessionCsvWriter | None = None

        self._session_close_timeout_s = session_close_timeout_s
        self._pending_close: (_pending_session_close | None) = None

        self._stop_requested = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._stats_lock = threading.Lock()

        self._thread: threading.Thread | None = None
        self._started = False

        self._processed_messages = 0
        self._discarded_messages = 0
        self._processing_errors = 0

        self._fatal_error: Exception | None = None

    @property
    def processed_messages(self) -> int:
        with self._stats_lock:
            return self._processed_messages

    @property
    def discarded_messages(self) -> int:
        with self._stats_lock:
            return self._discarded_messages

    @property
    def processing_errors(self) -> int:
        with self._stats_lock:
            return self._processing_errors

    @property
    def fatal_error(self) -> Exception | None:
        with self._stats_lock:
            return self._fatal_error

    @property
    def is_running(self) -> bool:
        thread = self._thread

        return thread is not None and thread.is_alive()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                raise RuntimeError("The telemetry worker is already running")

            self._stop_requested.clear()

            with self._stats_lock:
                self._fatal_error = None

            self._thread = threading.Thread(
                target=self._run,
                name="biosense-telemetry-worker",
                daemon=False,
            )

            self._started = True
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        with self._lifecycle_lock:
            if not self._started:
                return

            thread = self._thread
            self._stop_requested.set()

        if thread is not None:
            thread.join(timeout=timeout)

            if thread.is_alive():
                raise TimeoutError(
                    "The telemetry worker did not stop within the requested timeout"
                )

        with self._lifecycle_lock:
            self._thread = None
            self._started = False

    def raise_if_failed(self) -> None:
        error = self.fatal_error

        if error is not None:
            raise RuntimeError(
                "The telemetry worker terminated because of an internal error"
            ) from error

    def _run(self) -> None:
        LOGGER.info("Telemetry worker started")

        try:
            while True:
                self._finish_pending_close_if_ready()

                if self._stop_requested.is_set() and self._input_queue.empty() and self._pending_close is None:
                    break

                try:
                    message = self._input_queue.get(timeout=self._queue_poll_timeout_s)
                except queue.Empty:
                    continue

                try:
                    self._process_message(message)

                    with self._stats_lock:
                        self._processed_messages += 1
                except Exception as error:
                    with self._stats_lock:
                        self._processing_errors += 1
                        self._fatal_error = error

                    LOGGER.exception(
                        f"The telemetry worker failed while processing a {type(message).__name__} message"
                    )

                    self._stop_requested.set()
                    self._discard_pending_messages()
                    break

                finally:
                    self._input_queue.task_done()

        finally:
            try:
                pending = self._pending_close

                self._close_writer(
                    stop_sample=(
                        pending.stop_sample if pending is not None else None
                    )
                )
            except Exception as error:
                with self._stats_lock:
                    self._processing_errors += 1

                    if self._fatal_error is None:
                        self._fatal_error = error

                LOGGER.exception("Failed to close the active session writer")

            LOGGER.info("Telemetry worker stopped")

    def _process_message(self, message: telemetry_message) -> None:

        self._live_store.update(message)
        update = self._session_manager.process(message)

        # ===========================================TEMP================================
        if update.action is not SessionAction.NONE:
            LOGGER.info(
                "Session transition source: message=%s, sample=%s, ui_flags=%s",
                type(message).__name__,
                getattr(message, "sample", None),
                getattr(message, "ui_flags", None),
            )
        # ===========================================TEMP================================

        if update.action is SessionAction.STARTED:
            pending = self._pending_close

            if pending is not None:
                LOGGER.warning("A new session started while the previous session was still waiting for trailing telemetry; forcing the previous session to close")

                self._close_writer(stop_sample=pending.stop_sample)

            session = update.session

            if session is None:
                raise RuntimeError(
                    "SessionManager returned STARTED without session information"
                )

            self._open_writer(session)

            if self._writer is not None:
                self._writer.write(message)

            return

        if update.action is SessionAction.STOPPED:
            writer = self._writer

            if writer is None:
                LOGGER.warning(
                    "A session stop was received, but no session writer is active"
                )
                return

            stop_sample = update.transition_sample

            if stop_sample is None:
                raise RuntimeError("SessionManager returned STOPPED without a transition sample")

            writer.write(message)

            writer.set_stop_sample(stop_sample)

            self._pending_close = (
                _pending_session_close(
                    stop_sample=stop_sample,
                    deadline_monotonic=(
                        time.monotonic() + self._session_close_timeout_s
                    )
                )
            )

            LOGGER.info(
                f"Measurement session waiting for trailing telemetry: session={writer.session.session_id}, stop_sample={stop_sample}, timeout={self._session_close_timeout_s}"
            )

            return

        writer = self._writer

        if writer is None:
            return

        if self._pending_close is not None:
            if isinstance(message, (ecg_batch_message, gsr_batch_message,),):
                writer.write(message)

            return

        writer.write(message)

    def _open_writer(
        self,
        session: SessionInfo,
    ) -> None:
        if self._writer is not None:
            raise RuntimeError(
                "Cannot start a new session while another session writer is active"
            )

        writer = SessionCsvWriter(session=session)

        writer.open()
        self._writer = writer

        LOGGER.info(f"Measurement session started {session.session_id}")

    def _close_writer(self, stop_sample: int | None = None) -> None:
        writer = self._writer

        if writer is None:
            return

        session_id = writer.session.session_id

        metrics_written = writer.metrics_written
        events_written = writer.events_written
        ecg_samples_written = writer.ecg_samples_written
        gsr_samples_written = writer.gsr_samples_written
        ecg_integrity = writer.ecg_integrity
        gsr_integrity = writer.gsr_integrity

        try:
            writer.close(stop_sample=stop_sample)
        finally:
            self._writer = None
            self._pending_close = None

        LOGGER.info(
            "Measurement session closed: %s; "
            "metrics=%d, events=%d, "
            "ecg_samples=%d, gsr_samples=%d, "
            "ecg_gaps=%d, missing_ecg_samples=%d, "
            "ecg_complete=%s, "
            "gsr_gaps=%d, missing_gsr_samples=%d, "
            "gsr_non_monotonic=%d, "
            "gsr_misaligned=%d, "
            "gsr_configuration_changes=%d, "
            "gsr_continuity_complete=%s",
            session_id,
            metrics_written,
            events_written,
            ecg_samples_written,
            gsr_samples_written,
            ecg_integrity.gap_count,
            ecg_integrity.missing_samples,
            ecg_integrity.is_complete,
            gsr_integrity.gap_count,
            gsr_integrity.missing_samples,
            gsr_integrity.non_monotonic_batches,
            gsr_integrity.misaligned_batches,
            gsr_integrity.configuration_changes,
            gsr_integrity.is_complete,
        )

    def _discard_pending_messages(self) -> None:
        discarded = 0

        while True:
            try:
                self._input_queue.get_nowait()
            except queue.Empty:
                break

            else:
                self._input_queue.task_done()
                discarded += 1

        if discarded == 0:
            return

        with self._stats_lock:
            self._discarded_messages += discarded

        LOGGER.error(
            f"Discarded {discarded} queued telemetry messages after a worker failure"
        )

    def _finish_pending_close_if_ready(self,) -> None:
        pending = self._pending_close

        if pending is None:
            return

        writer = self._writer

        if writer is None:
            raise RuntimeError("A pending session close exists without an active session writer")

        ecg_boundary = writer.session_boundary_integrity
        gsr_boundary = writer.gsr_session_boundary_integrity
        ecg_ready = ecg_boundary.stop_boundary_complete
        gsr_ready = gsr_boundary.stop_boundary_complete

        if ecg_ready and gsr_ready:
            LOGGER.info(f"Trailing telemetry completed the session boundaries: session={writer.session.session_id}, stop_sample={pending.stop_sample}")

            self._close_writer(stop_sample=pending.stop_sample)

            return

        if time.monotonic() < pending.deadline_monotonic:
            return

        LOGGER.warning(
            "Timed out while waiting for trailing"
            f"session telemetry: session={writer.session.session_id}, "
            f"stop_sample={pending.stop_sample}, "
            f"ecg_ready={ecg_ready}, "
            f"gsr_ready={gsr_ready}, last_ecg_sample={ecg_boundary.last_ecg_sample}, last_gsr_sample={gsr_boundary.last_gsr_sample}"
        )

        self._close_writer(stop_sample=pending.stop_sample)
