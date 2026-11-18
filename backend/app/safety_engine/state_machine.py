"""DOORSAFE Phase 3: Deterministic Automotive Safety State Machine."""

import time
from enum import Enum
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from app.safety_engine.decision_rules import (
    CollisionDecisionRules,
    ThreatAssessment,
    ThreatLevel
)


class SafetyState(str, Enum):
    """Vehicle door safety states."""
    SAFE = "SAFE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    FAIL_SAFE = "FAIL_SAFE"


class ActuatorCommand(str, Enum):
    """Actuator servo control commands (mapped to SG90 servo in Phase 4)."""
    UNLOCK_PERMITTED = "UNLOCK_PERMITTED"  # Servo at 90 deg (unlatched)
    HAPTIC_WARNING = "HAPTIC_WARNING"      # Servo at 90 deg + buzzer alert
    LOCK_ENGAGED = "LOCK_ENGAGED"          # Servo at 0 deg (physical veto)
    FAIL_SAFE_LOCK = "FAIL_SAFE_LOCK"      # Servo at 0 deg (system failure safety lock)


class DoorActionStatus(str, Enum):
    """Outcome of passenger handle-touch request."""
    UNLATCHED = "UNLATCHED"
    UNLATCHED_WITH_WARNING = "UNLATCHED_WITH_WARNING"
    BLOCKED_BY_SAFETY_VETO = "BLOCKED_BY_SAFETY_VETO"
    BLOCKED_BY_FAIL_SAFE = "BLOCKED_BY_FAIL_SAFE"


@dataclass
class DoorHandleResponse:
    """Telemetry report emitted when passenger touches the door handle."""
    timestamp: float
    handle_touched: bool
    current_safety_state: SafetyState
    actuator_command: ActuatorCommand
    servo_angle_deg: int
    action_status: DoorActionStatus
    primary_hazard_id: Optional[int]
    primary_hazard_class: Optional[str]
    time_to_collision_s: Optional[float]
    lock_veto_active: bool
    chime_alert: bool
    message: str


@dataclass
class SafetyFrameResult:
    """Frame-by-frame safety assessment and actuator state."""
    timestamp_sec: float
    current_state: SafetyState
    previous_state: SafetyState
    actuator_command: ActuatorCommand
    servo_angle_deg: int
    is_door_locked: bool
    threat_count: int
    critical_hazards: List[ThreatAssessment]
    warning_hazards: List[ThreatAssessment]
    min_ttc_seconds: Optional[float]
    time_in_current_state_s: float
    fail_safe_active: bool


