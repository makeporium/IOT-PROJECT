"""DOORSAFE Phase 4: IoT, ESP32, Wokwi & MQTT Unit and Integration Test Suite."""

import time
import pytest
from starlette.testclient import TestClient

from app.iot.topics import (
    MQTTTopics,
    ActuatorCommandPayload,
    ActuatorStatePayload,
    HazardTelemetryPayload,
    TouchSensorPayload,
    RadarSensorPayload,
    HeartbeatPayload
)
from app.iot.mqtt_client import DoorSafeMQTTClient
from app.iot.bridge import IoTBridge
from app.safety_engine.manager import SafetyManager
from app.safety_engine.state_machine import SafetyStateMachine, SafetyState, ActuatorCommand, SafetyFrameResult
from backend.main import app

client = TestClient(app)


def test_mqtt_topic_payload_schemas():
    """Verifies that all Pydantic schemas serialize and validate accurately."""
    # 1. Actuator Command
    cmd = ActuatorCommandPayload(
        timestamp=time.time(),
        command="LOCK_ENGAGED",
        servo_angle_deg=0,
        door_locked=True,
        chime_alert=True,
        reason="Imminent cyclist collision",
        command_id="test-command-1",
        session_id="test-session",
        sequence=1,
    )
    assert cmd.servo_angle_deg == 0
    assert cmd.door_locked is True
    json_str = cmd.model_dump_json()
    assert "LOCK_ENGAGED" in json_str

    # 2. Hazard Telemetry
    haz = HazardTelemetryPayload(
        timestamp=time.time(),
        safety_state="CRITICAL",
        threat_count=1,
        min_ttc_seconds=0.75,
        primary_hazard_id=42,
        primary_hazard_class="motorcycle",
        distance_m=3.5,
        speed_kmh=22.0
    )
    assert haz.threat_count == 1
    assert haz.min_ttc_seconds == 0.75

    # 3. Touch Sensor
    touch = TouchSensorPayload(
        device_id="esp32_driver_door",
        timestamp=time.time(),
        touched=True,
        raw_capacitive_val=14
    )
    assert touch.touched is True
    assert touch.device_id == "esp32_driver_door"

    # 4. Radar Sensor
    radar = RadarSensorPayload(
        device_id="esp32_rear_radar",
        timestamp=time.time(),
        distance_cm=185.0,
        distance_m=1.85,
        relative_speed_mps=-4.2
    )
    assert radar.distance_m == 1.85

    # 5. Heartbeat
    hb = HeartbeatPayload(
        device_id="esp32_driver_door",
        timestamp=time.time(),
        status="ONLINE",
        uptime_seconds=300,
        free_heap_bytes=245000,
        firmware_version="1.0.0"
    )
    assert hb.status == "ONLINE"
    assert hb.free_heap_bytes == 245000


def test_mqtt_client_buffer_and_publish():
    """Verifies MQTT gateway message logging and status introspection."""
    mqtt_client = DoorSafeMQTTClient(client_id="test_suite_client")
    
    # Test publishing when offline (should buffer cleanly in ring buffer)
    success = mqtt_client.publish(
        topic=MQTTTopics.ACTUATOR_COMMAND.value,
        payload={"command": "TEST_COMMAND", "servo_angle_deg": 90}
    )
    
    status = mqtt_client.get_status()
    assert "is_connected" in status
    assert len(status["recent_messages"]) > 0
    assert status["recent_messages"][-1]["topic"] == MQTTTopics.ACTUATOR_COMMAND.value


def test_actuator_feedback_updates_verified_hardware_state():
    """The dashboard must use ESP32 acknowledgement rather than command animation alone."""
    mqtt_client = DoorSafeMQTTClient(client_id="test_feedback_client")
    bridge = IoTBridge(mqtt_client=mqtt_client, safety_manager=SafetyManager())
    feedback = ActuatorStatePayload(
        device_id="esp32_driver_door",
        timestamp=12.5,
        command_id="command-42",
        session_id="test-session",
        sequence=42,
        requested_angle_deg=0,
        applied_angle_deg=0,
        door_locked=True,
        status="APPLIED",
    )

    bridge._handle_actuator_state(feedback.model_dump())

    status = bridge.get_hardware_status()
    assert status["actuator_feedback"]["command_id"] == "command-42"
    assert status["actuator_feedback"]["door_locked"] is True


def test_offline_heartbeat_forces_fail_safe_lock():
    mqtt_client = DoorSafeMQTTClient(client_id="test_fail_safe_client")
    safety_mgr = SafetyManager()
    bridge = IoTBridge(mqtt_client=mqtt_client, safety_manager=safety_mgr)

    bridge._handle_incoming_heartbeat({
        "device_id": "esp32_driver_door",
        "status": "OFFLINE",
        "uptime_seconds": 10,
    })

    assert safety_mgr.state_machine.state == SafetyState.FAIL_SAFE
    assert safety_mgr.state_machine.servo_angle_deg == 0
    assert mqtt_client.recent_messages[-1]["payload"]["command"] == "FAIL_SAFE_LOCK"


