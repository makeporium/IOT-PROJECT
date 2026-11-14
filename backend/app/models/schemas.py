"""Pydantic schemas for data interchange and API contracts."""

from enum import Enum
from typing import List, Optional, Tuple, Dict, Any
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BoundingBox(BaseModel):
    x1: float = Field(..., description="Top-left X coordinate in pixels")
    y1: float = Field(..., description="Top-left Y coordinate in pixels")
    x2: float = Field(..., description="Bottom-right X coordinate in pixels")
    y2: float = Field(..., description="Bottom-right Y coordinate in pixels")
    width: float = Field(..., description="Box width in pixels")
    height: float = Field(..., description="Box height in pixels")
    center_x: float = Field(..., description="Centroid X coordinate in pixels")
    center_y: float = Field(..., description="Centroid Y coordinate in pixels")
    foot_x: float = Field(..., description="Ground contact point X coordinate ((x1+x2)/2)")
    foot_y: float = Field(..., description="Ground contact point Y coordinate (y2)")


class TrackPoint(BaseModel):
    x: float = Field(..., description="Ground contact X in pixels")
    y: float = Field(..., description="Ground contact Y in pixels")
    frame_idx: int = Field(..., description="Frame index of observation")
    timestamp_sec: float = Field(..., description="Video timestamp in seconds")


class DetectedObject(BaseModel):
    class_id: int
    class_name: str
    confidence: float
    bbox: BoundingBox
    track_id: Optional[int] = None


class TrackedObject(BaseModel):
    track_id: int
    class_id: int
    class_name: str
    confidence: float
    bbox: BoundingBox
    footpoint: Tuple[float, float]
    history: List[TrackPoint] = Field(default_factory=list)
    track_age_frames: int = 1
    is_active: bool = True
    
    # Phase 2 Metric Geometry & Kinematics
    metric_x: Optional[float] = Field(None, description="Lateral position relative to vehicle (meters)")
    metric_y: Optional[float] = Field(None, description="Longitudinal distance behind vehicle (meters)")
    distance_m: Optional[float] = Field(None, description="Euclidean distance to camera (meters)")
    velocity_x_mps: Optional[float] = Field(None, description="Lateral velocity (m/s)")
    velocity_y_mps: Optional[float] = Field(None, description="Longitudinal velocity (m/s, negative = approaching)")
    speed_mps: Optional[float] = Field(None, description="Ground speed magnitude (m/s)")
    speed_kmh: Optional[float] = Field(None, description="Ground speed (km/h)")
    acceleration_mps2: Optional[float] = Field(None, description="Longitudinal acceleration (m/s^2)")
    heading_deg: Optional[float] = Field(None, description="Movement heading angle (degrees)")
    is_approaching: bool = Field(False, description="Whether target is closing in on vehicle")
    
    # Phase 2 TTC & Collision Horizon
    ttc_seconds: Optional[float] = Field(None, description="Time-To-Collision in seconds")
    time_to_exit_seconds: Optional[float] = Field(None, description="Time to exit door zone (seconds)")
    is_conflict_predicted: bool = Field(False, description="Whether predicted trajectory intersects door zone")
    hazard_urgency: str = Field("SAFE", description="SAFE, WARNING, or CRITICAL")
    
    # Uncertainty & Confidence
    distance_confidence: float = Field(1.0, description="Confidence in geometric distance estimate (0.0 to 1.0)")
    uncertainty_m: float = Field(0.0, description="Estimated margin of error (+/- meters)")
    predicted_path: List[Dict[str, Any]] = Field(default_factory=list, description="Projected future waypoints")


class FrameTelemetry(BaseModel):
    frame_index: int
    timestamp_sec: float
    inference_time_ms: float
    tracking_time_ms: float
    total_time_ms: float
    fps: float
    active_tracks_count: int
    critical_conflicts_count: int = 0
    warning_conflicts_count: int = 0
    objects: List[TrackedObject] = Field(default_factory=list)


class VideoJobInfo(BaseModel):
    job_id: str
    filename: str
    source_type: str = "UPLOAD"  # UPLOAD or SAMPLE
    status: JobStatus = JobStatus.PENDING
    progress_percent: float = 0.0
    total_frames: int = 0
    processed_frames: int = 0
    fps: float = 0.0
    avg_latency_ms: float = 0.0
    annotated_video_url: Optional[str] = None
    telemetry_url: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


class ProcessVideoRequest(BaseModel):
    source_type: str = "SAMPLE"  # "SAMPLE" or "UPLOAD"
    sample_filename: Optional[str] = None
    upload_filename: Optional[str] = None
    conf_threshold: Optional[float] = 0.35
    stride: Optional[int] = 1
    camera_height_m: Optional[float] = 1.15
    pitch_deg: Optional[float] = 8.5


class SampleVideo(BaseModel):
    id: str
    filename: str
    filesize_bytes: int
    filesize_mb: float


class HealthResponse(BaseModel):
    status: str
    version: str
    model_loaded: bool
    model_name: str
    model_path: str
    device: str
    target_classes: List[str]
