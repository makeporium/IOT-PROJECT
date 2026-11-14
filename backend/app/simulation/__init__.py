"""Simulation package for DOORSAFE: ground-truth road scenarios, kinematics, and virtual sensors."""

from .scenario_generator import ScenarioGenerator
from .vehicle_physics import VehiclePhysics
from .object_motion import ObjectMotionSimulator
from .ground_truth import GroundTruthTelemetry
from .sensor_simulator import VirtualRadarSimulator

__all__ = [
    "ScenarioGenerator",
    "VehiclePhysics",
    "ObjectMotionSimulator",
    "GroundTruthTelemetry",
    "VirtualRadarSimulator"
]
