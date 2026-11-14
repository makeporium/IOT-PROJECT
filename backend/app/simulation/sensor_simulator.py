"""Virtual sensor simulation: mmWave Radar with configurable Gaussian noise and range limits."""

import random
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from app.simulation.ground_truth import GroundTruthState


class VirtualRadarReading(BaseModel):
    """Synthetic mmWave Radar sensor measurement."""
    sensor_id: str = "radar_rear_left_77ghz"
    target_id: Optional[int] = None
    target_detected: bool = False
    measured_distance_m: Optional[float] = None
    measured_velocity_mps: Optional[float] = None
    azimuth_deg: Optional[float] = None
    snr_db: Optional[float] = None
    source_mode: str = "SIMULATION"
    measurement_type: str = "SIMULATED"
    is_simulated: bool = True
    noise_sigma_distance: float = 0.15
    noise_sigma_velocity: float = 0.25


class VirtualRadarSimulator:
    """Simulates 77 GHz automotive FMCW radar mounted at the rear corner of the parked vehicle."""

    def __init__(
        self,
        max_range_m: float = 50.0,
        min_range_m: float = 0.5,
        fov_deg: float = 120.0,
        noise_distance_m: float = 0.15,
        noise_velocity_mps: float = 0.20
    ):
        self.max_range = max_range_m
        self.min_range = min_range_m
        self.fov = fov_deg
        self.noise_dist = noise_distance_m
        self.noise_vel = noise_velocity_mps

    def measure(self, target: Optional[GroundTruthState]) -> VirtualRadarReading:
        """Generates a realistic noisy radar observation from a ground-truth object."""
        if target is None:
            return VirtualRadarReading(target_detected=False)

        dist_true = target.actual_distance_m
        if dist_true < self.min_range or dist_true > self.max_range:
            return VirtualRadarReading(target_detected=False)

        # Inject realistic Gaussian measurement noise
        dist_meas = max(0.1, dist_true + random.gauss(0, self.noise_dist))
        vel_meas = target.velocity_mps + random.gauss(0, self.noise_vel)
        azimuth = target.heading_deg + random.gauss(0, 1.0)
        snr = max(5.0, 30.0 - 0.4 * dist_true + random.gauss(0, 1.5))

        return VirtualRadarReading(
            target_id=target.object_id,
            target_detected=True,
            measured_distance_m=round(dist_meas, 2),
            measured_velocity_mps=round(vel_meas, 2),
            azimuth_deg=round(azimuth, 1),
            snr_db=round(snr, 1),
            noise_sigma_distance=self.noise_dist,
            noise_sigma_velocity=self.noise_vel
        )
