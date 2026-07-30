# from __future__ import annotations

# import csv
# import json
# import os
# import ssl
# from datetime import datetime, timezone
# from pathlib import Path
# from typing import Any

# import paho.mqtt.client as mqtt

# from shared.config import load_mqtt_config
# from shared.topics import EVENTS_TOPIC, METRICS_TOPIC, STATUS_TOPIC

# ROOT_DIR = Path(__file__).resolve().parents[1]
# DATA_DIR = ROOT_DIR / "data"

# RUN_TIMESTAMP = datetime.now(timezone.utc).strftime("%d/%m/%Y, T%H%M%SZ")
# METRICS_FIELDS = [
#     "received_at_utc",
#     "v",
#     "device",
#     "sample",
#     "ecg_bpm",
#     "leads_off",
#     "gsr",
#     "batt_v",
#     "batt_percent",
#     "temp_amb",
#     "temp_obj",
#     "max_bpm",
#     "spo2",
#     "spo2_valid",
#     "max_finger",
#     "ui_flags",
# ]

# EVENT_FIELDS = [
#     "received_at_utc",
#     "v",
#     "device",
#     "sample",
#     "event",
#     "ui_flags",
# ]

# STATUS_FIELDS = [
#     "received_at_utc",
#     "device",
#     "online",
# ]

# def utc_timestamp() -> str:
#     return datetime.now(timezone.utc).isoformat(timespec="miliseconds")

# def append_csv(path: Path, fieldnames: list[str], row: dict[str, Any],) -> None:
#     path.parent.mkdir(parents=True, exist_ok=True)

#     write_header = (not path.exists() or path.stat().st_size == 0)

#     with path.open(mode="a", newline="", encoding="utf-8",) as file:
#         writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore",)

#         if write_header:
#             writer.writeheader()
        
#         writer.writerow(row)



from __future__ import annotations

import csv
import json
import os
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from shared.config import load_mqtt_config
from shared.topics import (
    ECG_TOPIC,
    EVENTS_TOPIC,
    METRICS_TOPIC,
    STATUS_TOPIC,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"

RUN_TIMESTAMP = datetime.now(
    timezone.utc
).strftime("%Y%m%dT%H%M%SZ")


METRICS_FIELDS = [
    "received_at_utc",
    "v",
    "device",
    "sample",
    "ecg_bpm",
    "leads_off",
    "gsr",
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
    "v",
    "device",
    "sample",
    "event",
    "ui_flags",
]

STATUS_FIELDS = [
    "received_at_utc",
    "device",
    "online",
]


def utc_timestamp() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="milliseconds")


