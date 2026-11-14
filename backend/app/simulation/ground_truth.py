"""Ground-truth definitions and data structures for simulation scenarios."""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class GroundTruthState(BaseModel):
    """Ground-truth spatial and kinematic state of a road participant in simulation."""
    object_id: int
    object_type: str  # bicycle, motorcycle, car, pedestrian
    x_pos_m: float = Field(..., description="Lateral position relative to parked car center (m)")
    y_pos_m: float = Field(..., description="Longitudinal position behind/ahead of car door (m)")
    velocity_mps: float = Field(..., description="Longitudinal speed (m/s)")
    lateral_velocity_mps: float = Field(0.0, description="Lateral drift speed (m/s)")
    heading_deg: float = Field(0.0, description="Heading relative to roadway (deg)")
    actual_distance_m: float = Field(..., description="Euclidean distance to driver door handle (m)")
    actual_ttc_seconds: Optional[float] = Field(None, description="Ground truth time to door zone collision")
    intersects_door_zone: bool = Field(False, description="Whether currently within door swing area")
    source_mode: str = "SIMULATION"
    measurement_type: str = "ACTUAL"


class GroundTruthTelemetry(BaseModel):
    """Simulation frame timestamped ground truth."""
    scenario_id: str
    scenario_name: str
    timestamp_sec: float
    parked_car_door_open: bool = False
    parked_car_handle_touched: bool = False
    participants: List[GroundTruthState] = Field(default_factory=list)
