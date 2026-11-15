"""DOORSAFE Phase 4: IoT Safety Bridge connecting Deterministic Engine to MQTT."""

import time
import logging
import uuid
from typing import Dict, Any, Optional

from app.iot.topics import (
    MQTTTopics,
    ActuatorCommandPayload,
    ActuatorStatePayload,
    HazardTelemetryPayload,
    TouchSensorPayload,
    RadarSensorPayload,
    HeartbeatPayload
)
from app.iot.mqtt_client import DoorSafeMQTTClient, get_mqtt_gateway
from app.safety_engine.manager import SafetyManager, get_safety_manager
from app.safety_engine.state_machine import (
    SafetyFrameResult,
    DoorHandleResponse,
    SafetyState,
    ActuatorCommand,
)
from app.persistence.event_store import get_event_store

logger = logging.getLogger("doorsafe.iot.bridge")

# The HC-SR04 has no lateral classification: it is a close-range, fixed-beam
# confirmation sensor. It therefore does not replace camera trajectory
# analysis, but blocks a handle request while a fresh object is in its beam.
RADAR_INTERLOCK_DISTANCE_M = 1.20
RADAR_FRESHNESS_S = 2.0


class IoTBridge:
    """
    Coordinates real-time bi-directional messaging between the Safety Decision Engine
    and connected ESP32 microcontrollers over the MQTT automotive bus.
    """

    def __init__(
        self,
        mqtt_client: Optional[DoorSafeMQTTClient] = None,
        safety_manager: Optional[SafetyManager] = None,
    ):
        self.mqtt = mqtt_client or get_mqtt_gateway()
        self.safety = safety_manager or get_safety_manager()
        self.events = get_event_store()

        # Telemetry tracking for connected hardware nodes
        self.last_esp32_heartbeat: Optional[Dict[str, Any]] = None
        self.last_radar_reading: Optional[Dict[str, Any]] = None
        self.last_actuator_feedback: Optional[Dict[str, Any]] = None
        self.last_actuator_command: Optional[Dict[str, Any]] = None
        self.last_touch_decision: Optional[Dict[str, Any]] = None
        self._command_sequence = 0
        self._session_id = str(uuid.uuid4())
        self._last_safety_command_signature: Optional[tuple] = None
        self._last_safety_command_time = 0.0
        self._controller_reported_offline = False
        self._last_recorded_safety_signature: Optional[tuple] = None

        self._register_subscribers()

    def _record_event(self, event_type: str, source: str, payload: Dict[str, Any], **state: Any) -> None:
        """Persistence must never interrupt the real-time safety path."""
        try:
            self.events.record(event_type, source, payload, **state)
        except Exception as exc:
            logger.warning("Flight recorder could not store %s: %s", event_type, exc)

    def _register_subscribers(self):
        """Attaches handlers to incoming sensor topics from the ESP32."""
        self.mqtt.register_callback(MQTTTopics.SENSOR_TOUCH.value, self._handle_incoming_touch)
        self.mqtt.register_callback(MQTTTopics.SENSOR_RADAR.value, self._handle_incoming_radar)
        self.mqtt.register_callback(MQTTTopics.SYSTEM_HEARTBEAT.value, self._handle_incoming_heartbeat)
        self.mqtt.register_callback(MQTTTopics.ACTUATOR_STATE.value, self._handle_actuator_state)

    def _publish_actuator_command(
        self,
        *,
        command: str,
        servo_angle_deg: int,
        door_locked: bool,
        chime_alert: bool,
        reason: str,
        source_mode: str,
        qos: int = 1,
    ) -> ActuatorCommandPayload:
        self._command_sequence += 1
        payload = ActuatorCommandPayload(
            timestamp=time.time(),
            command=command,
            servo_angle_deg=servo_angle_deg,
            door_locked=door_locked,
            chime_alert=chime_alert,
            reason=reason,
            command_id=str(uuid.uuid4()),
            session_id=self._session_id,
            sequence=self._command_sequence,
            ttl_ms=1500,
            source_mode=source_mode,
        )
        self.mqtt.publish(MQTTTopics.ACTUATOR_COMMAND.value, payload, qos=qos)
        self.last_actuator_command = payload.model_dump()
        self._record_event(
            "ACTUATOR_COMMAND",
            source_mode,
            self.last_actuator_command,
            door_locked=door_locked,
        )
        return payload

    def _handle_incoming_touch(self, payload: Dict[str, Any]):
        """
        Executes when the ESP32 capacitive door handle touch interrupt fires.
        Evaluates the current safety state and immediately publishes actuator decision.
        """
        try:
            touch = TouchSensorPayload.model_validate(payload)
        except Exception as exc:
            logger.warning("Rejected invalid ESP32 touch payload: %s", exc)
            return

        touched = touch.touched
        logger.info(f"ESP32 Capacitive Handle Touch Event: touched={touched}")

        if not touched:
            self.safety.is_handle_touch_active = False
            self._record_event("HANDLE_TOUCH", "ESP32", touch.model_dump())
            return

        # A fresh close ultrasonic echo is independent physical evidence in
        # the door-side beam. It has priority over a camera "clear" result.
        if self._radar_interlock_active():
            distance_m = self.last_radar_reading["distance_m"]
            command = self._publish_actuator_command(
                command=ActuatorCommand.LOCK_ENGAGED.value,
                servo_angle_deg=0,
                door_locked=True,
                chime_alert=True,
                reason=f"HC-SR04 proximity interlock: object at {distance_m:.2f} m",
                source_mode="LIVE_RADAR_INTERLOCK",
            )
            self.last_touch_decision = {
                "servo_angle_deg": 0,
                "door_locked": True,
                "message": f"DOOR LOCKED: HC-SR04 detected an object {distance_m:.2f} m from the door.",
                "command_id": command.command_id,
            }
            logger.warning("Door vetoed by fresh HC-SR04 echo at %.2f m", distance_m)
            self._record_event(
                "HANDLE_TOUCH",
                "ESP32",
                {**touch.model_dump(), **self.last_touch_decision},
                safety_state="RADAR_INTERLOCK",
                door_locked=True,
            )
            return

        # Deterministic camera/trajectory safety gate evaluation.
        handle_resp: DoorHandleResponse = self.safety.trigger_handle_touch(is_touched=touched)

        # Immediate Actuator Command Dispatch to ESP32
        command = self._publish_actuator_command(
            command=handle_resp.actuator_command.value,
            servo_angle_deg=handle_resp.servo_angle_deg,
            door_locked=handle_resp.lock_veto_active,
            chime_alert=handle_resp.chime_alert,
            reason=handle_resp.message,
            source_mode="LIVE_TOUCH",
        )
        self.last_touch_decision = {
            "servo_angle_deg": handle_resp.servo_angle_deg,
            "door_locked": handle_resp.lock_veto_active,
            "message": handle_resp.message,
            "command_id": command.command_id,
        }
        self._record_event(
            "HANDLE_TOUCH",
            "ESP32",
            {**touch.model_dump(), **self.last_touch_decision},
            safety_state=handle_resp.current_safety_state.value,
            door_locked=handle_resp.lock_veto_active,
        )

    def _handle_incoming_radar(self, payload: Dict[str, Any]):
        """Records physical ultrasonic or mmWave range readings from the ESP32."""
        try:
            radar = RadarSensorPayload.model_validate(payload)
        except Exception as exc:
            logger.warning("Rejected invalid radar payload: %s", exc)
            return
        self.last_radar_reading = {
            **radar.model_dump(),
            "received_at": time.time(),
            "interlock_active": radar.distance_m <= RADAR_INTERLOCK_DISTANCE_M,
        }
        self._record_event(
            "RADAR_READING",
            radar.device_id,
            self.last_radar_reading,
            door_locked=self._radar_interlock_active(),
        )
        logger.debug("ESP32 Radar measurement: %s cm", radar.distance_cm)

    def _radar_interlock_active(self) -> bool:
        """True only for a recent, close echo from the real ESP32 sensor."""
        if not self.last_radar_reading:
            return False
        age_s = time.time() - self.last_radar_reading.get("received_at", 0.0)
        return age_s <= RADAR_FRESHNESS_S and bool(self.last_radar_reading.get("interlock_active"))

    def _handle_incoming_heartbeat(self, payload: Dict[str, Any]):
        """Tracks the health and uptime of the ESP32 controller."""
        device_id = payload.get("device_id", "")
        # Only record foreign controller heartbeats (skip backend's own heartbeat)
        if "backend" not in device_id:
            self._controller_reported_offline = payload.get("status") == "OFFLINE"
            self.last_esp32_heartbeat = {
                "received_at": time.time(),
                "device_id": device_id,
                "status": payload.get("status", "ONLINE"),
                "uptime_seconds": payload.get("uptime_seconds", 0),
                "free_heap_bytes": payload.get("free_heap_bytes"),
                "firmware_version": payload.get("firmware_version", "unknown")
            }
            logger.info(f"ESP32 Heartbeat received from {device_id} (uptime: {payload.get('uptime_seconds')}s)")
            if payload.get("status") == "OFFLINE":
                self.safety.force_fail_safe("ESP32 heartbeat reported OFFLINE")
                self._publish_actuator_command(
                    command="FAIL_SAFE_LOCK",
                    servo_angle_deg=0,
                    door_locked=True,
                    chime_alert=True,
                    reason="Controller communication lost",
                    source_mode="FAIL_SAFE",
                )

    def _handle_actuator_state(self, payload: Dict[str, Any]):
        try:
            feedback = ActuatorStatePayload.model_validate(payload)
        except Exception as exc:
            logger.warning("Rejected invalid actuator feedback: %s", exc)
            return
        self.last_actuator_feedback = {
            **feedback.model_dump(),
            "received_at": time.time(),
        }
        self._record_event(
            "ACTUATOR_ACKNOWLEDGEMENT",
            feedback.device_id,
            self.last_actuator_feedback,
            door_locked=feedback.door_locked,
        )

    def sync_safety_state(self, frame_result: SafetyFrameResult, source_mode: str = "RECORDED_DEMO"):
        """
        Publishes real-time safety evaluation and servo position to the MQTT bus.
        Called on every processed video frame or sensor update.
        """
        # 1. Publish a physical veto only when video safety requires a lock.
        # A SAFE/WARNING frame means "unlatching may be requested"; it must not
        # autonomously drive the physical door latch to 90 degrees. Only a
        # deliberate touch event in _handle_incoming_touch may publish unlock.
        # This prevents command churn and, more importantly, prevents a safe
        # video frame from briefly overriding a currently locked door.
        if not frame_result.is_door_locked:
            self._last_safety_command_signature = None
        else:
            signature = (
                frame_result.actuator_command.value,
                frame_result.servo_angle_deg,
                frame_result.is_door_locked,
            )
            if signature != self._last_safety_command_signature:
                self._publish_actuator_command(
                    command=frame_result.actuator_command.value,
                    servo_angle_deg=frame_result.servo_angle_deg,
                    door_locked=True,
                    # Frame updates are silent. An actual blocked handle-touch
                    # request carries the one short audible veto alert.
                    chime_alert=False,
                    reason=f"State: {frame_result.current_state.value} (Min TTC: {frame_result.min_ttc_seconds or '--'}s)",
                    source_mode=source_mode,
                    qos=1,
                )
                self._last_safety_command_signature = signature

        safety_signature = (frame_result.current_state.value, frame_result.is_door_locked)
        if safety_signature != self._last_recorded_safety_signature:
            self._record_event(
                "SAFETY_STATE_CHANGE",
                source_mode,
                {
                    "previous_state": frame_result.previous_state.value,
                    "current_state": frame_result.current_state.value,
                    "threat_count": frame_result.threat_count,
                    "min_ttc_seconds": frame_result.min_ttc_seconds,
                    "actuator_command": frame_result.actuator_command.value,
                },
                safety_state=frame_result.current_state.value,
                door_locked=frame_result.is_door_locked,
            )
            self._last_recorded_safety_signature = safety_signature

        """Previous behaviour published SAFE as UNLOCK_PERMITTED every two
        seconds. It has intentionally been removed: safe is permission, not
        actuation. The code below publishes only hazard telemetry."""
        # 2. Publish Threat Telemetry
        crit_h = frame_result.critical_hazards[0] if frame_result.critical_hazards else None
        warn_h = frame_result.warning_hazards[0] if frame_result.warning_hazards else None
        primary = crit_h or warn_h

        hazard_payload = HazardTelemetryPayload(
            timestamp=time.time(),
            safety_state=frame_result.current_state.value,
            threat_count=frame_result.threat_count,
            min_ttc_seconds=frame_result.min_ttc_seconds,
            primary_hazard_id=primary.track_id if primary else None,
            primary_hazard_class=primary.class_name if primary else None,
            distance_m=primary.distance_m if primary else None,
            speed_kmh=primary.speed_kmh if primary else None
        )
        self.mqtt.publish(MQTTTopics.HAZARD_TELEMETRY.value, hazard_payload, qos=0)

    def process_observations(
        self,
        objects: list[Dict[str, Any]],
        timestamp_sec: float,
        source_mode: str = "RECORDED_DEMO",
    ) -> SafetyFrameResult:
        """Updates the one authoritative safety manager and synchronizes MQTT."""
        # The coordinator may receive video-relative, epoch, and controller-uptime
        # timestamps. Use one monotonic clock for safety hysteresis.
        result = self.safety.process_frame(objects=objects, timestamp_sec=time.monotonic())
        if self._controller_reported_offline:
            self.safety.force_fail_safe("ESP32 remains offline")
            result.current_state = SafetyState.FAIL_SAFE
            result.actuator_command = ActuatorCommand.FAIL_SAFE_LOCK
            result.servo_angle_deg = 0
            result.is_door_locked = True
            result.fail_safe_active = True
        self.sync_safety_state(result, source_mode=source_mode)
        return result

    def simulate_esp32_touch_event(
        self,
        touched: bool = True,
        objects: Optional[list[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Utility function to simulate an ESP32 hardware event through MQTT
        for testing, demonstrations, or when no physical hardware is plugged in.
        """
        payload = TouchSensorPayload(
            device_id="esp32_driver_door_sim",
            timestamp=time.time(),
            touched=touched,
            raw_capacitive_val=12 if touched else 85
        )
        if objects is not None:
            self.process_observations(objects, timestamp_sec=time.time(), source_mode="SIMULATION")
        self._handle_incoming_touch(payload.model_dump())
        decision = self.last_touch_decision or {}
        return {
            "status": "SIMULATED_TOUCH_DISPATCHED",
            "touched": touched,
            "safety_state": self.safety.state_machine.state.value,
            "servo_angle_deg": decision.get("servo_angle_deg", self.safety.state_machine.servo_angle_deg),
            "door_locked": decision.get("door_locked", bool(self.safety.state_machine.servo_angle_deg == 0)),
            "message": decision.get("message", "Touch released"),
        }

    def simulate_radar_event(self, distance_cm: float, relative_speed_mps: Optional[float]) -> Dict[str, Any]:
        """Feeds a physical-proximity echo into the same MQTT hardware path."""
        distance_m = round(distance_cm / 100.0, 3)
        radar_payload = RadarSensorPayload(
            device_id="esp32_rear_radar_sim",
            timestamp=time.time(),
            distance_cm=distance_cm,
            distance_m=distance_m,
            relative_speed_mps=relative_speed_mps,
        )
        self._handle_incoming_radar(radar_payload.model_dump())
        self.mqtt.publish(MQTTTopics.SENSOR_RADAR.value, radar_payload, qos=1)

        return {
            "status": "RADAR_TELEMETRY_PROCESSED",
            "data": radar_payload.model_dump(),
            "hardware_interlock_active": self._radar_interlock_active(),
            "interlock_distance_m": RADAR_INTERLOCK_DISTANCE_M,
        }

    def request_manual_actuator(self, action: str) -> Dict[str, Any]:
        """Allows locking at any time; unlock requests remain safety-gated."""
        if action == "LOCK":
            payload = self._publish_actuator_command(
                command="LOCK_ENGAGED",
                servo_angle_deg=0,
                door_locked=True,
                chime_alert=False,
                reason="Manual diagnostic lock request",
                source_mode="SIMULATION",
            )
            return {"accepted": True, "door_locked": True, "servo_angle_deg": 0, "command_id": payload.command_id}

        # Diagnostic unlock uses the exact same camera + HC-SR04 safety gate
        # as a physical handle touch; it must never bypass the hardware veto.
        decision = self.simulate_esp32_touch_event(touched=True)
        return {
            "accepted": not decision["door_locked"],
            "door_locked": decision["door_locked"],
            "servo_angle_deg": decision["servo_angle_deg"],
            "command_id": self.last_actuator_command["command_id"] if self.last_actuator_command else None,
            "message": decision["message"],
        }

    def get_hardware_status(self) -> Dict[str, Any]:
        """Returns consolidated IoT gateway and ESP32 hardware status."""
        mqtt_status = self.mqtt.get_status()
        esp32_online = False
        if self.last_esp32_heartbeat:
            elapsed = time.time() - self.last_esp32_heartbeat.get("received_at", 0)
            esp32_online = elapsed < 30.0  # Online if heartbeat received in last 30s

        return {
            "mqtt_gateway": mqtt_status,
            "esp32_hardware": {
                "online": esp32_online,
                "last_heartbeat": self.last_esp32_heartbeat,
                "last_radar_reading": self.last_radar_reading
            },
            "actuator_feedback": self.last_actuator_feedback,
            "requested_actuator": self.last_actuator_command,
            "safety": self.safety.get_status(),
        }


# Global singleton instance
_iot_bridge: Optional[IoTBridge] = None


def get_iot_bridge() -> IoTBridge:
    """Returns singleton IoTBridge instance."""
    global _iot_bridge
    if _iot_bridge is None:
        _iot_bridge = IoTBridge()
    return _iot_bridge
