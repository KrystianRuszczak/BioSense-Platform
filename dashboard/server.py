from __future__ import annotations

import logging
import threading
from typing import Any

from dash import Dash
from werkzeug.serving import make_server

LOGGER = logging.getLogger(__name__)

class DashboardServer:
    def __init__(self, app: Dash, host: str = "127.0.0.1", port: int = 8050,) -> None:
        if not host:
            raise ValueError("Host must not be empty")

        if port <= 0 or port > 65535:
            raise ValueError("Port must be between 1 and 65535")

        self._app = app
        self._host = host
        self._port = port

        self._lock = threading.Lock()
        self._server: Any | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return (f"http://{self._host}:{self._port}")

    @property
    def is_running(self) -> bool:
        thread = self._thread

        return (thread is not None and thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                raise RuntimeError("The dashboard server is already running")

            server = make_server(
                self._host,
                self._port,
                self._app.server,
                threaded=True,
            )

            thread = threading.Thread(target=self._serve, args=(server,), name="biosense-dashboard-server", daemon=False,)

            self._server = server
            self._thread = thread

            thread.start()

        LOGGER.info(f"BioSense dashboard started: {self.url}")

    def stop(self, timeout: float = 10.0) -> None:
        if timeout <= 0:
            raise ValueError("Timeout must be greater than zero")

        with self._lock:
            server = self._server
            thread = self._thread

        if server is None:
            return

        server.shutdown()

        if thread is not None:
            thread.join(timeout=timeout)

            if thread.is_alive():
                raise TimeoutError("The dashboard server did not stop within the requested timeout")

        server.server_close()

        with self._lock:
            self._server = None
            self._thread = None

        LOGGER.info("BioSense dashboard stopped")

    @staticmethod
    def _serve(server: Any) -> None:
        server.serve_forever()