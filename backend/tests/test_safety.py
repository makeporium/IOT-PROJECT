"""DOORSAFE Phase 3 Safety Decision Engine & Actuator Test Suite."""

import pytest
from starlette.testclient import TestClient

from app.safety_engine.zone_geometry import DoorZonePolygon
from app.safety_engine.decision_rules import CollisionDecisionRules, ThreatLevel
from app.safety_engine.state_machine import (
    SafetyStateMachine,
    SafetyState,
    ActuatorCommand,
    DoorActionStatus
)
from app.safety_engine.manager import SafetyManager
from backend.main import app

client = TestClient(app)


def test_door_zone_polygon_geometry():
    """Verifies physical 2D door hazard envelope calculations."""
    zone = DoorZonePolygon()

    # Point clearly inside open door corridor (driver side: X in [-2.2, -0.7], Y in [-0.8, 2.2])
    assert zone.contains_point(-1.2, 0.5) is True

    # Point in distant adjacent road lane (X = -3.5m)
    assert zone.contains_point(-3.5, 0.5) is False
    assert zone.distance_to_zone(-3.5, 0.5) > 1.0

    # Point ahead of car (Y = 10m)
    assert zone.contains_point(-1.2, 10.0) is False

    # Dynamic expansion buffer for high closing speed (30 km/h)
    x_min_dyn, x_max_dyn, y_min_dyn, y_max_dyn = zone.get_dynamic_bounds(speed_kmh=30.0)
    assert x_min_dyn < zone.x_min  # Expanded outward
    assert y_min_dyn < zone.y_min  # Expanded backward along closing path


def test_door_zone_trajectory_intersection():
    """Verifies linear trajectory intersection window [t_enter, t_exit]."""
    zone = DoorZonePolygon()

    # Cyclist approaching from behind (Y = -10m) heading directly along door corridor (X = -1.2m, Vy = 5m/s)
    intersects, t_enter, t_exit = zone.intersects_trajectory(
        x=-1.2, y=-10.0, vx=0.0, vy=5.0, time_horizon_s=3.0
    )
    assert intersects is True
    assert t_enter is not None
    assert 1.5 <= t_enter <= 2.2

    # Vehicle in far right lane (X = +2.5m) heading forward (Vy = 8m/s)
    intersects_far, _, _ = zone.intersects_trajectory(
        x=2.5, y=-10.0, vx=0.0, vy=8.0, time_horizon_s=3.0
    )
    assert intersects_far is False


def test_collision_decision_rules_imminent_vs_clear():
    """Verifies deterministic rule classification of critical hazards vs safe traffic."""
    rules = CollisionDecisionRules()

    # Approaching cyclist entering corridor in 1.2s -> CRITICAL
    critical_cyclist = {
        "track_id": 101,
        "class_name": "bicycle",
        "metric_x": -1.3,
        "metric_y": -6.0,
        "distance_m": 6.1,
        "velocity_x_mps": 0.0,
        "velocity_y_mps": 5.0,
        "speed_kmh": 18.0,
        "ttc_seconds": 1.2,
        "uncertainty_m": 0.2,
        "distance_confidence": 0.95,
        "is_approaching": True
    }
    threat_crit = rules.evaluate_object(critical_cyclist)
    assert threat_crit.threat_level == ThreatLevel.CRITICAL
    assert threat_crit.is_conflict is True

    # Distant passenger car in right-hand lane -> SAFE
    distant_car = {
        "track_id": 102,
        "class_name": "car",
        "metric_x": 3.0,
        "metric_y": 15.0,
        "distance_m": 15.3,
        "velocity_x_mps": 0.0,
        "velocity_y_mps": 0.0,
        "speed_kmh": 0.0,
        "ttc_seconds": None,
        "uncertainty_m": 0.5,
        "distance_confidence": 0.9,
        "is_approaching": False
    }
    threat_safe = rules.evaluate_object(distant_car)
    assert threat_safe.threat_level == ThreatLevel.SAFE
    assert threat_safe.is_conflict is False


def test_measurement_uncertainty_guardrail():
    """Verifies that high measurement uncertainty on closing target triggers fail-safe escalation."""
    rules = CollisionDecisionRules()

    # Object approaching with high uncertainty (e.g. sigma = 4.2m > threshold 3.5m)
    uncertain_target = {
        "track_id": 103,
        "class_name": "motorcycle",
        "metric_x": -1.5,
        "metric_y": 7.0,
        "distance_m": 7.2,
        "velocity_y_mps": 4.0,
        "speed_kmh": 14.4,
        "ttc_seconds": 1.8,
        "uncertainty_m": 4.2,  # High uncertainty
        "distance_confidence": 0.20,
        "is_approaching": True
    }
    threat = rules.evaluate_object(uncertain_target)
    assert threat.threat_level == ThreatLevel.UNCERTAIN
    assert threat.is_conflict is True


