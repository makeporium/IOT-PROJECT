"""Unit tests for kinematic filtering, Time-To-Collision (TTC), and trajectory extrapolation."""

import pytest
from app.trajectory.kinematics import KinematicEstimator
from app.trajectory.ttc_calculator import TTCCalculator
from app.trajectory.predictor import TrajectoryPredictor


def test_kinematic_velocity_convergence():
    """Verify that the Alpha-Beta filter smooths positions and converges to correct velocity."""
    estimator = KinematicEstimator()
    track_id = 42

    # Simulate object approaching at 5 m/s: y starts at 20m, decreasing by 0.166m every frame (at 30 fps)
    fps = 30.0
    dt = 1.0 / fps
    true_vy = -5.0  # Approaching (negative vy)

    current_y = 20.0
    timestamp = 0.0

    for i in range(25):
        timestamp += dt
        current_y += true_vy * dt
        kin = estimator.update(track_id=track_id, ground_x=-1.20, ground_y=current_y, timestamp=timestamp)

    # Filter should converge close to true velocity of -5.0 m/s
    assert abs(kin.vy_mps - true_vy) < 0.6
    assert kin.is_approaching is True
    assert kin.speed_kmh > 15.0  # 5 m/s = 18 km/h


def test_ttc_critical_door_zone_conflict():
    """Verify that an oncoming cyclist along the door corridor triggers CRITICAL TTC."""
    calc = TTCCalculator()

    # Cyclist 8 meters behind, lateral offset -1.3m (in door swing line), closing at -5.0 m/s
    res = calc.calculate(
        x_m=-1.30,
        y_m=8.0,
        vx_mps=0.0,
        vy_mps=-5.0
    )

    assert res.is_conflict_predicted is True
    assert res.ttc_seconds is not None
    # 8m / 5m/s ~ 1.6 seconds to door arrival
    assert 1.2 <= res.ttc_seconds <= 1.8
    assert res.hazard_urgency == "CRITICAL"


def test_ttc_safe_distant_lateral_lane():
    """Verify that a vehicle in the adjacent lane (3.8m offset) is recognized as not entering the door zone."""
    calc = TTCCalculator()

    # Car at X = -3.8m, Y = 15m, closing at -8.0 m/s in adjacent lane
    res = calc.calculate(
        x_m=-3.80,
        y_m=15.0,
        vx_mps=0.0,
        vy_mps=-8.0
    )

    assert res.is_conflict_predicted is False
    assert res.hazard_urgency in ["SAFE", "WARNING"]


def test_trajectory_predictor_waypoints():
    """Verify that the trajectory predictor extrapolates forward waypoints."""
    predictor = TrajectoryPredictor(time_horizons=[1.0, 2.0])
    
    waypoints = predictor.predict(
        x0=-1.20,
        y0=12.0,
        vx=0.0,
        vy=-4.0
    )

    assert len(waypoints) == 2
    # At t=1.0s: y = 12 - 4 = 8.0m
    assert waypoints[0].y_m == 8.0
    # At t=2.0s: y = 12 - 8 = 4.0m
    assert waypoints[1].y_m == 4.0
