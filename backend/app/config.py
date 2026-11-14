"""Configuration management for DOORSAFE Backend."""

import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve base directories
CURRENT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CURRENT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent

class Settings(BaseSettings):
    PROJECT_NAME: str = "DOORSAFE"
    PROJECT_TITLE: str = "DOORSAFE - Vehicle Door Opening Safety System"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"
    
    # Computer Vision & Model paths
    MODEL_PATH: str = str(BACKEND_DIR / "models" / "yolo26n.pt")
    DEFAULT_CONF_THRESHOLD: float = 0.35
    DEFAULT_IOU_THRESHOLD: float = 0.45
    TRACKER_TYPE: str = "bytetrack.yaml"
    
    # Target Automotive Classes (COCO IDs: 0=person, 1=bicycle, 2=car, 3=motorcycle, 5=bus, 7=truck)
    TARGET_CLASSES: List[str] = [
        "person",
        "bicycle",
        "motorcycle",
        "car",
        "bus",
        "truck"
    ]
    
    # Storage directories
    UPLOAD_DIR: Path = BACKEND_DIR / "data" / "uploads"
    OUTPUT_DIR: Path = BACKEND_DIR / "data" / "outputs"
    SAMPLES_DIR: Path = BACKEND_DIR / "data" / "samples"
    STATIC_DIR: Path = CURRENT_DIR / "static"
    
    # Tracking parameters
    MAX_TRAJECTORY_POINTS: int = 50
    INACTIVE_TRACK_THRESHOLD: int = 30  # prune track after 30 lost frames
    
    # Server configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    
    model_config = SettingsConfigDict(case_sensitive=True, env_file=".env", extra="ignore")

# Instantiate singleton settings
settings = Settings()

# Ensure directories exist
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
settings.STATIC_DIR.mkdir(parents=True, exist_ok=True)
