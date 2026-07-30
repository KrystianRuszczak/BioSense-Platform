from __future__ import annotations

import logging
import queue
import ssl
import threading
import uuid
from typing import Any

import paho.mqtt.client as mqtt

from collector.message_parser import MessageParseError, MessageParser
from shared.config import mqtt_config
from shared.models import telemetry_message
from shared.topics import ECG_TOPIC, GSR_TOPIC, EVENTS_TOPIC, METRICS_TOPIC, STATUS_TOPIC

LOGGER = logging.getLogger(__name__)

class MqttTelemetryClient:
    def __init__(self, config: mqtt_config, output_queue: queue.Queue[telemetry_message]) -> None:
        self._config = config
        self._output_queue = output_queue

        self._parser = MessageParser(device_id=config.device_id)

        self._status_topic = STATUS_TOPIC.format(device_id=config.device_id)
        self._metrics_topic = METRICS_TOPIC.format(device_id=config.device_id)
        self._events_topic = EVENTS_TOPIC.format(device_id=config.device_id)
        self._ecg_topic = ECG_TOPIC.format(device_id=config.device_id)
        self._gsr_topic = GSR_TOPIC.format(device_id=config.device_id)

        self._connected = threading.Event()
        self._stop_requested = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._stats_lock = threading.Lock()

        self._started = False
        self._dropped_messages = 0

        client_id = (f"biosense-platform-{config.device_id}-{uuid.uuid4().hex[:8]}")

        self._client = mqtt.Client(
            callback_api_version=(mqtt.CallbackAPIVersion.VERSION2), 
            client_id=client_id, 
            clean_session=True, 
            protocol=mqtt.MQTTv311, 
            reconnect_on_failure=True,)

        self._client.username_pw_set(config.username, config.password)

        tls_context = ssl.create_default_context()

        self._client.tls_set_context(tls_context)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def dropped_messages(self) -> int:
        with self._stats_lock:
            return self._dropped_messages

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                raise RuntimeError("MQTT Client has already been started")

            self._stop_requested.clear()
            self._connected.clear()

            self._client.connect_async(
                host=self._config.broker,
                port=self._config.port,
                keepalive=60,
            )

            result = self._client.loop_start()

            if result != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"Failed to start the MQTT loop: {mqtt.error_string(result)}")

            self._started = True

            LOGGER.info("MQTT client has been started: %s:%d", self._config.broker, self._config.port)

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._started:
                return

            self._stop_requested.set()
            self._client.disconnect()
            self._client.loop_stop()
            self._connected.clear()
            self._started = False

            LOGGER.info("MQTT client has been stopped")

    def wait_until_connected(self, timeout: float | None = None) -> bool:
        return self._connected.wait(timeout=timeout)

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: mqtt.ConnectFlags, reason_code: mqtt.ReasonCode, properties: mqtt.Properties | None) -> None:
        del userdata
        del flags
        del properties

        if reason_code.is_failure:
            self._connected.clear()

            LOGGER.error("Broker rejected the MQTT connection: %s", reason_code)

            return

        self._connected.set()

        subscriptions = [
            (self._status_topic, 1),
            (self._metrics_topic, 0),
            (self._events_topic, 1),
            (self._ecg_topic, 0),
            (self._gsr_topic, 0)
        ]

        result, message_id = client.subscribe(subscriptions)

        if result != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.error(f"Failed to send subscription: {mqtt.error_string(result)}")
            return

        LOGGER.info(f"Connected to MQTT; Subscriptions sent, mid={message_id}")

    def _on_connect_fail(self, client: mqtt.Client, userdata: Any) -> None:
        del client
        del userdata

        self._connected.clear()

        LOGGER.warning("Failed to establish a connection to the MQTT broker")

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, disconnect_flags: mqtt.DisconnectFlags, reason_code: mqtt.ReasonCode, properties: mqtt.Properties | None) -> None:

        del client
        del userdata
        del disconnect_flags
        del properties

        self._connected.clear()

        if self._stop_requested.is_set():
            LOGGER.info("MQTT disconnected while closing the application")
            return

        if reason_code.is_failure:
            LOGGER.warning(f"MQTT connection lost: {reason_code}")
        else:
            LOGGER.info(f"MQTT disconnected: {reason_code}")

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        del client
        del userdata

        try:
            parsed_message = self._parser.parse(topic=message.topic, payload=message.payload)
        except MessageParseError as error:
            LOGGER.warning(f"Message from topic {message.topic} rejected: {error}")
            return

        try:
            self._output_queue.put_nowait(parsed_message)
        except queue.Full:
            with self._stats_lock:
                self._dropped_messages += 1
                dropped = self._dropped_messages

            LOGGER.error(f"The telemetry queue is full. Message from topic {message.topic} rejected. Total rejected: {dropped}")