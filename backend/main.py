"""DOORSAFE FastAPI Application Entrypoint."""

import sys
import logging
from pathlib import Path
from contextlib import asynccontextmanager

# NumPy 2.x backward compatibility safeguard for ByteTrack / Ultralytics
import numpy as np
if not hasattr(np, 'matrix'):
    try:
        from numpy.matrixlib import matrix
        np.matrix = matrix
    except Exception:
        class matrix(np.ndarray):
            pass
        np.matrix = matrix

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

# Ensure backend directory is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.config import settings
from app.api.routes_health import router as health_router
from app.api.routes_video import router as video_router
from app.api.routes_calibration import router as calibration_router
from app.api.routes_safety import router as safety_router
from app.api.routes_iot import router as iot_router
from app.api.routes_events import router as events_router
from app.api.routes_twin import router as twin_router
from app.api.routes_video import get_detector

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("doorsafe.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown hooks."""
    logger.info("=" * 60)
    logger.info(f"   {settings.PROJECT_TITLE} - v{settings.VERSION}")
    logger.info("=" * 60)
    logger.info(f"Model path: {settings.MODEL_PATH}")
    logger.info(f"Target classes: {settings.TARGET_CLASSES}")
    
    # Pre-warm YOLO model and ByteTrack tracker
    try:
        det = get_detector()
        logger.info(f"YOLO detector successfully initialized on device: {det.device}")
    except Exception as e:
        logger.error(f"Failed to initialize YOLO model at startup: {e}")

    # Phase 4: Start MQTT Automotive Gateway
    from app.persistence.event_store import get_event_store
    event_store = get_event_store()
    logger.info("Phase 5 flight recorder ready: %s", event_store.database_path)

    # Phase 4: Start MQTT Automotive Gateway
    if getattr(settings, "MQTT_ENABLED", True):
        try:
            from app.iot.mqtt_client import get_mqtt_gateway
            from app.iot.bridge import get_iot_bridge
            mqtt_client = get_mqtt_gateway()
            mqtt_client.start()
            get_iot_bridge()
            logger.info(f"Phase 4 MQTT Gateway started on {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}")
        except Exception as e:
            logger.warning(f"Could not start MQTT Gateway: {e}")

    yield

    # Phase 4: Clean MQTT Gateway Shutdown
    if getattr(settings, "MQTT_ENABLED", True):
        try:
            from app.iot.mqtt_client import get_mqtt_gateway
            get_mqtt_gateway().stop()
        except Exception:
            pass

    logger.info("DOORSAFE Server shutting down.")


# Create FastAPI application
app = FastAPI(
    title=settings.PROJECT_TITLE,
    version=settings.VERSION,
    description="AI-Powered IoT-Based Vehicle Door Opening Safety System",
    lifespan=lifespan
)

# Enable CORS for local development and future Vite React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers (support both /api and /api/v1 prefixes)
app.include_router(health_router, prefix=settings.API_V1_STR)
app.include_router(video_router, prefix=settings.API_V1_STR)
app.include_router(calibration_router, prefix=settings.API_V1_STR)
app.include_router(safety_router, prefix=settings.API_V1_STR)
app.include_router(iot_router, prefix=settings.API_V1_STR)
app.include_router(events_router, prefix=settings.API_V1_STR)
app.include_router(twin_router, prefix=settings.API_V1_STR)

# Also expose under /api/v1 for standard REST client conventions
if settings.API_V1_STR != "/api/v1":
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(video_router, prefix="/api/v1")
    app.include_router(calibration_router, prefix="/api/v1")
    app.include_router(safety_router, prefix="/api/v1")
    app.include_router(iot_router, prefix="/api/v1")
    app.include_router(events_router, prefix="/api/v1")
    app.include_router(twin_router, prefix="/api/v1")

# Mount static web UI, sample videos, and processed outputs
static_dir = Path(__file__).resolve().parent / "app" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

if settings.OUTPUT_DIR.exists():
    app.mount("/outputs", StaticFiles(directory=str(settings.OUTPUT_DIR)), name="outputs")

if settings.SAMPLES_DIR.exists():
    app.mount("/samples", StaticFiles(directory=str(settings.SAMPLES_DIR)), name="samples")

@app.get("/", include_in_schema=False)
def root_ui():
    """Serves the DOORSAFE Video Analysis & Tracking Dashboard."""
    index_file = static_dir / "index.html"
    return FileResponse(str(index_file))


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        app_dir=str(BASE_DIR)
    )
