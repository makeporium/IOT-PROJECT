"""Kinematics and motion trajectory generation for road participants."""

import math
from typing import Tuple, List, Optional
from app.simulation.ground_truth import GroundTruthState


class ObjectMotionSimulator:
    """Updates participant positions along straight or lane-change paths."""

    def __init__(
        self,
        object_id: int,
        object_type: str,
        start_x_m: float,
        start_y_m: float,
        speed_mps: float,
        lateral_speed_mps: float = 0.0
    ):
        self.object_id = object_id
        self.object_type = object_type
        self.x = start_x_m
        self.y = start_y_m
        self.vx = lateral_speed_mps
        self.vy = speed_mps

    def step(self, dt_sec: float) -> GroundTruthState:
        """Advances position by dt_sec."""
        self.x += self.vx * dt_sec
        self.y += self.vy * dt_sec
        
        # Calculate distance to driver door (assumed at x=-0.91, y=0.0)
        door_x, door_y = -0.91, 0.0
        distance = math.sqrt((self.x - door_x)**2 + (self.y - door_y)**2)
        
        # Longitudinal TTC if approaching
        ttc = None
        if self.vy > 0.1 and self.y < door_y:
            ttc = abs(door_y - self.y) / self.vy
        elif self.vy < -0.1 and self.y > door_y:
            ttc = abs(self.y - door_y) / abs(self.vy)

        return GroundTruthState(
            object_id=self.object_id,
            object_type=self.object_type,
            x_pos_m=round(self.x, 3),
            y_pos_m=round(self.y, 3),
            velocity_mps=round(self.vy, 2),
            lateral_velocity_mps=round(self.vx, 2),
            heading_deg=round(math.degrees(math.atan2(self.vx, self.vy)), 1),
            actual_distance_m=round(distance, 2),
            actual_ttc_seconds=round(ttc, 2) if ttc is not None else None,
            intersects_door_zone=(abs(self.x - door_x) < 1.4 and abs(self.y - door_y) < 1.8),
            source_mode="SIMULATION",
            measurement_type="ACTUAL"
        )
