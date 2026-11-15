"""Phase 6 deterministic ground-truth digital-twin session."""

from __future__ import annotations

from typing import Dict, List

from app.simulation.object_motion import ObjectMotionSimulator
from app.simulation.scenario_generator import ScenarioGenerator
from app.simulation.sensor_simulator import VirtualRadarSimulator
from app.simulation.vehicle_physics import VehiclePhysics


class DigitalTwinSession:
    """A small, deterministic simulation that the UI can advance on demand."""

    SCENARIOS = {
        "bicycle_overtake": ("Cyclist in door corridor", ScenarioGenerator.create_bicycle_overtake_scenario),
        "motorcycle_fast": ("Fast motorcycle approach", ScenarioGenerator.create_motorcycle_fast_approach),
        "safe_adjacent_car": ("Safe adjacent-lane car", ScenarioGenerator.create_safe_distant_traffic),
    }

    def __init__(self):
        self.physics = VehiclePhysics()
        self.radar = VirtualRadarSimulator()
        self.reset()

    def reset(self, scenario_id: str = "bicycle_overtake") -> Dict:
        if scenario_id not in self.SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario_id}")
        self.scenario_id = scenario_id
        self.scenario_name, factory = self.SCENARIOS[scenario_id]
        self.participants: List[ObjectMotionSimulator] = factory()
        self.elapsed_s = 0.0
        self.door_open_requested = False
        return self.snapshot()

    def request_door_open(self) -> Dict:
        """Models a passenger pressing the interior/exterior door handle."""
        self.door_open_requested = True
        return self.snapshot()

    def close_door(self) -> Dict:
        self.door_open_requested = False
        return self.snapshot()

    def step(self, dt_s: float = 0.1) -> Dict:
        dt_s = max(0.02, min(float(dt_s), 1.0))
        self.elapsed_s += dt_s
        states = [participant.step(dt_s) for participant in self.participants]
        return self._snapshot_from(states)

    def snapshot(self) -> Dict:
        return self._snapshot_from([participant.step(0.0) for participant in self.participants])

    def _snapshot_from(self, states) -> Dict:
        readings = [self.radar.measure(state).model_dump() for state in states]
        threatening = [state for state in states if state.intersects_door_zone]
        safety_state = "CRITICAL" if threatening else "SAFE"
        door_allowed = safety_state == "SAFE"
        door_open = bool(self.door_open_requested and door_allowed)
        return {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "timestamp_sec": round(self.elapsed_s, 2),
            "car": {
                "length_m": self.physics.length,
                "width_m": self.physics.width,
                "door_zone_polygon": self.physics.get_door_zone_polygon(),
                "door_hinge": {"x_m": self.physics.hinge_x, "y_m": self.physics.hinge_y},
            },
            "participants": [state.model_dump() for state in states],
            "virtual_radar": readings,
            "safety": {
                "state": safety_state,
                "threat_count": len(threatening),
                "door_open_requested": self.door_open_requested,
                "door_locked": not door_open,
                "door_angle_deg": 65 if door_open else 0,
                "reason": "Participant occupies the left door-swing corridor." if threatening else "Left door corridor clear.",
            },
            "ground_truth_note": "Positions and distances are exact simulation truth; radar contains deliberate measurement noise.",
        }


_session = DigitalTwinSession()


def get_digital_twin() -> DigitalTwinSession:
    return _session
