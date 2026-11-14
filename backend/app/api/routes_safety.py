"""DOORSAFE Phase 3: Safety Decision Engine REST API Endpoints."""

import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Depends

from app.safety_engine.manager import SafetyManager, get_safety_manager
from app.safety_engine.state_machine import SafetyState, ActuatorCommand, DoorActionStatus

logger = logging.getLogger("doorsafe.api.safety")
router = APIRouter(prefix="/safety", tags=["Phase 3: Door Safety & Actuator Decision Engine"])


class HandleTouchRequest(BaseModel):
    """Payload to simulate or receive passenger capacitive door handle touch."""
    touched: bool = Field(True, description="True if passenger touches door handle, False when released")
    objects: Optional[List[Dict[str, Any]]] = Field(None, description="Optional current obstacle telemetry")


class EvaluateFrameRequest(BaseModel):
    """Payload to evaluate safety state for a collection of tracked objects."""
    timestamp_sec: float = Field(0.0, description="Video or sensor timestamp in seconds")
    objects: List[Dict[str, Any]] = Field(default_factory=list, description="List of tracked obstacles")


@router.get("/status")
def get_safety_status(manager: SafetyManager = Depends(get_safety_manager)):
    """
    Returns real-time vehicle safety state, door lock status,
    servo motor command (0° vs 90°), active hazards, and audit statistics.
    """
    return manager.get_status()


@router.post("/handle-touch")
def trigger_handle_touch(
    req: HandleTouchRequest,
    manager: SafetyManager = Depends(get_safety_manager)
):
    """
    Simulates or receives a physical capacitive touch event on the vehicle door handle.
    Returns deterministic actuator response (e.g. BLOCKED_BY_SAFETY_VETO or UNLATCHED).
    """
    resp = manager.trigger_handle_touch(is_touched=req.touched, objects=req.objects)
    return {
        "timestamp": resp.timestamp,
        "handle_touched": resp.handle_touched,
        "current_safety_state": resp.current_safety_state.value,
        "actuator_command": resp.actuator_command.value,
        "servo_angle_deg": resp.servo_angle_deg,
        "action_status": resp.action_status.value,
        "door_locked": resp.lock_veto_active,
        "chime_alert": resp.chime_alert,
        "message": resp.message
    }


@router.post("/evaluate")
def evaluate_objects(
    req: EvaluateFrameRequest,
    manager: SafetyManager = Depends(get_safety_manager)
):
    """
    Evaluates a collection of tracked road participants against the door danger zone.
    Executes the deterministic state machine with release hysteresis.
    """
    result = manager.process_frame(objects=req.objects, timestamp_sec=req.timestamp_sec)
    return {
        "timestamp_sec": result.timestamp_sec,
        "safety_state": result.current_state.value,
        "previous_state": result.previous_state.value,
        "actuator_command": result.actuator_command.value,
        "servo_angle_deg": result.servo_angle_deg,
        "is_door_locked": result.is_door_locked,
        "threat_count": result.threat_count,
        "min_ttc_seconds": result.min_ttc_seconds,
        "fail_safe_active": result.fail_safe_active,
        "critical_hazards": [
            {
                "track_id": h.track_id,
                "class_name": h.class_name,
                "distance_m": h.distance_m,
                "ttc_seconds": h.ttc_seconds,
                "speed_kmh": h.speed_kmh,
                "reason": h.reason
            }
            for h in result.critical_hazards
        ],
        "warning_hazards": [
            {
                "track_id": h.track_id,
                "class_name": h.class_name,
                "distance_m": h.distance_m,
                "ttc_seconds": h.ttc_seconds,
                "speed_kmh": h.speed_kmh,
                "reason": h.reason
            }
            for h in result.warning_hazards
        ]
    }


@router.post("/reset")
def reset_safety_system(manager: SafetyManager = Depends(get_safety_manager)):
    """Resets the safety state machine to the baseline SAFE state."""
    manager.reset()
    return {"status": "RESET_SUCCESSFUL", "safety_state": "SAFE", "servo_angle_deg": 90}
