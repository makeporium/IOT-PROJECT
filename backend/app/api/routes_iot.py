"""DOORSAFE Phase 4: IoT, ESP32 & MQTT Gateway REST API Endpoints."""

import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException

from app.iot.bridge import IoTBridge, get_iot_bridge
from app.iot.topics import (
    MQTTTopics,
    ActuatorCommandPayload,
    ActuatorStatePayload,
    HazardTelemetryPayload,
    TouchSensorPayload,
    RadarSensorPayload,
    HeartbeatPayload,
)

logger = logging.getLogger("doorsafe.api.iot")
router = APIRouter(prefix="/iot", tags=["Phase 4: IoT Controller, ESP32 & MQTT Gateway"])


class SimulateTouchRequest(BaseModel):
    """Payload to simulate an ESP32 capacitive touch interrupt."""
    touched: bool = Field(True, description="True when finger touches handle, False on release")
    objects: Optional[List[Dict[str, Any]]] = Field(None, description="Current observations visible to the passenger")


class SimulateRadarRequest(BaseModel):
    """Payload to simulate an ESP32 ultrasonic/radar range measurement."""
    distance_cm: float = Field(..., description="Range to obstacle in centimeters")
    relative_speed_mps: Optional[float] = Field(None, description="Closing velocity in m/s")


class TestPublishRequest(BaseModel):
    """Payload to publish an arbitrary test message over the MQTT automotive bus."""
    topic: str = Field(MQTTTopics.ACTUATOR_COMMAND.value, description="Target MQTT topic")
    payload: Dict[str, Any] = Field(default_factory=dict, description="JSON message payload")


class ObservationSyncRequest(BaseModel):
    timestamp_sec: float
    objects: List[Dict[str, Any]] = Field(default_factory=list)
    source_mode: str = "RECORDED_DEMO"


class ManualActuatorRequest(BaseModel):
    action: str = Field(..., pattern="^(LOCK|UNLOCK)$")


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
    result = bridge.simulate_esp32_touch_event(touched=req.touched, objects=req.objects)
    return result


@router.post("/simulate-radar")
def simulate_esp32_radar(
    req: SimulateRadarRequest,
    bridge: IoTBridge = Depends(get_iot_bridge)
):
    """
    Simulates an incoming radar / ultrasonic distance reading from the ESP32 rear sensor.
    """
    return bridge.simulate_radar_event(req.distance_cm, req.relative_speed_mps)


@router.post("/sync-observation")
def sync_observation(req: ObservationSyncRequest, bridge: IoTBridge = Depends(get_iot_bridge)):
    """Synchronizes the displayed frame with the authoritative IoT safety state."""
    result = bridge.process_observations(req.objects, req.timestamp_sec, req.source_mode)
    return {
        "safety_state": result.current_state.value,
        "servo_angle_deg": result.servo_angle_deg,
        "door_locked": result.is_door_locked,
        "min_ttc_seconds": result.min_ttc_seconds,
    }


@router.post("/manual-actuator")
def manual_actuator(req: ManualActuatorRequest, bridge: IoTBridge = Depends(get_iot_bridge)):
    return bridge.request_manual_actuator(req.action)


@router.post("/publish-test")
def publish_test_message(
    req: TestPublishRequest,
    bridge: IoTBridge = Depends(get_iot_bridge)
):
    """
    Publishes an arbitrary test payload to the specified MQTT topic.
    """
    validators = {
        MQTTTopics.ACTUATOR_COMMAND.value: ActuatorCommandPayload,
        MQTTTopics.ACTUATOR_STATE.value: ActuatorStatePayload,
        MQTTTopics.HAZARD_TELEMETRY.value: HazardTelemetryPayload,
        MQTTTopics.SENSOR_TOUCH.value: TouchSensorPayload,
        MQTTTopics.SENSOR_RADAR.value: RadarSensorPayload,
        MQTTTopics.SYSTEM_HEARTBEAT.value: HeartbeatPayload,
    }
    model = validators.get(req.topic)
    if model is None:
        raise HTTPException(status_code=400, detail="Topic is outside the DOORSAFE protocol allowlist")
    try:
        validated_payload = model.model_validate(req.payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid protocol payload: {exc}") from exc
    success = bridge.mqtt.publish(req.topic, validated_payload)
    return {"success": success, "topic": req.topic, "payload": validated_payload.model_dump()}