def append_csv(
    path: Path,
    fieldnames: list[str],
    row: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    write_header = (
        not path.exists()
        or path.stat().st_size == 0
    )

    with path.open(
        mode="a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        if write_header:
            writer.writeheader()

        writer.writerow(row)


class MqttCollector:
    def __init__(self) -> None:
        self.config = load_mqtt_config()

        self.status_topic = STATUS_TOPIC.format(
            device_id=self.config.device_id
        )

        self.metrics_topic = METRICS_TOPIC.format(
            device_id=self.config.device_id
        )

        self.events_topic = EVENTS_TOPIC.format(
            device_id=self.config.device_id
        )

        self.ecg_topic = ECG_TOPIC.format(
            device_id=self.config.device_id
        )

        device = self.config.device_id

        self.metrics_file = (
            DATA_DIR
            / f"{device}_{RUN_TIMESTAMP}_metrics.csv"
        )

        self.events_file = (
            DATA_DIR
            / f"{device}_{RUN_TIMESTAMP}_events.csv"
        )

        self.status_file = (
            DATA_DIR
            / f"{device}_{RUN_TIMESTAMP}_status.csv"
        )

        client_id = (
            f"biosense-platform-"
            f"{device}-"
            f"{os.getpid()}"
        )

        self.client = mqtt.Client(
            callback_api_version=(
                mqtt.CallbackAPIVersion.VERSION2
            ),
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )

        self.client.username_pw_set(
            self.config.username,
            self.config.password,
        )

        self.client.tls_set(
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )

        self.client.reconnect_delay_set(
            min_delay=1,
            max_delay=30,
        )

        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del userdata
        del flags
        del properties

        if reason_code.is_failure:
            print(
                "Połączenie MQTT odrzucone: "
                f"{reason_code}"
            )
            return

        print("Połączono z brokerem MQTT.")
        print(f"Status:  {self.status_topic}")
        print(f"Metrics: {self.metrics_topic}")
        print(f"Events:  {self.events_topic}")
        print(f"ECG:     {self.ecg_topic}")
        print()

        subscriptions = [
            (self.status_topic, 1),
            (self.metrics_topic, 0),
            (self.events_topic, 1),
            (self.ecg_topic, 0),
        ]

        result, message_id = client.subscribe(
            subscriptions
        )

        if result != mqtt.MQTT_ERR_SUCCESS:
            print(
                "Nie udało się zasubskrybować topiców: "
                f"{mqtt.error_string(result)}"
            )
            return

        print(
            "Subskrypcje wysłane, "
            f"message_id={message_id}"
        )

    def on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del client
        del userdata
        del disconnect_flags
        del properties

        print(
            "\nRozłączono z MQTT: "
            f"{reason_code}"
        )

    def on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        del client
        del userdata

        try:
            payload_text = message.payload.decode(
                "utf-8"
            )

            payload = json.loads(payload_text)

            if not isinstance(payload, dict):
                raise ValueError(
                    "JSON nie jest obiektem"
                )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            print(
                "\nNieprawidłowy payload MQTT:"
            )
            print(f"Topic: {message.topic}")
            print(f"Payload: {message.payload!r}")
            print(f"Błąd: {error}")
            return

        received_at = utc_timestamp()

        try:
            if message.topic == self.status_topic:
                self.handle_status(
                    payload,
                    received_at,
                )

            elif message.topic == self.metrics_topic:
                self.handle_metrics(
                    payload,
                    received_at,
                )

            elif message.topic == self.events_topic:
                self.handle_event(
                    payload,
                    received_at,
                )
            elif message.topic == self.ecg_topic:
                self.handle_ecg(
                    payload,
                    received_at,
                )

            else:
                print(
                    "Wiadomość z nieznanego topicu: "
                    f"{message.topic}"
                )

        except (
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            print(
                "\nNieprawidłowa struktura wiadomości:"
            )
            print(f"Topic: {message.topic}")
            print(f"Payload: {payload}")
            print(f"Błąd: {error}")

    def handle_status(
        self,
        payload: dict[str, Any],
        received_at: str,
    ) -> None:
        online = bool(payload.get("online", False))

        row = {
            "received_at_utc": received_at,
            "device": self.config.device_id,
            "online": int(online),
        }

        append_csv(
            self.status_file,
            STATUS_FIELDS,
            row,
        )

        print(
            f"[STATUS] "
            f"device={self.config.device_id} "
            f"online={online}"
        )

    def handle_metrics(
        self,
        payload: dict[str, Any],
        received_at: str,
    ) -> None:
        row = {
            "received_at_utc": received_at,
            "v": int(payload.get("v", 0)),
            "device": str(
                payload.get(
                    "device",
                    self.config.device_id,
                )
            ),
            "sample": int(
                payload.get("sample", 0)
            ),
            "ecg_bpm": int(
                payload.get("ecg_bpm", 0)
            ),
            "leads_off": int(
                bool(payload.get("leads_off", False))
            ),
            "gsr": int(
                payload.get("gsr", 0)
            ),
            "batt_v": float(
                payload.get("batt_v", 0.0)
            ),
            "batt_percent": int(
                payload.get("batt_percent", 0)
            ),
            "temp_amb": float(
                payload.get("temp_amb", 0.0)
            ),
            "temp_obj": float(
                payload.get("temp_obj", 0.0)
            ),
            "max_bpm": int(
                payload.get("max_bpm", 0)
            ),
            "spo2": float(
                payload.get("spo2", 0.0)
            ),
            "spo2_valid": int(
                bool(payload.get("spo2_valid", False))
            ),
            "max_finger": int(
                bool(payload.get("max_finger", False))
            ),
            "ui_flags": int(
                payload.get("ui_flags", 0)
            ),
        }

        append_csv(
            self.metrics_file,
            METRICS_FIELDS,
            row,
        )

        print(
            "\r"
            f"[METRICS] "
            f"sample={row['sample']} | "
            f"ECG={row['ecg_bpm']} BPM | "
            f"MAX={row['max_bpm']} BPM | "
            f"SpO2={row['spo2']:.1f}% | "
            f"GSR={row['gsr']} | "
            f"BAT={row['batt_v']:.3f}V/"
            f"{row['batt_percent']}% | "
            f"Tobj={row['temp_obj']:.2f}C | "
            f"LO={row['leads_off']}      ",
            end="",
            flush=True,
        )

    def handle_event(
        self,
        payload: dict[str, Any],
        received_at: str,
    ) -> None:
        row = {
            "received_at_utc": received_at,
            "v": int(payload.get("v", 0)),
            "device": str(
                payload.get(
                    "device",
                    self.config.device_id,
                )
            ),
            "sample": int(
                payload.get("sample", 0)
            ),
            "event": int(
                payload.get("event", 0)
            ),
            "ui_flags": int(
                payload.get("ui_flags", 0)
            ),
        }

        append_csv(
            self.events_file,
            EVENT_FIELDS,
            row,
        )

        print()
        print(
            f"[EVENT] "
            f"sample={row['sample']} | "
            f"event={row['event']} | "
            f"ui_flags={row['ui_flags']}"
        )

    def run(self) -> None:
        print(
            "BioSense MQTT Collector"
        )

        print(
            f"Broker: "
            f"{self.config.broker}:"
            f"{self.config.port}"
        )

        print(
            f"Device: {self.config.device_id}"
        )

        print(
            f"CSV metrics: {self.metrics_file}"
        )

        print(
            f"CSV events:  {self.events_file}"
        )

        print(
            f"CSV status:  {self.status_file}"
        )

        print()

        self.client.connect(
            self.config.broker,
            self.config.port,
            keepalive=60,
        )

        self.client.loop_forever(
            retry_first_connection=True
        )

    def handle_ecg(
        self,
        payload: dict[str, Any],
        received_at: str,
    ) -> None:
        del received_at

        sample_start = int(
            payload.get("sample_start", 0)
        )

        count = int(
            payload.get("count", 0)
        )

        raw = payload.get("raw", [])
        filtered = payload.get("filtered", [])

        if (
            not isinstance(raw, list) or
            not isinstance(filtered, list)
        ):
            raise ValueError(
                "Pola raw i filtered muszą być tablicami"
            )

        if (
            len(raw) != count or
            len(filtered) != count
        ):
            raise ValueError(
                "Liczba próbek nie zgadza się z count"
            )

        print(
            "\r"
            f"[ECG] "
            f"sample_start={sample_start} | "
            f"count={count} | "
            f"raw_last={raw[-1] if raw else 0} | "
            f"filtered_last={filtered[-1] if filtered else 0}      ",
            end="",
            flush=True,
        )


def main() -> None:
    collector = MqttCollector()

    try:
        collector.run()

    except KeyboardInterrupt:
        print("\nZatrzymywanie collectora...")

        collector.client.disconnect()

    except OSError as error:
        raise SystemExit(
            "Błąd połączenia sieciowego MQTT: "
            f"{error}"
        ) from error


if __name__ == "__main__":
    main()