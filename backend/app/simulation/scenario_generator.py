"""Scenario generation for simulation runs and ML dataset generation."""

from typing import List, Dict, Any
from app.simulation.object_motion import ObjectMotionSimulator


class ScenarioGenerator:
    """Creates reproducible simulation scenarios with known ground truth."""

    @staticmethod
    def create_bicycle_overtake_scenario() -> List[ObjectMotionSimulator]:
        """A cyclist approaching at 4.5 m/s (~16 km/h) along the door opening corridor."""
        return [
            ObjectMotionSimulator(
                object_id=101,
                object_type="bicycle",
                start_x_m=-1.35,
                start_y_m=-25.0,  # 25m behind
                speed_mps=4.5,
                lateral_speed_mps=0.0
            )
        ]

    @staticmethod
    def create_motorcycle_fast_approach() -> List[ObjectMotionSimulator]:
        """A motorcycle lane-filtering at 11.0 m/s (~40 km/h) close to the car door."""
        return [
            ObjectMotionSimulator(
                object_id=102,
                object_type="motorcycle",
                start_x_m=-1.50,
                start_y_m=-45.0,
                speed_mps=11.0,
                lateral_speed_mps=0.1
            )
        ]

    @staticmethod
    def create_safe_distant_traffic() -> List[ObjectMotionSimulator]:
        """A car in the adjacent lane (3.5m lateral offset) passing safely."""
        return [
            ObjectMotionSimulator(
                object_id=103,
                object_type="car",
                start_x_m=-3.80,
                start_y_m=-30.0,
                speed_mps=8.0,
                lateral_speed_mps=0.0
            )
        ]
