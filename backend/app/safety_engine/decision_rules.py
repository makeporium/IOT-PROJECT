"""DOORSAFE Phase 3: Deterministic Collision & Threat Decision Rules."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List
import logging

from app.safety_engine.zone_geometry import DoorZonePolygon

logger = logging.getLogger("doorsafe.safety.rules")


class ThreatLevel(str, Enum):
    """Urgency severity levels for road obstacles."""
    SAFE = "SAFE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class ThreatAssessment:
    """Detailed threat diagnosis for a single tracked obstacle."""
    track_id: int
    class_name: str
    threat_level: ThreatLevel
    is_conflict: bool
    distance_m: Optional[float]
    ttc_seconds: Optional[float]
    speed_kmh: Optional[float]
    reason: str


class CollisionDecisionRules:
    """
    Deterministic rule engine evaluating road participant kinematics
    against vehicle door opening hazard boundaries.
    """

    def __init__(
        self,
        door_zone: Optional[DoorZonePolygon] = None,
        critical_ttc_threshold_s: float = 2.0,
        warning_ttc_threshold_s: float = 4.0,
        lateral_warning_distance_m: float = 3.5,
        max_acceptable_uncertainty_m: float = 3.5,
        min_distance_confidence: float = 0.35,
    ):
        self.door_zone = door_zone or DoorZonePolygon()
        self.critical_ttc_s = critical_ttc_threshold_s
        self.warning_ttc_s = warning_ttc_threshold_s
        self.lateral_warning_m = lateral_warning_distance_m
        self.max_uncertainty_m = max_acceptable_uncertainty_m
        self.min_confidence = min_distance_confidence

    def evaluate_object(self, obj: Dict[str, Any]) -> ThreatAssessment:
        """
        Applies deterministic automotive safety rules to a single tracked object.
        """
        track_id = obj.get("track_id", -1)
        class_name = obj.get("class_name", "unknown")
        x = obj.get("metric_x")
        y = obj.get("metric_y")
        distance_m = obj.get("distance_m")
        vx = obj.get("velocity_x_mps", 0.0) or 0.0
        vy = obj.get("velocity_y_mps", 0.0) or 0.0
        speed_kmh = obj.get("speed_kmh", 0.0) or 0.0
        ttc = obj.get("ttc_seconds")
        uncertainty = obj.get("uncertainty_m", 0.0) or 0.0
        conf = obj.get("distance_confidence", 1.0) or 1.0
        is_approaching = obj.get("is_approaching", False)

        # 1. Missing coordinates check
        if x is None or y is None or distance_m is None:
            return ThreatAssessment(
                track_id=track_id,
                class_name=class_name,
                threat_level=ThreatLevel.UNCERTAIN,
                is_conflict=False,
                distance_m=None,
                ttc_seconds=None,
                speed_kmh=None,
                reason="Metric coordinates missing or uncalibrated."
            )

        # 2. Measurement Uncertainty Guardrail
        # If camera ray divergence or detection jitter exceeds safety bounds on a close object:
        if y < 15.0 and (uncertainty > self.max_uncertainty_m or conf < self.min_confidence):
            return ThreatAssessment(
                track_id=track_id,
                class_name=class_name,
                threat_level=ThreatLevel.UNCERTAIN,
                is_conflict=True,
                distance_m=distance_m,
                ttc_seconds=ttc,
                speed_kmh=speed_kmh,
                reason=f"High measurement uncertainty (±{uncertainty:.1f}m, conf={conf:.2f}). Escalated for safety."
            )

        # 3. Object already inside the physical door danger corridor
        if self.door_zone.contains_point(x, y, speed_kmh):
            return ThreatAssessment(
                track_id=track_id,
                class_name=class_name,
                threat_level=ThreatLevel.CRITICAL,
                is_conflict=True,
                distance_m=distance_m,
                ttc_seconds=0.0,
                speed_kmh=speed_kmh,
                reason=f"{class_name.capitalize()} #{track_id} is inside the physical door swing corridor."
            )

        # 4. Trajectory Corridor Intersection Check
        intersects, entry_t, _ = self.door_zone.intersects_trajectory(
            x=x, y=y, vx=vx, vy=vy, time_horizon_s=self.warning_ttc_s
        )

        effective_ttc = ttc if ttc is not None else entry_t
        # A short longitudinal TTC alone is *not* evidence that a door will be
        # struck.  A vehicle can be close and approaching while remaining in
        # its own lane.  It becomes a door-opening conflict only when its
        # measured/predicted path enters the physical swing corridor.
        #
        # ``is_conflict_predicted`` is produced by the independent 2-D TTC
        # calculator, while ``intersects`` is this rule engine's direct
        # projection.  Either is valid spatial evidence; TTC then determines
        # severity, not whether a collision exists in the first place.
        is_conflict = intersects or obj.get("is_conflict_predicted", False)

        # 5. Critical Threshold Evaluation (Imminent Door Impact)
        if is_conflict and effective_ttc is not None:
            if effective_ttc < self.critical_ttc_s:
                return ThreatAssessment(
                    track_id=track_id,
                    class_name=class_name,
                    threat_level=ThreatLevel.CRITICAL,
                    is_conflict=True,
                    distance_m=distance_m,
                    ttc_seconds=round(effective_ttc, 2),
                    speed_kmh=round(speed_kmh, 1),
                    reason=f"Imminent collision: {class_name} #{track_id} trajectory enters door zone in {effective_ttc:.1f}s."
                )
            elif effective_ttc <= self.warning_ttc_s:
                return ThreatAssessment(
                    track_id=track_id,
                    class_name=class_name,
                    threat_level=ThreatLevel.WARNING,
                    is_conflict=True,
                    distance_m=distance_m,
                    ttc_seconds=round(effective_ttc, 2),
                    speed_kmh=round(speed_kmh, 1),
                    reason=f"Closing hazard: {class_name} #{track_id} predicted to enter corridor in {effective_ttc:.1f}s."
                )

        # 6. Lateral Proximity Warning (VRU alongside car even if parallel)
        dist_to_zone = self.door_zone.distance_to_zone(x, y)
        if dist_to_zone < 1.0 and distance_m < 5.0 and (is_approaching or speed_kmh > 3.0):
            return ThreatAssessment(
                track_id=track_id,
                class_name=class_name,
                threat_level=ThreatLevel.WARNING,
                is_conflict=False,
                distance_m=distance_m,
                ttc_seconds=effective_ttc,
                speed_kmh=round(speed_kmh, 1),
                reason=f"Close proximity: {class_name} #{track_id} is {dist_to_zone:.1f}m from door zone."
            )

        # 7. Safe Clear Lane
        return ThreatAssessment(
            track_id=track_id,
            class_name=class_name,
            threat_level=ThreatLevel.SAFE,
            is_conflict=False,
            distance_m=distance_m,
            ttc_seconds=effective_ttc,
            speed_kmh=round(speed_kmh, 1),
            reason=f"Lane clear: {class_name} #{track_id} does not intersect door corridor."
        )

    def evaluate_frame_threats(self, objects: List[Dict[str, Any]]) -> List[ThreatAssessment]:
        """Evaluates all tracked objects within a video frame."""
        return [self.evaluate_object(obj) for obj in objects]
