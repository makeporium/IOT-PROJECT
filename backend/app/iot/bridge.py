"""DOORSAFE Phase 4: IoT Safety Bridge connecting Deterministic Engine to MQTT."""

import time
import logging
from typing import Dict, Any, Optional

from app.iot.topics import (
    MQTTTopics,
    ActuatorCommandPayload,
    HazardTelemetryPayload,
    TouchSensorPayload,
    HeartbeatPayload
)
from app.iot.mqtt_client import DoorSafeMQTTClient, get_mqtt_gateway
from app.safety_engine.manager import SafetyManager, get_safety_manager
from app.safety_engine.state_machine import SafetyFrameResult, DoorHandleResponse

logger = logging.getLogger("doorsafe.iot.bridge")


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

        # Telemetry tracking for connected hardware nodes
        self.last_esp32_heartbeat: Optional[Dict[str, Any]] = None
        self.last_radar_reading: Optional[Dict[str, Any]] = None

        self._register_subscribers()

    def _register_subscribers(self):
        """Attaches handlers to incoming sensor topics from the ESP32."""
        self.mqtt.register_callback(MQTTTopics.SENSOR_TOUCH.value, self._handle_incoming_touch)
        self.mqtt.register_callback(MQTTTopics.SENSOR_RADAR.value, self._handle_incoming_radar)
        self.mqtt.register_callback(MQTTTopics.SYSTEM_HEARTBEAT.value, self._handle_incoming_heartbeat)

    def _handle_incoming_touch(self, payload: Dict[str, Any]):
        """
        Executes when the ESP32 capacitive door handle touch interrupt fires.
        Evaluates the current safety state and immediately publishes actuator decision.
        """
        touched = payload.get("touched", True)
        logger.info(f"ESP32 Capacitive Handle Touch Event: touched={touched}")

        # Deterministic Safety Gate Evaluation
        handle_resp: DoorHandleResponse = self.safety.trigger_handle_touch(is_touched=touched)

        # Immediate Actuator Command Dispatch to ESP32
        actuator_msg = ActuatorCommandPayload(
            timestamp=time.time(),
            command=handle_resp.actuator_command.value,
            servo_angle_deg=handle_resp.servo_angle_deg,
            door_locked=handle_resp.lock_veto_active,
            chime_alert=handle_resp.chime_alert,
            reason=handle_resp.message
        )
        self.mqtt.publish(MQTTTopics.ACTUATOR_COMMAND.value, actuator_msg, qos=1)

    def _handle_incoming_radar(self, payload: Dict[str, Any]):
        """Records physical ultrasonic or mmWave range readings from the ESP32."""
        self.last_radar_reading = payload
        logger.debug(f"ESP32 Radar measurement: {payload.get('distance_cm')} cm")

    def _handle_incoming_heartbeat(self, payload: Dict[str, Any]):
        """Tracks the health and uptime of the ESP32 controller."""
        device_id = payload.get("device_id", "")
        # Only record foreign controller heartbeats (skip backend's own heartbeat)
        if "backend" not in device_id:
            self.last_esp32_heartbeat = {
                "received_at": time.time(),
                "device_id": device_id,
                "status": payload.get("status", "ONLINE"),
                "uptime_seconds": payload.get("uptime_seconds", 0),
                "free_heap_bytes": payload.get("free_heap_bytes"),
                "firmware_version": payload.get("firmware_version", "unknown")
            }
            logger.info(f"ESP32 Heartbeat received from {device_id} (uptime: {payload.get('uptime_seconds')}s)")

    def sync_safety_state(self, frame_result: SafetyFrameResult):
        """
        Publishes real-time safety evaluation and servo position to the MQTT bus.
        Called on every processed video frame or sensor update.
        """
        # 1. Publish Actuator Command
        actuator_payload = ActuatorCommandPayload(
            timestamp=time.time(),
            command=frame_result.actuator_command.value,
            servo_angle_deg=frame_result.servo_angle_deg,
            door_locked=frame_result.is_door_locked,
            chime_alert=frame_result.current_state.value in ("WARNING", "CRITICAL"),
            reason=f"State: {frame_result.current_state.value} (Min TTC: {frame_result.min_ttc_seconds or '--'}s)"
        )
        self.mqtt.publish(MQTTTopics.ACTUATOR_COMMAND.value, actuator_payload, qos=0)

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

    def simulate_esp32_touch_event(self, touched: bool = True) -> Dict[str, Any]:
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
        # Directly trigger touch handler
        self._handle_incoming_touch(payload.model_dump())
        return {
            "status": "SIMULATED_TOUCH_DISPATCHED",
            "touched": touched,
            "safety_state": self.safety.state_machine.state.value,
            "servo_angle_deg": self.safety.state_machine.servo_angle_deg
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
            }
        }


# Global singleton instance
_iot_bridge: Optional[IoTBridge] = None


def get_iot_bridge() -> IoTBridge:
    """Returns singleton IoTBridge instance."""
    global _iot_bridge
    if _iot_bridge is None:
        _iot_bridge = IoTBridge()
    return _iot_bridge