def test_iot_bridge_safety_sync():
    """Verifies that IoTBridge broadcasts actuator commands upon safety evaluation."""
    mqtt_client = DoorSafeMQTTClient(client_id="test_bridge_client")
    safety_mgr = SafetyManager()
    bridge = IoTBridge(mqtt_client=mqtt_client, safety_manager=safety_mgr)

    # Frame result with hazard
    frame_res = SafetyFrameResult(
        timestamp_sec=1.5,
        current_state=SafetyState.CRITICAL,
        previous_state=SafetyState.SAFE,
        actuator_command=ActuatorCommand.LOCK_ENGAGED,
        servo_angle_deg=0,
        is_door_locked=True,
        threat_count=1,
        critical_hazards=[],
        warning_hazards=[],
        min_ttc_seconds=0.6,
        time_in_current_state_s=0.1,
        fail_safe_active=False
    )

    bridge.sync_safety_state(frame_res)

    # Verify message was logged to buffer
    recent = bridge.mqtt.recent_messages
    assert len(recent) >= 2
    topics = [m["topic"] for m in recent]
    assert MQTTTopics.ACTUATOR_COMMAND.value in topics
    assert MQTTTopics.HAZARD_TELEMETRY.value in topics


def test_esp32_touch_triggers_immediate_veto_when_hazard_present():
    """Verifies that an incoming ESP32 touch event is vetoed when hazard is closing."""
    mqtt_client = DoorSafeMQTTClient(client_id="test_touch_veto")
    safety_mgr = SafetyManager()
    bridge = IoTBridge(mqtt_client=mqtt_client, safety_manager=safety_mgr)

    # Place hazard into safety manager state machine
    crit_obj = {
        "track_id": 99,
        "class_name": "bicycle",
        "metric_x": -1.2,
        "metric_y": -2.0,
        "distance_m": 2.3,
        "velocity_y_mps": 5.0,
        "speed_kmh": 18.0,
        "ttc_seconds": 0.4,
        "is_approaching": True
    }
    safety_mgr.process_frame([crit_obj], timestamp_sec=1.0)
    assert safety_mgr.state_machine.state == SafetyState.CRITICAL

    # Simulate ESP32 touch message
    touch_payload = {
        "device_id": "esp32_driver_door",
        "timestamp": 1.05,
        "touched": True
    }
    bridge._handle_incoming_touch(touch_payload)

    # The actuator response published back must be LOCKED (0 deg)
    last_msg = bridge.mqtt.recent_messages[-1]
    assert last_msg["topic"] == MQTTTopics.ACTUATOR_COMMAND.value
    assert last_msg["payload"]["servo_angle_deg"] == 0
    assert last_msg["payload"]["door_locked"] is True
    assert last_msg["payload"]["command"] == "LOCK_ENGAGED"


def test_esp32_touch_permits_unlatch_when_clear():
    """Verifies that an incoming ESP32 touch event permits unlatching when corridor is clear."""
    mqtt_client = DoorSafeMQTTClient(client_id="test_touch_clear")
    safety_mgr = SafetyManager()
    bridge = IoTBridge(mqtt_client=mqtt_client, safety_manager=safety_mgr)

    safety_mgr.reset()
    assert safety_mgr.state_machine.state == SafetyState.SAFE

    # Passenger touches handle
    touch_payload = {
        "device_id": "esp32_driver_door",
        "timestamp": 2.0,
        "touched": True
    }
    bridge._handle_incoming_touch(touch_payload)

    last_msg = bridge.mqtt.recent_messages[-1]
    assert last_msg["topic"] == MQTTTopics.ACTUATOR_COMMAND.value
    assert last_msg["payload"]["servo_angle_deg"] == 90
    assert last_msg["payload"]["door_locked"] is False
    assert last_msg["payload"]["command"] == "UNLOCK_PERMITTED"


def test_iot_rest_api_endpoints():
    """Verifies REST endpoints for Phase 4 IoT gateway."""
    # 1. Status
    r_status = client.get("/api/iot/status")
    assert r_status.status_code == 200
    status_data = r_status.json()
    assert "mqtt_gateway" in status_data
    assert "esp32_hardware" in status_data

    # 2. Simulate Touch
    r_touch = client.post("/api/iot/simulate-touch", json={"touched": True})
    assert r_touch.status_code == 200
    touch_data = r_touch.json()
    assert touch_data["status"] == "SIMULATED_TOUCH_DISPATCHED"
    assert touch_data["touched"] is True

    # 3. Simulate Radar
    r_radar = client.post("/api/iot/simulate-radar", json={"distance_cm": 150.0, "relative_speed_mps": -3.0})
    assert r_radar.status_code == 200
    radar_data = r_radar.json()
    assert radar_data["status"] == "RADAR_TELEMETRY_PROCESSED"
    assert radar_data["data"]["distance_m"] == 1.5

    # 4. Test Publish
    r_pub = client.post("/api/iot/publish-test", json={
        "topic": MQTTTopics.ACTUATOR_COMMAND.value,
        "payload": {
            "timestamp": time.time(),
            "command": "LOCK_ENGAGED",
            "servo_angle_deg": 0,
            "door_locked": True,
            "chime_alert": False,
            "reason": "Protocol validation test",
            "command_id": "api-test-command",
            "session_id": "api-test-session",
            "sequence": 1,
        }
    })
    assert r_pub.status_code == 200
    assert r_pub.json()["topic"] == MQTTTopics.ACTUATOR_COMMAND.value
