"""DOORSAFE Phase 4: MQTT Topic Definitions & Structured IoT Message Schemas."""

from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class MQTTTopics(str, Enum):
    """Standardized MQTT topic hierarchy for automotive door safety."""
    HAZARD_TELEMETRY = "doorsafe/telemetry/hazard"
    ACTUATOR_COMMAND = "doorsafe/actuator/command"
    SENSOR_TOUCH = "doorsafe/sensor/touch"
    SENSOR_RADAR = "doorsafe/sensor/radar"
    SYSTEM_HEARTBEAT = "doorsafe/system/heartbeat"


class ActuatorCommandPayload(BaseModel):
    """Payload dispatched to ESP32 to position the physical SG90 door latch servo."""
    timestamp: float = Field(..., description="UNIX epoch or system timestamp (seconds)")
    command: str = Field(..., description="LOCK_ENGAGED, UNLOCK_PERMITTED, FAIL_SAFE_LOCK, HAPTIC_WARNING")
    servo_angle_deg: int = Field(..., description="Target servo horn angle: 0 (locked) or 90 (unlatched)")
    door_locked: bool = Field(..., description="True if door latch is physically blocked by safety veto")
    chime_alert: bool = Field(False, description="True if acoustic chime / buzzer should sound")
    reason: str = Field("Nominal operation", description="Human-readable decision cause")


class HazardTelemetryPayload(BaseModel):
    """Payload broadcasting current road danger metrics to connected ECUs."""
    timestamp: float = Field(..., description="Telemetry timestamp (seconds)")
    safety_state: str = Field(..., description="SAFE, WARNING, CRITICAL, or FAIL_SAFE")
    threat_count: int = Field(0, description="Total active obstacles threatening the vehicle")
    min_ttc_seconds: Optional[float] = Field(None, description="Shortest Time-To-Collision in seconds")
    primary_hazard_id: Optional[int] = Field(None, description="Tracking ID of the most critical obstacle")
    primary_hazard_class: Optional[str] = Field(None, description="Class name: bicycle, motorcycle, etc.")
    distance_m: Optional[float] = Field(None, description="Distance in meters")
    speed_kmh: Optional[float] = Field(None, description="Closing speed in km/h")


class TouchSensorPayload(BaseModel):
    """Payload received from ESP32 capacitive door handle touch pin."""
    device_id: str = Field("esp32_driver_door", description="Unique identifier of the door handle ECU")
    timestamp: float = Field(..., description="Timestamp of capacitive contact")
    touched: bool = Field(..., description="True if passenger touches handle, False on release")
    raw_capacitive_val: Optional[int] = Field(None, description="Raw ADC/capacitive reading from ESP32 pin")


class RadarSensorPayload(BaseModel):
    """Payload received from ESP32 ultrasonic / virtual mmWave radar sensor."""
    device_id: str = Field("esp32_rear_radar", description="Radar sensor ECU identifier")
    timestamp: float = Field(..., description="Sensor sample timestamp")
    distance_cm: float = Field(..., description="Measured distance in centimeters")
    distance_m: float = Field(..., description="Distance converted to meters")
    relative_speed_mps: Optional[float] = Field(None, description="Estimated range rate (m/s)")


class HeartbeatPayload(BaseModel):
    """Health monitor heartbeat periodically emitted by ESP32 or Backend."""
    device_id: str = Field(..., description="Originating node identifier")
    timestamp: float = Field(..., description="Heartbeat emission timestamp")
    status: str = Field("ONLINE", description="ONLINE, OFFLINE, or DEGRADED")
    uptime_seconds: int = Field(0, description="Seconds since controller boot")
    free_heap_bytes: Optional[int] = Field(None, description="ESP32 internal free RAM in bytes")
    firmware_version: str = Field("1.0.0", description="Embedded firmware semver")
