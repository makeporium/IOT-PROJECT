"""Video processing, upload, streaming, and telemetry endpoints."""

import os
import uuid
import time
import json
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.models.schemas import (
    VideoJobInfo,
    JobStatus,
    ProcessVideoRequest,
    SampleVideo
)
from app.computer_vision.pipeline import VideoProcessingPipeline
from app.computer_vision.detector import YOLODetector

# NumPy 2.x backward compatibility safeguard
import numpy as np
if not hasattr(np, 'matrix'):
    try:
        from numpy.matrixlib import matrix
        np.matrix = matrix
    except Exception:
        class matrix(np.ndarray):
            pass
        np.matrix = matrix

logger = logging.getLogger("doorsafe.api.video")
router = APIRouter(prefix="/video", tags=["Video Processing & Tracking"])

# In-memory job registry
JOBS: Dict[str, Dict[str, Any]] = {}
SHARED_DETECTOR: Optional[YOLODetector] = None


def scan_existing_jobs():
    """Scans output directory to restore existing completed jobs upon startup or page reload."""
    if not settings.OUTPUT_DIR.exists():
        return
    for telem_file in settings.OUTPUT_DIR.glob("*_telemetry.json"):
        job_id = telem_file.stem.replace("_telemetry", "")
        annotated_video = settings.OUTPUT_DIR / f"{job_id}_annotated.mp4"
        if not annotated_video.exists():
            continue
        try:
            with open(telem_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            meta = data.get("meta", {})
            JOBS[job_id] = {
                "job_id": job_id,
                "filename": meta.get("input_video", f"Job {job_id}"),
                "source_type": "SAMPLE",
                "status": JobStatus.COMPLETED,
                "progress_percent": 100.0,
                "total_frames": meta.get("total_frames_processed", 0),
                "processed_frames": meta.get("total_frames_processed", 0),
                "fps": meta.get("avg_fps", 0.0),
                "avg_latency_ms": meta.get("avg_inference_latency_ms", 0.0),
                "annotated_video_url": f"/api/video/jobs/{job_id}/stream",
                "telemetry_url": f"/api/video/jobs/{job_id}/telemetry",
                "total_unique_tracks": meta.get("total_unique_tracks", 0),
                "created_at": meta.get("timestamp", datetime.now().isoformat()),
                "completed_at": meta.get("timestamp", datetime.now().isoformat()),
                "error_message": None,
                "output_video_path": str(annotated_video),
                "telemetry_json_path": str(telem_file)
            }
        except Exception as e:
            logger.debug(f"Failed to load cached job {job_id}: {e}")


# Run initial directory scan
scan_existing_jobs()


def get_detector() -> YOLODetector:
    """Lazy singleton for the YOLO detector to conserve memory."""
    global SHARED_DETECTOR
    if SHARED_DETECTOR is None:
        SHARED_DETECTOR = YOLODetector()
    return SHARED_DETECTOR


def run_pipeline_task(
    job_id: str,
    input_path: str,
    output_video_path: str,
    telemetry_json_path: str,
    conf_threshold: float,
    stride: int
):
    """Background worker executing the video perception pipeline."""
    try:
        JOBS[job_id]["status"] = JobStatus.PROCESSING
        JOBS[job_id]["processed_frames"] = 0

        def on_progress(processed: int, total: int, fps: float, latency_ms: float):
            if job_id in JOBS:
                JOBS[job_id]["processed_frames"] = processed
                JOBS[job_id]["total_frames"] = total
                JOBS[job_id]["progress_percent"] = round((processed / max(1, total)) * 100.0, 1)
                JOBS[job_id]["fps"] = round(fps, 1)
                JOBS[job_id]["avg_latency_ms"] = round(latency_ms, 1)

        pipeline = VideoProcessingPipeline(detector=get_detector())
        summary = pipeline.process_video(
            input_video_path=input_path,
            output_video_path=output_video_path,
            telemetry_json_path=telemetry_json_path,
            conf_threshold=conf_threshold,
            stride=stride,
            progress_callback=on_progress
        )

        JOBS[job_id]["status"] = JobStatus.COMPLETED
        JOBS[job_id]["progress_percent"] = 100.0
        JOBS[job_id]["completed_at"] = datetime.now().isoformat()
        JOBS[job_id]["fps"] = summary["avg_fps"]
        JOBS[job_id]["avg_latency_ms"] = summary["avg_latency_ms"]
        JOBS[job_id]["annotated_video_url"] = f"/api/video/jobs/{job_id}/stream"
        JOBS[job_id]["telemetry_url"] = f"/api/video/jobs/{job_id}/telemetry"
        JOBS[job_id]["total_unique_tracks"] = summary["total_unique_tracks"]

    except Exception as e:
        logger.exception(f"Error processing video job {job_id}: {e}")
        if job_id in JOBS:
            JOBS[job_id]["status"] = JobStatus.FAILED
            JOBS[job_id]["error_message"] = str(e)


@router.get("/samples", response_model=List[SampleVideo])
def list_sample_videos():
    """Returns the list of bundled benchmark traffic videos."""
    samples = []
    if settings.SAMPLES_DIR.exists():
        for file in settings.SAMPLES_DIR.glob("*.mp4"):
            size_b = file.stat().st_size
            samples.append(
                SampleVideo(
                    id=file.stem,
                    filename=file.name,
                    filesize_bytes=size_b,
                    filesize_mb=round(size_b / (1024 * 1024), 2)
                )
            )
    return samples


@router.api_route("/samples/{filename}/stream", methods=["GET", "HEAD"])
def stream_sample_video(filename: str):
    """Streams a raw sample video for source preview."""
    target_path = settings.SAMPLES_DIR / filename
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Sample video not found")
    return FileResponse(target_path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    """Uploads a user-provided video (MP4, AVI, MOV)."""
    allowed_exts = {".mp4", ".avi", ".mov", ".mkv"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video format: {ext}. Allowed: {allowed_exts}"
        )

    file_id = f"upload_{uuid.uuid4().hex[:8]}{ext}"
    dest_path = settings.UPLOAD_DIR / file_id

    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {
        "status": "UPLOADED",
        "filename": file_id,
        "original_name": file.filename,
        "size_bytes": dest_path.stat().st_size,
        "download_url": f"/api/video/uploads/{file_id}"
    }


@router.post("/process", response_model=VideoJobInfo)
def start_video_processing(req: ProcessVideoRequest, background_tasks: BackgroundTasks):
    """Triggers asynchronous YOLO detection, ByteTrack tracking, and video annotation."""
    if req.source_type.upper() == "SAMPLE":
        if not req.sample_filename:
            raise HTTPException(status_code=400, detail="sample_filename must be provided for SAMPLE source")
        input_path = settings.SAMPLES_DIR / req.sample_filename
        display_name = req.sample_filename
    else:
        if not req.upload_filename:
            raise HTTPException(status_code=400, detail="upload_filename must be provided for UPLOAD source")
        input_path = settings.UPLOAD_DIR / req.upload_filename
        display_name = req.upload_filename

    if not input_path.exists():
        raise HTTPException(status_code=404, detail=f"Source video file not found: {input_path.name}")

    job_id = uuid.uuid4().hex[:8]
    output_video_path = str(settings.OUTPUT_DIR / f"{job_id}_annotated.mp4")
    telemetry_json_path = str(settings.OUTPUT_DIR / f"{job_id}_telemetry.json")

    job_info = {
        "job_id": job_id,
        "filename": display_name,
        "source_type": req.source_type.upper(),
        "status": JobStatus.PENDING,
        "progress_percent": 0.0,
        "total_frames": 0,
        "processed_frames": 0,
        "fps": 0.0,
        "avg_latency_ms": 0.0,
        "annotated_video_url": None,
        "telemetry_url": None,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "error_message": None,
        "output_video_path": output_video_path,
        "telemetry_json_path": telemetry_json_path
    }
    JOBS[job_id] = job_info

    # Enqueue background task
    background_tasks.add_task(
        run_pipeline_task,
        job_id=job_id,
        input_path=str(input_path),
        output_video_path=output_video_path,
        telemetry_json_path=telemetry_json_path,
        conf_threshold=req.conf_threshold or settings.DEFAULT_CONF_THRESHOLD,
        stride=req.stride or 1
    )

    return VideoJobInfo(**job_info)


@router.get("/jobs", response_model=List[VideoJobInfo])
def list_jobs():
    """Lists all video processing tasks and their current statuses."""
    scan_existing_jobs()
    return [VideoJobInfo(**info) for info in JOBS.values()]


@router.get("/jobs/{job_id}", response_model=VideoJobInfo)
def get_job_status(job_id: str):
    """Queries real-time status and progress metrics of a processing task."""
    scan_existing_jobs()
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return VideoJobInfo(**JOBS[job_id])


@router.api_route("/jobs/{job_id}/stream", methods=["GET", "HEAD"])
def stream_annotated_video(job_id: str):
    """Streams the annotated MP4 video with perception HUD and tracking trails."""
    scan_existing_jobs()
    if job_id not in JOBS:
        target_video = settings.OUTPUT_DIR / f"{job_id}_annotated.mp4"
        if target_video.exists():
            return FileResponse(
                str(target_video),
                media_type="video/mp4",
                headers={"Accept-Ranges": "bytes"}
            )
        raise HTTPException(status_code=404, detail="Job not found")

    output_path = JOBS[job_id].get("output_video_path")
    if not output_path or not os.path.exists(output_path):
        raise HTTPException(status_code=404, detail="Annotated video file is not ready or missing")

    return FileResponse(
        output_path,
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes"}
    )


@router.get("/jobs/{job_id}/download")
def download_annotated_video(job_id: str):
    """Downloads the annotated video file."""
    scan_existing_jobs()
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    output_path = JOBS[job_id].get("output_video_path")
    if not output_path or not os.path.exists(output_path):
        raise HTTPException(status_code=404, detail="Annotated video file is not ready or missing")
    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"doorsafe_annotated_{job_id}.mp4"
    )


@router.get("/jobs/{job_id}/telemetry")
def get_job_telemetry(job_id: str):
    """Returns the full structured frame-by-frame telemetry JSON payload."""
    scan_existing_jobs()
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    telemetry_path = JOBS[job_id].get("telemetry_json_path")
    if not telemetry_path or not os.path.exists(telemetry_path):
        raise HTTPException(status_code=404, detail="Telemetry JSON is not ready or missing")
    with open(telemetry_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return JSONResponse(content=data)
