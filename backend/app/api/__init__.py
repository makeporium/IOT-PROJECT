"""FastAPI API routers for DOORSAFE."""
from .routes_health import router as health_router
from .routes_video import router as video_router
from .routes_calibration import router as calibration_router

__all__ = ["health_router", "video_router", "calibration_router"]
