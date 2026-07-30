from __future__ import annotations

import logging
import queue
import time
from pathlib import Path

from collector.live_store import LiveTelemetryStore
from collector.mqtt_client import MqttTelemetryClient
from collector.telemetry_worker import TelemetryWorker
from dashboard.app import create_dashboard_app
from dashboard.server import DashboardServer
from shared.config import load_mqtt_config
from shared.models import telemetry_message

LOGGER = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT_DIR / "data"

TELEMETRY_QUEUE_SIZE = 4096
MQTT_INITIAL_CONNECTION_TIMEOUT_S = 15.0
HEALTH_CHECK_INTERVAL_S = 0.5
LIVE_ECG_WINDOW_S = 10.0
LIVE_EVENT_LIMIT = 100
LIVE_METRICS_WINDOW_S = 600.0

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8050

def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"),
        datefmt="%d-%m-%Y %H:%M:%S",
    )


def run() -> None:
    cfg = load_mqtt_config()

    telemetry_queue: queue.Queue[telemetry_message] = queue.Queue(
        maxsize=TELEMETRY_QUEUE_SIZE
    )

    live_store = LiveTelemetryStore(
        device_id=cfg.device_id,
        ecg_window_s=LIVE_ECG_WINDOW_S,
        event_limit=LIVE_EVENT_LIMIT,
        metrics_window_s=LIVE_METRICS_WINDOW_S
    )

    worker = TelemetryWorker(
        input_queue=telemetry_queue,
        data_root=DATA_ROOT,
        device_id=cfg.device_id,
        live_store=live_store,
    )

    mqtt_client = MqttTelemetryClient(config=cfg, output_queue=telemetry_queue)

    dashboard_app = create_dashboard_app(live_store=live_store)

    dashboard_server = DashboardServer(app=dashboard_app, host=DASHBOARD_HOST, port=DASHBOARD_PORT)

    LOGGER.info("Starting BioSense collector")

    LOGGER.info(f"Device: {cfg.device_id}")

    LOGGER.info(f"Broker: {cfg.broker}:{cfg.port}")

    LOGGER.info(f"Session data directory: {DATA_ROOT / 'sessions'}")

    try:
        worker.start()
        mqtt_client.start()
        dashboard_server.start()

        connected = mqtt_client.wait_until_connected(
            timeout=MQTT_INITIAL_CONNECTION_TIMEOUT_S
        )

        if connected:
            LOGGER.info("BioSense collector is running")
        else:
            LOGGER.warning(
                f"The initial MQTT connection was not established wihtin {MQTT_INITIAL_CONNECTION_TIMEOUT_S} seconds."
                "Reconnection attempts will continue in the background."
            )

        LOGGER.info(f"Open the dashboard in a browser: {dashboard_server.url}")

        while True:
            worker.raise_if_failed()

            time.sleep(HEALTH_CHECK_INTERVAL_S)

    except KeyboardInterrupt:
        LOGGER.info("Shutdown requested by the user")

    finally:
        try:
            dashboard_server.stop()
        except Exception:
            LOGGER.exception("Failed to stop the dashboard server")

        mqtt_client.stop()
        worker.stop()

        LOGGER.info(
            f"Collector statistics: processed={worker.processed_messages}, worker_discarded={worker.discarded_messages}, mqtt_dropped={mqtt_client.dropped_messages}, processing_errors={worker.processing_errors}"
        )

        LOGGER.info("BioSense collector stopped")

    worker.raise_if_failed()


def main() -> int:
    configure_logging()

    try:
        run()

    except Exception:
        LOGGER.exception("BioSense collector terminated because of an error")

        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
