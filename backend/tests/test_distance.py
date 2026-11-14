"""Unit tests for camera geometry, pinhole projection, and distance estimation."""

import pytest
import math
from app.distance_estimation.camera_geometry import (
    PinholeGroundProjector,
    CameraParameters,
    GroundCoordinate
)


def test_pinhole_geometry_initialization():
    """Verify camera parameter intrinsics and focal length computation."""
    params = CameraParameters(
        camera_height_m=1.15,
        pitch_deg=8.5,
        hfov_deg=72.0,
        image_width=1920,
        image_height=1080
    )
    projector = PinholeGroundProjector(params)
    assert projector.fx > 1000.0
    assert projector.cx == 960.0
    assert projector.cy == 540.0


def test_ground_projection_monotonicity():
    """Verify that lower screen pixels (closer to road) correspond to smaller longitudinal distances."""
    projector = PinholeGroundProjector()
    
    # Point near bottom of screen (road near vehicle bumper, v=1000)
    close_coord = projector.pixel_to_ground(u=960, v=1000)
    assert close_coord.is_valid
    assert 1.0 < close_coord.y_m < 5.0
    assert abs(close_coord.x_m) < 0.2  # Centered column -> X close to 0

    # Point mid-range (v=620)
    mid_coord = projector.pixel_to_ground(u=960, v=620)
    assert mid_coord.is_valid
    assert mid_coord.y_m > close_coord.y_m

    # Distant point above optical center (v=400)
    far_coord = projector.pixel_to_ground(u=960, v=400)
    assert far_coord.is_valid
    assert far_coord.y_m > mid_coord.y_m
    assert far_coord.y_m > 15.0


def test_ground_projection_lateral_symmetry():
    """Verify that left image pixels produce negative X (driver side) and right produce positive X."""
    projector = PinholeGroundProjector()
    
    left_coord = projector.pixel_to_ground(u=400, v=850)
    right_coord = projector.pixel_to_ground(u=1520, v=850)
    
    assert left_coord.x_m < 0.0  # Left / Driver side
    assert right_coord.x_m > 0.0  # Right side
    assert abs(abs(left_coord.x_m) - abs(right_coord.x_m)) < 0.5  # Symmetric around center
    assert abs(left_coord.y_m - right_coord.y_m) < 0.1  # Same row -> same distance


def test_inverse_projection_roundtrip():
    """Verify that (X, Y) -> (u, v) -> (X, Y) roundtrip preserves coordinates."""
    projector = PinholeGroundProjector()
    orig_x, orig_y = -1.25, 8.50
    
    pixel = projector.ground_to_pixel(orig_x, orig_y)
    assert pixel is not None
    u, v = pixel
    
    reproj = projector.pixel_to_ground(u, v)
    assert abs(reproj.x_m - orig_x) < 0.15
    assert abs(reproj.y_m - orig_y) < 0.25


def test_uncertainty_growth_with_distance():
    """Verify that measurement uncertainty grows and confidence drops with distance."""
    projector = PinholeGroundProjector()
    
    near_pt = projector.pixel_to_ground(u=960, v=950)   # ~2.5m
    far_pt = projector.pixel_to_ground(u=960, v=400)    # ~26.8m
    
    assert far_pt.uncertainty_m > near_pt.uncertainty_m
    assert near_pt.confidence > far_pt.confidence
