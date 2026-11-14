"""Time-To-Collision (TTC) and Door-Zone Entry/Exit Horizon calculator."""

import math
from typing import Optional, Tuple
from pydantic import BaseModel, Field


class TTCResult(BaseModel):
    """Evaluation result for collision horizon."""
    ttc_seconds: Optional[float] = Field(None, description="Time to entry into danger zone (seconds)")
    time_to_exit_seconds: Optional[float] = Field(None, description="Time to exit danger zone (seconds)")
    is_conflict_predicted: bool = Field(False, description="Whether trajectory will intersect door zone")
    is_approaching: bool = Field(False, description="Whether distance is closing")
    closing_speed_mps: float = Field(0.0, description="Rate of approach towards door (m/s)")
    hazard_urgency: str = Field("SAFE", description="SAFE, WARNING, or CRITICAL based on TTC")


class TTCCalculator:
    """
    Computes Time-To-Collision using full 2D motion geometry rather than naive distance/speed.
    Analyzes temporal intersection with the driver-side door opening danger corridor.
    """

    def __init__(
        self,
        # Driver-side door danger envelope in vehicle metric space
        door_x_min: float = -2.10,  # ~2.1m out from car center (full door swing + buffer)
        door_x_max: float = -0.75,  # Car flank
        door_y_min: float = -0.60,  # Just ahead of door
        door_y_max: float = 1.80,   # Immediate rear of front door
        critical_ttc_threshold: float = 2.50,  # Seconds
        warning_ttc_threshold: float = 4.50    # Seconds
    ):
        self.x_min = door_x_min
        self.x_max = door_x_max
        self.y_min = door_y_min
        self.y_max = door_y_max
        self.critical_ttc = critical_ttc_threshold
        self.warning_ttc = warning_ttc_threshold

    def calculate(
        self,
        x_m: float,
        y_m: float,
        vx_mps: float,
        vy_mps: float
    ) -> TTCResult:
        """
        Calculates time of entry and exit into the door zone corridor.
        
        Args:
            x_m: Current lateral position in meters
            y_m: Current longitudinal distance in meters (positive = behind vehicle)
            vx_mps: Lateral velocity in m/s
            vy_mps: Longitudinal velocity in m/s (negative = approaching vehicle)
        """
        is_closing = (vy_mps < -0.30)
        closing_speed = abs(vy_mps) if is_closing else 0.0

        # If object is moving away or already past the door
        if vy_mps >= 0.0 and y_m < self.y_min:
            return TTCResult(
                ttc_seconds=None,
                time_to_exit_seconds=None,
                is_conflict_predicted=False,
                is_approaching=False,
                closing_speed_mps=0.0,
                hazard_urgency="SAFE"
            )

        # 1. Calculate time windows for X intersection
        # x(t) = x_m + vx * t  =>  self.x_min <= x_m + vx * t <= self.x_max
        tx_enter, tx_exit = self._get_1d_time_interval(x_m, vx_mps, self.x_min, self.x_max)

        # 2. Calculate time windows for Y intersection
        # y(t) = y_m + vy * t  =>  self.y_min <= y_m + vy * t <= self.y_max
        ty_enter, ty_exit = self._get_1d_time_interval(y_m, vy_mps, self.y_min, self.y_max)

        # Overlap of the two time intervals [tx_enter, tx_exit] and [ty_enter, ty_exit]
        t_enter = max(tx_enter, ty_enter)
        t_exit = min(tx_exit, ty_exit)

        # A collision occurs if the intervals overlap in the future (t_enter <= t_exit and t_exit >= 0)
        if t_enter <= t_exit and t_exit >= 0.0:
            actual_ttc = max(0.0, t_enter)
            
            # Determine urgency level based on TTC horizon
            if actual_ttc <= self.critical_ttc:
                urgency = "CRITICAL"
            elif actual_ttc <= self.warning_ttc:
                urgency = "WARNING"
            else:
                urgency = "SAFE"

            return TTCResult(
                ttc_seconds=round(actual_ttc, 2),
                time_to_exit_seconds=round(t_exit, 2),
                is_conflict_predicted=True,
                is_approaching=is_closing,
                closing_speed_mps=round(closing_speed, 2),
                hazard_urgency=urgency
            )

        # Fallback: Object is in adjacent lane or not directly entering the door zone
        # Compute pure longitudinal TTC as secondary advisory if approaching within 3 meters laterally
        if is_closing and abs(x_m) < 3.2 and y_m > 0:
            pure_long_ttc = y_m / abs(vy_mps)
            if pure_long_ttc < self.warning_ttc:
                return TTCResult(
                    ttc_seconds=round(pure_long_ttc, 2),
                    time_to_exit_seconds=None,
                    is_conflict_predicted=False,
                    is_approaching=True,
                    closing_speed_mps=round(closing_speed, 2),
                    hazard_urgency="WARNING"
                )

        return TTCResult(
            ttc_seconds=None,
            time_to_exit_seconds=None,
            is_conflict_predicted=False,
            is_approaching=is_closing,
            closing_speed_mps=round(closing_speed, 2),
            hazard_urgency="SAFE"
        )

    def _get_1d_time_interval(
        self,
        p0: float,
        v: float,
        bound_min: float,
        bound_max: float
    ) -> Tuple[float, float]:
        """Calculates interval [t_min, t_max] where bound_min <= p0 + v*t <= bound_max."""
        if abs(v) < 1e-4:
            # Not moving in this dimension
            if bound_min <= p0 <= bound_max:
                return (-float('inf'), float('inf'))
            return (float('inf'), -float('inf'))

        t1 = (bound_min - p0) / v
        t2 = (bound_max - p0) / v

        return (min(t1, t2), max(t1, t2))
