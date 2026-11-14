"""Camera geometry and ground plane calibration endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.distance_estimation.camera_geometry import (
    CameraParameters,
    GroundCoordinate,
    PinholeGroundProjector
)

router = APIRouter(prefix="/calibration", tags=["Camera Geometry & Calibration"])

# Global calibration instance
SHARED_PROJECTOR = PinholeGroundProjector()


class PixelProjectionRequest(BaseModel):
    pixel_u: float = Field(..., description="Image horizontal coordinate (column)")
    pixel_v: float = Field(..., description="Image vertical coordinate (row / footpoint)")


@router.get("/camera", response_model=CameraParameters)
def get_camera_parameters():
    """Returns active camera mounting and optical calibration parameters."""
    return SHARED_PROJECTOR.params


@router.post("/camera", response_model=CameraParameters)
def update_camera_parameters(params: CameraParameters):
    """Updates camera height, pitch angle, or field of view."""
    SHARED_PROJECTOR.params = params
    SHARED_PROJECTOR._update_intrinsics()
    return SHARED_PROJECTOR.params


@router.post("/project", response_model=GroundCoordinate)
def project_pixel_to_ground(req: PixelProjectionRequest):
    """Calculates physical (X, Y) metric ground position for a pixel footpoint."""
    return SHARED_PROJECTOR.pixel_to_ground(req.pixel_u, req.pixel_v)
