"""Trajectory path extrapolation and forward waypoint prediction."""

from typing import List, Tuple, Optional
from pydantic import BaseModel, Field


class TrajectoryWaypoint(BaseModel):
    """A projected future spatial coordinate."""
    future_time_sec: float
    x_m: float
    y_m: float
    pixel_u: Optional[int] = None
    pixel_v: Optional[int] = None


class TrajectoryPredictor:
    """Projects kinematic paths forward in time (0.5 to 3.0 seconds)."""

    def __init__(self, time_horizons: Optional[List[float]] = None):
        self.horizons = time_horizons or [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]

    def predict(
        self,
        x0: float,
        y0: float,
        vx: float,
        vy: float,
        ground_to_pixel_fn=None
    ) -> List[TrajectoryWaypoint]:
        """Calculates future spatial waypoints."""
        waypoints: List[TrajectoryWaypoint] = []
        for t in self.horizons:
            px = x0 + vx * t
            py = y0 + vy * t
            
            u_px, v_px = None, None
            if ground_to_pixel_fn:
                proj = ground_to_pixel_fn(px, py)
                if proj:
                    u_px, v_px = proj

            waypoints.append(
                TrajectoryWaypoint(
                    future_time_sec=t,
                    x_m=round(px, 2),
                    y_m=round(py, 2),
                    pixel_u=u_px,
                    pixel_v=v_px
                )
            )
        return waypoints
