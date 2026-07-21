import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class mqtt_config:
    broker: str
    port: int
    username: str
    password: str
    device_id: str

def load_mqtt_config() -> mqtt_config:
    required = {
        "MQTT_BROKER": os.getenv("MQTT_BROKER"),
        "MQTT_USERNAME": os.getenv("MQTT_USERNAME"),
        "MQTT_PASSWORD": os.getenv("MQTT_PASSWORD"),
        "MQTT_DEVICE_ID": os.getenv("MQTT_DEVICE_ID"),
    }

    missing = [
        name
        for name, value in required.items()
        if not value
    ]

    if missing:
        raise RuntimeError("Missing required variables in .env" + ", ".join(missing))
    
    return mqtt_config(
        broker=required["MQTT_BROKER"],
        port=int(os.getenv("MQTT_PORT", "8883")),
        username=required["MQTT_USERNAME"],
        password=required["MQTT_PASSWORD"],
        device_id=required["MQTT_DEVICE_ID"],
    )