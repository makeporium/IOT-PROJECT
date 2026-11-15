"""DOORSAFE Phase 3: Vehicle Safety Manager & Actuator Orchestrator."""

import time
import logging
from typing import Dict, Any, List, Optional
from collections import deque
from datetime import datetime

from app.safety_engine.state_machine import (
    SafetyStateMachine,
    SafetyState,
    ActuatorCommand,
    DoorActionStatus,
    DoorHandleResponse,
    SafetyFrameResult
)
from app.safety_engine.decision_rules import ThreatAssessment, ThreatLevel

logger = logging.getLogger("doorsafe.safety.manager")


class SafetyManager:
    """
    Central vehicle safety coordinator.
    Manages deterministic state machine, handle-touch actuation,
    audit event history, and hardware integration.
    """

    def __init__(self):
        self.state_machine = SafetyStateMachine()
        self.audit_log: deque = deque(maxlen=200)
        self.active_frame_result: Optional[SafetyFrameResult] = None
        self.last_handle_response: Optional[DoorHandleResponse] = None
        self.is_handle_touch_active: bool = False
        self.total_lock_vetoes: int = 0
        self.total_door_openings: int = 0
        self.last_fail_safe_reason: Optional[str] = None

    def process_frame(self, objects: List[Dict[str, Any]], timestamp_sec: float) -> SafetyFrameResult:
        """Processes live or recorded frame telemetry through the deterministic state machine."""
        result = self.state_machine.update(objects, timestamp_sec)
        self.active_frame_result = result

        # Check for state transition event to log
        if result.current_state != result.previous_state:
            event = {
                "timestamp": datetime.now().isoformat(),
                "video_timestamp_s": round(timestamp_sec, 2),
                "type": "STATE_TRANSITION",
                "from_state": result.previous_state.value,
                "to_state": result.current_state.value,
                "servo_angle": result.servo_angle_deg,
                "actuator_command": result.actuator_command.value,
                "threat_count": result.threat_count,
                "min_ttc_s": result.min_ttc_seconds
            }
            self.audit_log.append(event)
            logger.info(f"Safety State Transition: {result.previous_state.value} -> {result.current_state.value} "
                        f"[Servo: {result.servo_angle_deg}°, Threats: {result.threat_count}]")

        return result

    def trigger_handle_touch(self, is_touched: bool, objects: Optional[List[Dict[str, Any]]] = None) -> DoorHandleResponse:
        """Simulates or receives physical capacitive touch on the passenger door handle."""
        self.is_handle_touch_active = is_touched
        response = self.state_machine.handle_touch_trigger(is_touched, objects)
        self.last_handle_response = response

        if is_touched:
            if response.lock_veto_active:
                self.total_lock_vetoes += 1
            else:
                self.total_door_openings += 1

            # Log audit event
            event = {
                "timestamp": datetime.now().isoformat(),
                "type": "HANDLE_TOUCH_REQUEST",
                "handle_touched": is_touched,
                "state": response.current_safety_state.value,
                "action": response.action_status.value,
                "servo_angle": response.servo_angle_deg,
                "veto_active": response.lock_veto_active,
                "message": response.message
            }
            self.audit_log.append(event)
            logger.info(f"Door Handle Event: {response.action_status.value} (Servo: {response.servo_angle_deg}°)")

        return response

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive real-time automotive safety status."""
        current_state = self.state_machine.state.value
        servo_angle = self.state_machine.servo_angle_deg
        is_locked = (servo_angle == 0)

        min_ttc = None
        threats = []
        if self.active_frame_result:
            min_ttc = self.active_frame_result.min_ttc_seconds
            threats = [
                {
                    "track_id": t.track_id,
                    "class_name": t.class_name,
                    "threat_level": t.threat_level.value,
                    "distance_m": t.distance_m,
                    "ttc_seconds": t.ttc_seconds,
                    "speed_kmh": t.speed_kmh,
                    "reason": t.reason
                }
                for t in (self.active_frame_result.critical_hazards + self.active_frame_result.warning_hazards)
            ]

        return {
            "status": "ONLINE",
            "safety_state": current_state,
            "door_locked": is_locked,
            "servo_angle_deg": servo_angle,
            "actuator_command": self.state_machine.actuator_command.value,
            "handle_touched": self.is_handle_touch_active,
            "telemetry_fresh": self.state_machine.telemetry_is_fresh(),
            "min_ttc_seconds": min_ttc,
            "active_threats_count": len(threats),
            "threats": threats,
            "statistics": {
                "total_lock_vetoes": self.total_lock_vetoes,
                "total_door_openings": self.total_door_openings,
                "total_events_logged": len(self.audit_log)
            },
            "recent_events": list(self.audit_log)[-10:]
        }

    def force_fail_safe(self, reason: str) -> None:
        """Locks the controller and records why the fail-safe was entered."""
        if self.state_machine.state != SafetyState.FAIL_SAFE:
            self.state_machine.force_fail_safe()
        if reason != self.last_fail_safe_reason:
            self.audit_log.append({
                "timestamp": datetime.now().isoformat(),
                "type": "FAIL_SAFE",
                "reason": reason,
                "servo_angle": 0,
            })
            logger.warning("Safety fail-safe engaged: %s", reason)
            self.last_fail_safe_reason = reason

    def reset(self):
        """Resets the safety manager and state machine."""
        self.state_machine.reset()
        self.active_frame_result = None
        self.is_handle_touch_active = False
        self.last_handle_response = None
        self.last_fail_safe_reason = None
        logger.info("SafetyManager reset to clean baseline state.")


# Global Singleton for FastAPI application
SAFETY_MANAGER = SafetyManager()


def get_safety_manager() -> SafetyManager:
    """Dependency injector for SafetyManager singleton."""
    return SAFETY_MANAGER