def test_safety_state_machine_hysteresis():
    """Verifies release hysteresis prevents chattering between states."""
    machine = SafetyStateMachine(lock_hold_duration_s=1.0)

    # Frame 1: Imminent cyclist -> State becomes CRITICAL
    crit_obj = {
        "track_id": 1, "class_name": "bicycle", "metric_x": -1.2, "metric_y": 2.0,
        "distance_m": 2.3, "velocity_y_mps": 5.0, "speed_kmh": 18.0, "ttc_seconds": 0.5,
        "is_approaching": True
    }
    res1 = machine.update([crit_obj], frame_timestamp_s=1.0)
    assert res1.current_state == SafetyState.CRITICAL
    assert res1.is_door_locked is True
    assert res1.servo_angle_deg == 0

    # Frame 2 (0.3s later): Cyclist momentarily occluded -> Raw would be SAFE, but hysteresis holds CRITICAL!
    res2 = machine.update([], frame_timestamp_s=1.3)
    assert res2.current_state == SafetyState.CRITICAL  # Held by hysteresis
    assert res2.is_door_locked is True

    # Frame 3 (1.5s later): Hold duration (1.0s) has elapsed -> Returns to SAFE
    res3 = machine.update([], frame_timestamp_s=2.6)
    assert res3.current_state == SafetyState.SAFE
    assert res3.is_door_locked is False
    assert res3.servo_angle_deg == 90


def test_door_handle_touch_actuator_veto():
    """Verifies physical door latch veto when handle is touched during CRITICAL hazard."""
    machine = SafetyStateMachine()

    # Hazard approaching
    crit_obj = {
        "track_id": 5, "class_name": "motorcycle", "metric_x": -1.2, "metric_y": 4.0,
        "distance_m": 4.2, "velocity_y_mps": 6.0, "speed_kmh": 21.6, "ttc_seconds": 0.7,
        "is_approaching": True
    }
    machine.update([crit_obj], frame_timestamp_s=0.5)

    # Passenger touches capacitive handle to open door
    touch_resp = machine.handle_touch_trigger(is_touched=True)
    assert touch_resp.handle_touched is True
    assert touch_resp.current_safety_state == SafetyState.CRITICAL
    assert touch_resp.actuator_command == ActuatorCommand.LOCK_ENGAGED
    assert touch_resp.servo_angle_deg == 0
    assert touch_resp.action_status == DoorActionStatus.BLOCKED_BY_SAFETY_VETO
    assert touch_resp.lock_veto_active is True
    assert touch_resp.chime_alert is True

    # After hazard clears, passenger touches handle again -> unlatch permitted
    machine.update([], frame_timestamp_s=3.0)  # Beyond hysteresis
    touch_resp_clear = machine.handle_touch_trigger(is_touched=True)
    assert touch_resp_clear.current_safety_state == SafetyState.SAFE
    assert touch_resp_clear.action_status == DoorActionStatus.UNLATCHED
    assert touch_resp_clear.servo_angle_deg == 90
    assert touch_resp_clear.lock_veto_active is False


def test_safety_api_endpoints():
    """Verifies FastAPI REST endpoints for Phase 3 safety engine."""
    # 1. Reset
    r_reset = client.post("/api/v1/safety/reset")
    assert r_reset.status_code == 200
    assert r_reset.json()["safety_state"] == "SAFE"

    # 2. Get status
    r_status = client.get("/api/v1/safety/status")
    assert r_status.status_code == 200
    data = r_status.json()
    assert data["status"] == "ONLINE"
    assert data["servo_angle_deg"] == 90

    # 3. Simulate Handle Touch in SAFE state
    r_touch = client.post("/api/v1/safety/handle-touch", json={"touched": True})
    assert r_touch.status_code == 200
    touch_data = r_touch.json()
    assert touch_data["action_status"] == "UNLATCHED"
    assert touch_data["servo_angle_deg"] == 90

    # 4. Evaluate dangerous frame via API
    dangerous_objects = [
        {
            "track_id": 99,
            "class_name": "bicycle",
            "metric_x": -1.1,
            "metric_y": 1.0,
            "distance_m": 1.5,
            "velocity_y_mps": 4.0,
            "speed_kmh": 14.4,
            "ttc_seconds": 0.3,
            "is_approaching": True
        }
    ]
    r_eval = client.post("/api/v1/safety/evaluate", json={"timestamp_sec": 1.0, "objects": dangerous_objects})
    assert r_eval.status_code == 200
    eval_data = r_eval.json()
    assert eval_data["safety_state"] == "CRITICAL"
    assert eval_data["is_door_locked"] is True
    assert eval_data["servo_angle_deg"] == 0

    # 5. Handle touch now blocked
    r_touch_blocked = client.post("/api/v1/safety/handle-touch", json={"touched": True})
    assert r_touch_blocked.json()["action_status"] == "BLOCKED_BY_SAFETY_VETO"
    assert r_touch_blocked.json()["door_locked"] is True
