"""DOORSAFE Phase 3: Spatial Door-Zone Hazard Geometry."""

from dataclasses import dataclass
from typing import Tuple, List, Optional
import math


@dataclass
class DoorZonePolygon:
    """
    Represents the physical 2D spatial envelope where the vehicle's driver door swings open.
    
    Automotive Coordinate Reference (ISO 8855):
      - Origin (0, 0): Center of vehicle rear bumper on ground plane.
      - +X: Lateral Right (passenger side)
      - -X: Lateral Left (driver side in LHD vehicle)
      - +Y: Longitudinal Forward (ahead of vehicle)
      - -Y: Longitudinal Backward (behind vehicle, road approach direction)
      - Z: Vertical Up (ground = 0)
    """
    # Driver-side door opening boundary (meters)
    x_min: float = -2.20   # Maximum outward reach of open door panel + buffer
    x_max: float = -0.70   # Vehicle side sheet metal
    y_min: float = -0.80   # Trailing edge buffer behind open door swing
    y_max: float = 2.20    # Forward edge buffer near A-pillar hinge

    # Speed-dependent dynamic margin expansion
    speed_buffer_factor: float = 0.05  # Extra meters per km/h of closing speed

    def get_dynamic_bounds(self, speed_kmh: float = 0.0) -> Tuple[float, float, float, float]:
        """Calculates expanded door hazard boundary based on target closing speed."""
        expansion = max(0.0, speed_kmh * self.speed_buffer_factor)
        # Expand lateral and longitudinal bounds proportionally
        return (
            self.x_min - (expansion * 0.5),
            self.x_max,
            self.y_min - expansion,
            self.y_max + (expansion * 0.2)
        )

    def contains_point(self, x: float, y: float, speed_kmh: float = 0.0) -> bool:
        """Evaluates whether metric coordinate (x, y) lies inside the door danger zone."""
        x_min, x_max, y_min, y_max = self.get_dynamic_bounds(speed_kmh)
        return (x_min <= x <= x_max) and (y_min <= y <= y_max)

    def distance_to_zone(self, x: float, y: float) -> float:
        """
        Calculates Euclidean distance from metric point (x, y) to the nearest edge of the door zone.
        Returns 0.0 if the point is strictly inside the zone.
        """
        dx = 0.0
        if x < self.x_min:
            dx = self.x_min - x
        elif x > self.x_max:
            dx = x - self.x_max

        dy = 0.0
        if y < self.y_min:
            dy = self.y_min - y
        elif y > self.y_max:
            dy = y - self.y_max

        return math.hypot(dx, dy)

    def intersects_trajectory(
        self,
        x: float,
        y: float,
        vx: float,
        vy: float,
        time_horizon_s: float = 3.0,
        dt: float = 0.1
    ) -> Tuple[bool, Optional[float], Optional[float]]:
        """
        Checks whether constant-velocity linear trajectory intersects the door corridor.
        
        Returns:
            Tuple of (intersects: bool, entry_time_s: Optional[float], exit_time_s: Optional[float])
        """
        if self.contains_point(x, y):
            return True, 0.0, None

        steps = int(time_horizon_s / dt)
        entry_time = None
        exit_time = None

        for step in range(steps + 1):
            t = step * dt
            proj_x = x + (vx * t)
            proj_y = y + (vy * t)

            if self.contains_point(proj_x, proj_y):
                if entry_time is None:
                    entry_time = t
                exit_time = t
            elif entry_time is not None:
                # Exited the zone
                break

        intersects = entry_time is not None
        return intersects, entry_time, exit_time