class SafetyStateMachine:
    """
    Deterministic rule-based safety engine with strict fail-safe veto power.
    
    Automotive Safety Integrity Principles:
      1. Immediate Lock: Transitions to CRITICAL/FAIL_SAFE occur in 0ms (no delay).
      2. Release Hysteresis: Transitions out of CRITICAL require a holding buffer
         (default: 1.0s) to prevent dangerous chatter during occlusion or tracking jitter.
      3. Fail-Safe Default: Missing telemetry or sensor timeout defaults to LOCK_ENGAGED.
    """

    def __init__(
        self,
        decision_rules: Optional[CollisionDecisionRules] = None,
        lock_hold_duration_s: float = 1.0,
        # A paused recorded-video frame is still valid telemetry for a short
        # inspection window. 0.5 s caused a user to hit the handle after
        # pausing and receive a false FAIL_SAFE lock before they could test it.
        sensor_timeout_s: float = 30.0,
        enable_sensor_timeout: bool = True,
    ):
        self.rules = decision_rules or CollisionDecisionRules()
        self.lock_hold_duration_s = lock_hold_duration_s
        self.sensor_timeout_s = sensor_timeout_s
        self.enable_sensor_timeout = enable_sensor_timeout

        # Live State
        self.state: SafetyState = SafetyState.SAFE
        self.previous_state: SafetyState = SafetyState.SAFE
        self.state_entered_time: float = time.perf_counter()
        self.last_sensor_update_time: float = time.perf_counter()

        # Actuator State
        self.actuator_command: ActuatorCommand = ActuatorCommand.UNLOCK_PERMITTED
        self.servo_angle_deg: int = 90  # 90 = Unlocked, 0 = Locked

        # Hysteresis Tracking
        self.last_critical_timestamp: float = 0.0

        # Passenger Handle Sensor
        self.is_handle_touched: bool = False

    def force_fail_safe(self) -> None:
        """Immediately enter the locked fail-safe state after a control-path failure."""
        self.previous_state = self.state
        self.state = SafetyState.FAIL_SAFE
        self.actuator_command = ActuatorCommand.FAIL_SAFE_LOCK
        self.servo_angle_deg = 0
        self.state_entered_time = time.perf_counter()

    def telemetry_is_fresh(self) -> bool:
        return (time.perf_counter() - self.last_sensor_update_time) <= self.sensor_timeout_s

    def update(self, objects: List[Dict[str, Any]], frame_timestamp_s: float) -> SafetyFrameResult:
        """
        Processes one frame of tracked road users and determines safety state and actuator position.
        """
        now = time.perf_counter()
        self.last_sensor_update_time = now

        # Evaluate all objects in frame
        threats = self.rules.evaluate_frame_threats(objects)

        critical_list = [t for t in threats if t.threat_level == ThreatLevel.CRITICAL]
        warning_list = [t for t in threats if t.threat_level == ThreatLevel.WARNING]
        uncertain_list = [t for t in threats if t.threat_level == ThreatLevel.UNCERTAIN]

        # Calculate minimum TTC among approaching hazards
        active_ttcs = [t.ttc_seconds for t in (critical_list + warning_list) if t.ttc_seconds is not None]
        min_ttc = min(active_ttcs) if active_ttcs else None

        # Determine raw target state
        if critical_list or uncertain_list:
            raw_target_state = SafetyState.CRITICAL if critical_list else SafetyState.FAIL_SAFE
            # Hysteresis uses a monotonic clock, independent of frame_timestamp_s
            # (which may be video-relative seconds from process_frame() or wall-clock
            # epoch time from a handle-touch re-evaluation) so the two callers can
            # never desync the "hold lock for N seconds" window.
            self.last_critical_timestamp = now
        elif warning_list:
            raw_target_state = SafetyState.WARNING
        else:
            raw_target_state = SafetyState.SAFE

        # Apply Safety Hysteresis: Hold CRITICAL lock for buffer duration
        elapsed_since_crit = now - self.last_critical_timestamp
        if self.state in (SafetyState.CRITICAL, SafetyState.FAIL_SAFE) and raw_target_state != SafetyState.CRITICAL:
            if elapsed_since_crit < self.lock_hold_duration_s:
                # Hold in CRITICAL until road user has fully cleared the danger zone
                raw_target_state = self.state

        # State Transition Execution
        prev = self.state
        if raw_target_state != self.state:
            self.previous_state = self.state
            self.state = raw_target_state
            self.state_entered_time = now

        # Derive Actuator Command
        if self.state == SafetyState.CRITICAL:
            self.actuator_command = ActuatorCommand.LOCK_ENGAGED
            self.servo_angle_deg = 0
            is_locked = True
        elif self.state == SafetyState.FAIL_SAFE:
            self.actuator_command = ActuatorCommand.FAIL_SAFE_LOCK
            self.servo_angle_deg = 0
            is_locked = True
        elif self.state == SafetyState.WARNING:
            self.actuator_command = ActuatorCommand.HAPTIC_WARNING
            self.servo_angle_deg = 90
            is_locked = False
        else:  # SAFE
            self.actuator_command = ActuatorCommand.UNLOCK_PERMITTED
            self.servo_angle_deg = 90
            is_locked = False

        time_in_state = now - self.state_entered_time

        return SafetyFrameResult(
            timestamp_sec=round(frame_timestamp_s, 3),
            current_state=self.state,
            previous_state=prev,
            actuator_command=self.actuator_command,
            servo_angle_deg=self.servo_angle_deg,
            is_door_locked=is_locked,
            threat_count=len(critical_list) + len(warning_list),
            critical_hazards=critical_list,
            warning_hazards=warning_list,
            min_ttc_seconds=round(min_ttc, 2) if min_ttc else None,
            time_in_current_state_s=round(time_in_state, 3),
            fail_safe_active=(self.state == SafetyState.FAIL_SAFE)
        )

    def handle_touch_trigger(
        self,
        is_touched: bool,
        objects: Optional[List[Dict[str, Any]]] = None,
        enforce_timeout: bool = True,
    ) -> DoorHandleResponse:
        """
        Evaluates a passenger door-handle touch event against current safety state.
        This represents the physical interaction before opening the door.
        """
        self.is_handle_touched = is_touched
        now = time.time()

        # If telemetry objects passed in, do instant evaluation
        if objects is not None:
            self.update(objects, frame_timestamp_s=time.time())

        # Check sensor timeout (fail-safe trigger) only if watchdog is explicitly enabled
        if self.enable_sensor_timeout and enforce_timeout:
            time_since_last_sensor = time.perf_counter() - self.last_sensor_update_time
            if time_since_last_sensor > self.sensor_timeout_s:
                self.force_fail_safe()

        # Formulate Door Response
        if self.state == SafetyState.CRITICAL:
            return DoorHandleResponse(
                timestamp=now,
                handle_touched=is_touched,
                current_safety_state=self.state,
                actuator_command=ActuatorCommand.LOCK_ENGAGED,
                servo_angle_deg=0,
                action_status=DoorActionStatus.BLOCKED_BY_SAFETY_VETO,
                primary_hazard_id=None,
                primary_hazard_class=None,
                time_to_collision_s=None,
                lock_veto_active=True,
                chime_alert=True,
                message="DOOR LOCKED: Safety veto active. Imminent road participant collision predicted."
            )
        elif self.state == SafetyState.FAIL_SAFE:
            return DoorHandleResponse(
                timestamp=now,
                handle_touched=is_touched,
                current_safety_state=self.state,
                actuator_command=ActuatorCommand.FAIL_SAFE_LOCK,
                servo_angle_deg=0,
                action_status=DoorActionStatus.BLOCKED_BY_FAIL_SAFE,
                primary_hazard_id=None,
                primary_hazard_class=None,
                time_to_collision_s=None,
                lock_veto_active=True,
                chime_alert=True,
                message="DOOR LOCKED: Fail-safe active due to sensor timeout or measurement uncertainty."
            )
        elif self.state == SafetyState.WARNING:
            return DoorHandleResponse(
                timestamp=now,
                handle_touched=is_touched,
                current_safety_state=self.state,
                actuator_command=ActuatorCommand.HAPTIC_WARNING,
                servo_angle_deg=90,
                action_status=DoorActionStatus.UNLATCHED_WITH_WARNING,
                primary_hazard_id=None,
                primary_hazard_class=None,
                time_to_collision_s=None,
                lock_veto_active=False,
                chime_alert=True,
                message="WARNING: Closing traffic detected. Unlatching permitted with caution."
            )
        else:  # SAFE
            return DoorHandleResponse(
                timestamp=now,
                handle_touched=is_touched,
                current_safety_state=self.state,
                actuator_command=ActuatorCommand.UNLOCK_PERMITTED,
                servo_angle_deg=90,
                action_status=DoorActionStatus.UNLATCHED,
                primary_hazard_id=None,
                primary_hazard_class=None,
                time_to_collision_s=None,
                lock_veto_active=False,
                chime_alert=False,
                message="CLEAR: Corridor safe. Door latch opened freely."
            )

    def reset(self):
        """Resets state machine to initial clean safe state."""
        self.state = SafetyState.SAFE
        self.previous_state = SafetyState.SAFE
        self.actuator_command = ActuatorCommand.UNLOCK_PERMITTED
        self.servo_angle_deg = 90
        self.last_critical_timestamp = 0.0
        self.is_handle_touched = False
        self.state_entered_time = time.perf_counter()
        self.last_sensor_update_time = time.perf_counter()
