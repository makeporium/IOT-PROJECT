"""Kinematic motion estimation and Alpha-Beta state filtering."""

import math
from typing import Dict, Optional, Tuple, List
from pydantic import BaseModel, Field


class KinematicState(BaseModel):
    """Estimated physical motion state of a road participant."""
    x_m: float = Field(..., description="Filtered lateral position (meters)")
    y_m: float = Field(..., description="Filtered longitudinal distance (meters)")
    vx_mps: float = Field(0.0, description="Lateral velocity (m/s)")
    vy_mps: float = Field(0.0, description="Longitudinal velocity (m/s, negative = closing/approaching)")
    speed_mps: float = Field(0.0, description="Total ground speed (m/s)")
    speed_kmh: float = Field(0.0, description="Total ground speed (km/h)")
    acceleration_mps2: float = Field(0.0, description="Estimated longitudinal acceleration (m/s^2)")
    heading_deg: float = Field(0.0, description="Direction of movement relative to vehicle (degrees)")
    is_approaching: bool = Field(False, description="Whether closing in on the parked vehicle")


class ObjectKinematicTracker:
    """Alpha-Beta filter for smooth tracking of position and velocity for a single object."""

    def __init__(
        self,
        track_id: int,
        initial_x: float,
        initial_y: float,
        initial_timestamp: float,
        alpha: float = 0.70,  # Position smoothing weight
        beta: float = 0.35    # Velocity smoothing weight
    ):
        self.track_id = track_id
        self.x = initial_x
        self.y = initial_y
        self.vx = 0.0
        self.vy = 0.0
        self.ax = 0.0
        self.ay = 0.0
        self.last_timestamp = initial_timestamp
        self.alpha = alpha
        self.beta = beta
        self.updates_count = 1

    def update(self, meas_x: float, meas_y: float, timestamp: float) -> KinematicState:
        """Applies Alpha-Beta filter update with new ground position measurement."""
        dt = timestamp - self.last_timestamp
        # Guard against zero or negative delta
        if dt <= 0.001:
            dt = 0.033  # Assume ~30 FPS fallback

        # 1. State prediction step
        pred_x = self.x + self.vx * dt
        pred_y = self.y + self.vy * dt

        # 2. Residual (innovation)
        res_x = meas_x - pred_x
        res_y = meas_y - pred_y

        # If residual is massive (e.g. teleporting track), adapt gain
        gain_scale = 1.0
        if abs(res_y) > 10.0:
            gain_scale = 1.5

        # 3. State correction step
        self.x = pred_x + min(1.0, self.alpha * gain_scale) * res_x
        self.y = pred_y + min(1.0, self.alpha * gain_scale) * res_y

        prev_vy = self.vy
        self.vx = self.vx + (self.beta / dt) * res_x
        self.vy = self.vy + (self.beta / dt) * res_y

        # Clamp physically unrealistic velocities for road participants (> 130 km/h)
        self.vy = max(-36.0, min(36.0, self.vy))
        self.vx = max(-15.0, min(15.0, self.vx))

        # Acceleration
        self.ay = (self.vy - prev_vy) / dt
        self.last_timestamp = timestamp
        self.updates_count += 1

        speed = math.sqrt(self.vx**2 + self.vy**2)
        speed_kmh = speed * 3.6
        heading = math.degrees(math.atan2(self.vx, -self.vy))  # 0 deg = straight approaching

        # In vehicle reference frame: negative vy means distance Y is shrinking (approaching)
        is_approaching = (self.vy < -0.30) and (self.y > 0.3)

        return KinematicState(
            x_m=round(self.x, 2),
            y_m=round(self.y, 2),
            vx_mps=round(self.vx, 2),
            vy_mps=round(self.vy, 2),
            speed_mps=round(speed, 2),
            speed_kmh=round(speed_kmh, 1),
            acceleration_mps2=round(self.ay, 2),
            heading_deg=round(heading, 1),
            is_approaching=is_approaching
        )


class KinematicEstimator:
    """Manages kinematic state filters for all active tracked road users."""

    def __init__(self):
        self.trackers: Dict[int, ObjectKinematicTracker] = {}

    def update(
        self,
        track_id: int,
        ground_x: float,
        ground_y: float,
        timestamp: float
    ) -> KinematicState:
        """Updates or initializes kinematic filter for track_id."""
        if track_id not in self.trackers:
            self.trackers[track_id] = ObjectKinematicTracker(
                track_id=track_id,
                initial_x=ground_x,
                initial_y=ground_y,
                initial_timestamp=timestamp
            )
            speed = 0.0
            return KinematicState(
                x_m=ground_x,
                y_m=ground_y,
                vx_mps=0.0,
                vy_mps=0.0,
                speed_mps=0.0,
                speed_kmh=0.0,
                acceleration_mps2=0.0,
                heading_deg=0.0,
                is_approaching=False
            )

        return self.trackers[track_id].update(ground_x, ground_y, timestamp)

    def prune(self, active_track_ids: List[int]):
        """Removes trackers for objects that have left the scene."""
        current_ids = set(self.trackers.keys())
        active_set = set(active_track_ids)
        for tid in current_ids - active_set:
            del self.trackers[tid]

    def reset(self):
        """Clears all trackers."""
        self.trackers.clear()
