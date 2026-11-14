"""Camera calibration, pinhole geometry, and ground-plane inverse projection."""

import math
import numpy as np
from typing import Tuple, Optional, Dict, Any
from pydantic import BaseModel, Field


class CameraParameters(BaseModel):
    """Extrinsic and intrinsic camera calibration parameters."""
    camera_height_m: float = Field(1.15, description="Mounting height above road surface (meters)")
    pitch_deg: float = Field(8.5, description="Downward tilt angle towards asphalt (degrees)")
    roll_deg: float = Field(0.0, description="Roll angle (degrees)")
    yaw_deg: float = Field(0.0, description="Yaw orientation relative to car longitudinal axis (degrees)")
    hfov_deg: float = Field(72.0, description="Horizontal Field of View (degrees)")
    image_width: int = Field(1920, description="Frame width in pixels")
    image_height: int = Field(1080, description="Frame height in pixels")
    footpoint_pixel_sigma: float = Field(4.0, description="Estimated pixel noise on ground footpoint")


class GroundCoordinate(BaseModel):
    """Estimated metric position on the flat road ground plane (Z = 0)."""
    x_m: float = Field(..., description="Lateral position (meters, negative = left, positive = right)")
    y_m: float = Field(..., description="Longitudinal distance behind vehicle (meters)")
    distance_m: float = Field(..., description="Euclidean distance from camera (meters)")
    confidence: float = Field(..., description="Geometric distance confidence score (0.0 to 1.0)")
    uncertainty_m: float = Field(..., description="Estimated 1-sigma uncertainty margin (meters)")
    is_valid: bool = Field(True, description="Whether projection lies on valid ground plane")


class PinholeGroundProjector:
    """
    Projects 2D image footpoint pixels (u, v) onto the physical 3D ground plane (X, Y, Z=0)
    using calibrated camera mounting height and downward pitch angle.
    """

    def __init__(self, params: Optional[CameraParameters] = None):
        self.params = params or CameraParameters()
        self._update_intrinsics()

    def update_parameters(self, **kwargs):
        """Updates calibration settings dynamically."""
        current = self.params.model_dump()
        current.update(kwargs)
        self.params = CameraParameters(**current)
        self._update_intrinsics()

    def _update_intrinsics(self):
        """Calculates focal lengths and optical center from HFOV and resolution."""
        w = self.params.image_width
        h = self.params.image_height
        hfov_rad = math.radians(self.params.hfov_deg)

        # Focal lengths in pixels
        self.fx = (w / 2.0) / math.tan(hfov_rad / 2.0)
        self.fy = self.fx  # Assume square pixels
        self.cx = w / 2.0
        self.cy = h / 2.0
        self.pitch_rad = math.radians(self.params.pitch_deg)
        self.height = self.params.camera_height_m

    def set_resolution(self, width: int, height: int):
        """Adjusts calibration for a new video resolution."""
        if width != self.params.image_width or height != self.params.image_height:
            self.params.image_width = width
            self.params.image_height = height
            self._update_intrinsics()

    def pixel_to_ground(self, u: float, v: float) -> GroundCoordinate:
        """
        Calculates metric (X, Y) coordinates in meters on the road surface
        given the bottom-center footpoint (u, v) of a road participant.
        """
        # Vertical ray angle relative to optical center
        alpha = math.atan((v - self.cy) / self.fy)

        # Total depression angle below the horizontal plane
        gamma = self.pitch_rad + alpha

        # Horizon boundary check (ray pointing above ground)
        if gamma <= 0.015:  # ~0.85 degrees minimum ground clearance
            return GroundCoordinate(
                x_m=0.0,
                y_m=100.0,
                distance_m=100.0,
                confidence=0.05,
                uncertainty_m=35.0,
                is_valid=False
            )

        # Longitudinal distance Y (distance along optical ground plane)
        y = self.height / math.tan(gamma)

        # Lateral distance X (offset from camera centerline)
        # In planar pinhole geometry: X = Y * (u - cx) / fx * cos(alpha)
        x = (y * (u - self.cx)) / self.fx

        # Euclidean straight-line distance to camera
        dist = math.sqrt(x**2 + y**2)

        # Uncertainty estimation: dY/dv * sigma_v
        # d/dv [ h / tan(theta + atan((v-cy)/fy)) ] = - h * sec^2(gamma) * (1 / (fy * (1 + ((v-cy)/fy)^2))) / tan^2(gamma)
        # Simplified: dy/dv ~= Y^2 / (h * fy)
        dY_dv = (y ** 2) / (self.height * self.fy)
        sigma_v = self.params.footpoint_pixel_sigma
        uncertainty_y = abs(dY_dv * sigma_v)

        # Confidence drops as uncertainty grows or distance exceeds 50m
        # Also penalize if footpoint is truncated right at image borders
        boundary_penalty = 1.0
        if v >= (self.params.image_height - 6) or v <= (self.cy + 10):
            boundary_penalty = 0.5

        confidence = max(0.05, min(0.98, (1.0 / (1.0 + 0.12 * uncertainty_y + 0.02 * max(0.0, y - 25.0)))) * boundary_penalty)

        return GroundCoordinate(
            x_m=round(x, 2),
            y_m=round(y, 2),
            distance_m=round(dist, 2),
            confidence=round(confidence, 3),
            uncertainty_m=round(uncertainty_y, 2),
            is_valid=(y > 0.5 and y < 80.0)
        )

    def ground_to_pixel(self, x_m: float, y_m: float) -> Optional[Tuple[int, int]]:
        """Projects physical ground coordinates (X, Y) back into pixel coordinates (u, v)."""
        if y_m <= 0.1:
            return None

        # gamma = atan(h / y)
        gamma = math.atan(self.height / y_m)
        alpha = gamma - self.pitch_rad
        tan_alpha = math.tan(alpha)

        v = int(self.cy + self.fy * tan_alpha)
        u = int(self.cx + (x_m * self.fx) / y_m)

        if 0 <= u < self.params.image_width and 0 <= v < self.params.image_height:
            return (u, v)
        return None
