"""DOORSAFE Phase 4: IoT, ESP32 & MQTT Gateway REST API Endpoints."""

import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends

from app.iot.bridge import IoTBridge, get_iot_bridge
from app.iot.topics import MQTTTopics

logger = logging.getLogger("doorsafe.api.iot")
router = APIRouter(prefix="/iot", tags=["Phase 4: IoT Controller, ESP32 & MQTT Gateway"])


class SimulateTouchRequest(BaseModel):
    """Payload to simulate an ESP32 capacitive touch interrupt."""
    touched: bool = Field(True, description="True when finger touches handle, False on release")


class SimulateRadarRequest(BaseModel):
    """Payload to simulate an ESP32 ultrasonic/radar range measurement."""
    distance_cm: float = Field(..., description="Range to obstacle in centimeters")
    relative_speed_mps: Optional[float] = Field(None, description="Closing velocity in m/s")


class TestPublishRequest(BaseModel):
    """Payload to publish an arbitrary test message over the MQTT automotive bus."""
    topic: str = Field(MQTTTopics.ACTUATOR_COMMAND.value, description="Target MQTT topic")
    payload: Dict[str, Any] = Field(default_factory=dict, description="JSON message payload")


@router.get("/status")
def get_iot_status(bridge: IoTBridge = Depends(get_iot_bridge)):
    """
    Returns live MQTT gateway connection metrics, broker host/port,
    throughput statistics, recent messages, and ESP32 hardware node status.
    """
    return bridge.get_hardware_status()


@router.post("/simulate-touch")
def simulate_esp32_touch(
    req: SimulateTouchRequest,
    bridge: IoTBridge = Depends(get_iot_bridge)
):
    """
    Simulates an incoming capacitive touch event from the ESP32 microcontroller.
    Triggers the deterministic safety engine and dispatches the actuator servo command.
    """
    result = bridge.simulate_esp32_touch_event(touched=req.touched)
    return result


@router.post("/simulate-radar")
def simulate_esp32_radar(
    req: SimulateRadarRequest,
    bridge: IoTBridge = Depends(get_iot_bridge)
):
    """
    Simulates an incoming radar / ultrasonic distance reading from the ESP32 rear sensor.
    """
    payload = {
        "device_id": "esp32_rear_radar_sim",
        "distance_cm": req.distance_cm,
        "distance_m": round(req.distance_cm / 100.0, 2),
        "relative_speed_mps": req.relative_speed_mps
    }
    bridge.mqtt.publish(MQTTTopics.SENSOR_RADAR.value, payload)
    return {"status": "RADAR_TELEMETRY_PUBLISHED", "data": payload}


@router.post("/publish-test")
def publish_test_message(
    req: TestPublishRequest,
    bridge: IoTBridge = Depends(get_iot_bridge)
):
    """
    Publishes an arbitrary test payload to the specified MQTT topic.
    """
    success = bridge.mqtt.publish(req.topic, req.payload)
    return {"success": success, "topic": req.topic, "payload": req.payload}
