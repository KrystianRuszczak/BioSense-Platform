from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from shared.models import ecg_batch_message, gsr_batch_message, event_message, metrics_message, status_message, telemetry_message
from shared.topics import ECG_TOPIC, GSR_TOPIC, EVENTS_TOPIC, METRICS_TOPIC, STATUS_TOPIC

class MessageParseError(ValueError):
    pass

def _reject_nonstandard_number(value: str) -> None:
    raise MessageParseError(f"Invalid JSON numeric value: {value}")

class MessageParser:
    SUPPORTED_PROTOCOL_VERSION = 1

    MAX_ECG_BATCH_SIZE = 250
    MAX_SAMPLE_RATE = 2000

    MAX_GSR_BATCH_SIZE = 100
    MAX_GSR_SAMPLE_RATE = 1000

    def __init__(self, device_id: str) -> None:
        if not device_id:
            raise ValueError("device_id can not be empty")

        self._device_id = device_id

        self._status_topic = STATUS_TOPIC.format(device_id=device_id)
        self._metrics_topic = METRICS_TOPIC.format(device_id=device_id)
        self._events_topic = EVENTS_TOPIC.format(device_id=device_id)
        self._ecg_topic = ECG_TOPIC.format(device_id=device_id)
        self._gsr_topic = GSR_TOPIC.format(device_id=device_id)

    def parse(self, topic: str, payload: bytes, received_at_utc: datetime | None = None,) -> telemetry_message:
        data = self._decode_payload(payload)

        received_at = self._normalize_timestamp(received_at_utc)

        if topic == self._status_topic:
            return self._parse_status(data, received_at,)

        if topic == self._metrics_topic:
            return self._parse_metrics(data, received_at,)

        if topic == self._events_topic:
            return self._parse_event(data, received_at,)

        if topic == self._ecg_topic:
            return self._parse_ecg(data, received_at,)

        if topic == self._gsr_topic:
            return self._parse_gsr(data, received_at,)

        raise MessageParseError(f"Unsupported MQTT topic {topic}")

    def _parse_status(self, data: dict[str, Any], received_at: datetime,) -> status_message:
        return status_message(received_at_utc=received_at, device=self._device_id, online=self._require_bool(data, "online",),)

    def _parse_metrics(self, data: dict[str, Any], received_at: datetime,) -> metrics_message:
        protocol_version = (self._require_protocol_version(data))

        device = self._require_device(data)

        return metrics_message(
            received_at_utc=received_at, 
            protocol_version=protocol_version, 
            device=device,
            sample=self._require_int(data, "sample", minimum=0,),
            ecg_bpm = self._require_int(data, "ecg_bpm", minimum=0,),
            leads_off=self._require_bool(data, "leads_off",),
            gsr=self._require_int(data, "gsr", minimum=0, maximum=4095),
            gsr_resistance_ohm=self._require_float(data, "gsr_resistance_ohm"),
            gsr_conductance_us=self._require_float(data, "gsr_conductance_us"),
            gsr_valid=self._require_bool(data, "gsr_valid"),
            gsr_calibration_raw=self._require_int(data, "gsr_calibration_raw", minimum=0, maximum=4095),
            batt_v=self._require_float(data, "batt_v",),
            batt_percent=self._require_int(data, "batt_percent", minimum=0, maximum=100,),
            temp_amb=self._require_float(data, "temp_amb",),
            temp_obj=self._require_float(data, "temp_obj",),
            max_bpm=self._require_int(data, "max_bpm", minimum=0,),
            spo2=self._require_float(data, "spo2",),
            spo2_valid=self._require_bool(data, "spo2_valid",),
            max_finger=self._require_bool(data, "max_finger",),
            ui_flags=self._require_int(data, "ui_flags", minimum=0, maximum=255,),
            )

    def _parse_event(self, data: dict[str, Any], received_at: datetime,) -> event_message:

        protocol_version = (self._require_protocol_version(data))
        device = self._require_device(data)

        return event_message(
            received_at_utc=received_at,
            protocol_version=protocol_version,
            device=device,

            sample=self._require_int(data, "sample", minimum=0,),
            event=self._require_int(data, "event", minimum=0, maximum=255,),
            ui_flags=self._require_int(data, "ui_flags", minimum=0, maximum=255,),
        )

    def _parse_ecg(self, data: dict[str, Any], received_at: datetime) -> ecg_batch_message:
        protocol_version = (self._require_protocol_version(data))
        device = self._require_device(data)

        sample_start = self._require_int(data, "sample_start", minimum=0)
        sample_rate = self._require_int(data, "sample_rate", minimum=1, maximum=self.MAX_SAMPLE_RATE)
        count = self._require_int(data, "count", minimum=1, maximum=self.MAX_ECG_BATCH_SIZE,)
        raw = self._require_int_tuple(data, "raw", expected_count=count,)
        filtered = self._require_int_tuple(data, "filtered", expected_count=count)
        rpeak = self._require_bool_tuple(data, "rpeak", expected_count=count,)
        leads_off = self._require_bool_tuple(data, "leads_off", expected_count=count,)

        return ecg_batch_message(
            received_at_utc=received_at,
            protocol_version=protocol_version,
            device=device,
            sample_start=sample_start,
            sample_rate=sample_rate,
            raw=raw,
            filtered=filtered,
            rpeak=rpeak,
            leads_off=leads_off,
        )

    def _parse_gsr(self, data: dict[str, Any], received_at: datetime) -> gsr_batch_message:
        protocol_version = (self._require_protocol_version(data))
        device = self._require_device(data)

        sample_start = self._require_int(data, "sample_start", minimum=0)
        sample_step = self._require_int(data, "sample_step", minimum=1)
        sample_rate = self._require_int(data, "sample_rate", minimum=1, maximum=self.MAX_GSR_SAMPLE_RATE)
        count = self._require_int(data, "count", minimum=1, maximum=self.MAX_GSR_BATCH_SIZE,)
        calibration_raw = self._require_int(data, "calibration_raw", minimum=1, maximum=4095)
        raw = self._require_int_tuple(data, "raw", expected_count=count, minimum=1, maximum=4095)
        resistance_ohm = self._require_float_tuple(data, "resistance_ohm", expected_count=count, minimum=0.0)
        conductance_us = self._require_float_tuple(data, "conductance_us", expected_count=count, minimum=0.0)
        valid = self._require_bool_tuple(data, "valid", expected_count=count)

        for i in range(count):
            if valid[i] and (resistance_ohm[i] <= 0.0 or conductance_us[i] <= 0.0):
                raise MessageParseError(f"Valid GSR samples must have positive resistance and conductance: index={i}")

        return gsr_batch_message(
            received_at_utc=received_at,
            protocol_version=protocol_version,
            device=device,
            sample_start=sample_start,
            sample_step=sample_step,
            sample_rate=sample_rate,
            calibration_raw=calibration_raw,
            raw=raw,
            resistance_ohm=resistance_ohm,
            conductance_us=conductance_us,
            valid=valid,
        )

    def _require_protocol_version(self, data: dict[str, Any]) -> int:
        version = self._require_int(data, "v", minimum=0)

        if version != self.SUPPORTED_PROTOCOL_VERSION:
            raise MessageParseError(f"Unsupported protocol version: {version}")

        return version

    def _require_device(self, data: dict[str, Any]) -> str:
        device = self._require_string(data, "device",)

        if device != self._device_id:
            raise MessageParseError(f"Device ID in the payload does not match the topic: {device!r} != {self._device_id!r}")

        return device

    @staticmethod
    def _decode_payload(payload: bytes,) -> dict[str, Any]:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MessageParseError("The payload is not valid UTF-8") from error

        if not text:
            raise MessageParseError("The MQTT payload is empty")

        try:
            data = json.loads(text, parse_constant=_reject_nonstandard_number)
        except json.JSONDecodeError as error:
            raise MessageParseError(f"The payload is not valid JSON: {error.msg}") from error

        if not isinstance(data, dict):
            raise MessageParseError("The root JSON element must be an object")

        return data

    @staticmethod
    def _normalize_timestamp(received_at_utc: datetime | None,) -> datetime:
        if received_at_utc is None:
            return datetime.now(timezone.utc)

        if received_at_utc.tzinfo is  None:
            raise ValueError("received_at_utc must include a time zone")

        return received_at_utc.astimezone(timezone.utc)

    @staticmethod
    def _required(data: dict[str, Any], field: str) -> Any:
        if field not in data:
            raise MessageParseError(f"Missing required field: {field}")

        return data[field]

    @classmethod
    def _require_int(cls, data: dict[str, Any], field: str, minimum: int | None = None, maximum: int | None = None) -> int:
        value = cls._required(data, field)

        return cls._validate_int(value, field, minimum, maximum)

    @staticmethod
    def _validate_int(value: Any, field: str, minimum: int | None = None, maximum: int | None = None) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise MessageParseError(f"The field {field} must be an integer")

        if minimum is not None and value < minimum:
            raise MessageParseError(f"The field {field} cannot be smaller than {minimum}")

        if maximum is not None and value > maximum:
            raise MessageParseError(f"Field {field} cannot be bigger than {maximum}")

        return value

    @classmethod
    def _require_float(cls, data: dict[str, Any], field: str, minimum: float | None = None, maximum: float | None = None) -> float:
        value = cls._required(data, field)

        return cls._validate_float(value, field, minimum, maximum)

    @staticmethod
    def _validate_float(value: Any, field: str, minimum: float | None = None, maximum: float | None = None):

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MessageParseError(f"The field {field} must be a number")

        result = float(value)

        if not math.isfinite(result):
            raise MessageParseError(f"The field {field} must be a finite number")

        if minimum is not None and result < minimum:
            raise MessageParseError(f"The field {field} cannot be smaller than {minimum}")

        if maximum is not None and result > maximum:
            raise MessageParseError(f"The field {field} cannot be bigger than {maximum}")

        return result

    @classmethod
    def _require_string(cls, data: dict[str, Any], field: str,) -> str:
        value = cls._required(data, field)

        if not isinstance(value, str) or not value:
            raise MessageParseError(f"The field {field} must be a non-empty string")

        return value

    @classmethod
    def _require_bool(cls, data: dict[str, Any], field: str,) -> bool:
        value = cls._required(data, field)

        return cls._validate_bool(value, field)

    @staticmethod
    def _validate_bool(value: Any, field: str,) -> bool:
        if isinstance(value, bool):
            return value

        if (isinstance(value, int) and not isinstance(value, bool) and value in (0, 1)):
            return bool(value)

        raise MessageParseError(f"The field {field} must have value true/false or 1/0")

    @classmethod
    def _require_int_tuple(cls, data: dict[str, Any], field: str, expected_count: int, minimum: int | None = None, maximum: int | None = None) -> tuple[int, ...]:
        values = cls._required(data=data, field=field)

        if not isinstance(values, list):
            raise MessageParseError(f"The field {field} must be an array")

        if len(values) != expected_count:
            raise MessageParseError(f"The field {field} contains {len(values)} elements, {expected_count} were expected")

        result: list[int] = []

        for index, value in enumerate(values):
            result.append(cls._validate_int(value, f"{field}[{index}]", minimum, maximum))

        return tuple(result)

    @classmethod
    def _require_bool_tuple(cls, data: dict[str, Any], field: str, expected_count: int) -> tuple[bool, ...]:
        values = cls._required(data, field)

        if not isinstance(values, list):
            raise MessageParseError(f"The field {field} must be an array")

        if len(values) != expected_count:
            raise MessageParseError(f"The field {field} contains {len(values)} elements, expected {expected_count}")

        result: list[bool] = []

        for index, value in enumerate(values):
            result.append(cls._validate_bool(value, f"{field}[{index}]",))

        return tuple(result)

    @classmethod
    def _require_float_tuple(cls, data: dict[str, Any], field: str, expected_count: int, minimum: float | None = None, maximum: float | None = None) -> tuple[float, ...]:
        values = cls._required(data=data, field=field)

        if not isinstance(values, list):
            raise MessageParseError(f"The field {field} must be an array")

        if len(values) != expected_count:
            raise MessageParseError(f"The field {field} contains {len(values)} elements, {expected_count} were expected")

        result: list[float] = []

        for index, value in enumerate(values):
            result.append(cls._validate_float(value, f"{field}[{index}]", minimum, maximum))

        return tuple(result)