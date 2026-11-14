"""Health check and system diagnostic endpoints."""

import os
import torch
from fastapi import APIRouter
from app.config import settings
from app.models.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["System Diagnostics"])


@router.get("", response_model=HealthResponse)
def get_system_health():
    """Returns real-time health, YOLO model status, and device acceleration."""
    model_exists = os.path.exists(settings.MODEL_PATH)
    device_name = "cuda" if torch.cuda.is_available() else "cpu"
    if torch.cuda.is_available():
        device_name += f" ({torch.cuda.get_device_name(0)})"

    return HealthResponse(
        status="ONLINE",
        version=settings.VERSION,
        model_loaded=model_exists,
        model_name=os.path.basename(settings.MODEL_PATH),
        model_path=settings.MODEL_PATH,
        device=device_name,
        target_classes=settings.TARGET_CLASSES
    )
