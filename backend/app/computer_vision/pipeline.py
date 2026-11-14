"""Complete video processing pipeline with detection, tracking, metric distance, kinematics, TTC, and telemetry."""

import os
import json
import time
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
import cv2
import numpy as np

from app.config import settings
from app.computer_vision.detector import YOLODetector
from app.computer_vision.annotator import FrameAnnotator
from app.tracking.trajectory_tracker import TrajectoryTracker
from app.distance_estimation.camera_geometry import PinholeGroundProjector, CameraParameters
from app.trajectory.kinematics import KinematicEstimator
from app.trajectory.ttc_calculator import TTCCalculator
from app.trajectory.predictor import TrajectoryPredictor
from app.models.schemas import FrameTelemetry, TrackedObject

logger = logging.getLogger("doorsafe.pipeline")


def convert_to_web_h264(raw_mp4_path: str, final_mp4_path: str):
    """
    Transcodes an OpenCV-generated MP4 video into standard web-compatible H.264 (AVC)
    with YUV420p pixel format and the '+faststart' moov atom placement.
    This is required for native HTML5 <video> playback in Chrome, Edge, Safari, and Firefox.
    """
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe, "-y",
            "-i", raw_mp4_path,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            final_mp4_path
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        if os.path.exists(raw_mp4_path) and os.path.abspath(raw_mp4_path) != os.path.abspath(final_mp4_path):
            try:
                os.remove(raw_mp4_path)
            except OSError:
                pass
        logger.info(f"Successfully converted {final_mp4_path} to web-compatible H.264 (+faststart).")
    except Exception as e:
        logger.warning(f"Web H.264 transcoding fallback: {e}")
        if os.path.exists(raw_mp4_path) and raw_mp4_path != final_mp4_path:
            shutil.move(raw_mp4_path, final_mp4_path)


class VideoProcessingPipeline:
    """Orchestrates video decoding, YOLO inference, ByteTrack tracking, metric kinematics, TTC, and annotation."""

    def __init__(
        self,
        detector: Optional[YOLODetector] = None,
        camera_params: Optional[CameraParameters] = None
    ):
        self.detector = detector or YOLODetector()
        self.annotator = FrameAnnotator()
        self.tracker = TrajectoryTracker()
        self.projector = PinholeGroundProjector(params=camera_params)
        self.kinematics = KinematicEstimator()
        self.ttc_calculator = TTCCalculator()
        self.predictor = TrajectoryPredictor()

    def process_video(
        self,
        input_video_path: str,
        output_video_path: str,
        telemetry_json_path: str,
        conf_threshold: Optional[float] = None,
        stride: int = 1,
        max_frames: Optional[int] = None,
        camera_height_m: Optional[float] = None,
        pitch_deg: Optional[float] = None,
        progress_callback: Optional[Callable[[int, int, float, float], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes end-to-end video processing with Phase 2 physical distance, kinematics, and TTC.
        """
        if not os.path.exists(input_video_path):
            raise FileNotFoundError(f"Input video not found: {input_video_path}")

        cap = cv2.VideoCapture(input_video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open video file: {input_video_path}")

        total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        target_frames = total_video_frames
        if max_frames and max_frames < target_frames:
            target_frames = max_frames

        # Update projector resolution & optional camera parameter overrides
        self.projector.set_resolution(width, height)
        if camera_height_m is not None:
            self.projector.update_parameters(camera_height_m=camera_height_m)
        if pitch_deg is not None:
            self.projector.update_parameters(pitch_deg=pitch_deg)

        logger.info(f"Processing video: {input_video_path} ({width}x{height} @ {video_fps:.1f}fps, {target_frames} frames)")
        logger.info(f"Camera Calibration: height={self.projector.params.camera_height_m}m, pitch={self.projector.params.pitch_deg}deg, HFOV={self.projector.params.hfov_deg}deg")

        # Prepare intermediate raw video file for OpenCV
        raw_temp_output = output_video_path + ".raw.mp4"
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out_writer = cv2.VideoWriter(raw_temp_output, fourcc, video_fps, (width, height))
        if not out_writer.isOpened():
            cap.release()
            raise RuntimeError(f"Could not open VideoWriter for {raw_temp_output}")

        # Reset states for clean video run
        self.tracker.reset()
        self.detector.reset_tracker()
        self.kinematics.reset()

        telemetry_records: List[Dict[str, Any]] = []
        frame_idx = 0
        processed_count = 0
        total_inference_time = 0.0
        total_pipeline_time = 0.0
        t_pipeline_start = time.perf_counter()

        try:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                if max_frames and frame_idx > max_frames:
                    break

                if (frame_idx - 1) % stride != 0:
                    continue

                t_frame_start = time.perf_counter()
                timestamp_sec = frame_idx / video_fps

                # 1. Detection & ByteTrack tracking
                detections, inf_ms = self.detector.track_frame(
                    frame,
                    conf_threshold=conf_threshold,
                    persist=True
                )
                total_inference_time += inf_ms

                # 2. Update trajectory history
                t_track_start = time.perf_counter()
                tracked_objects = self.tracker.update(
                    detections=detections,
                    frame_idx=frame_idx,
                    timestamp_sec=timestamp_sec
                )

                # 3. Phase 2 Metric Geometry, Kinematics & TTC Estimation
                active_ids = []
                critical_conflicts = 0
                warning_conflicts = 0

                for obj in tracked_objects:
                    active_ids.append(obj.track_id)
                    foot_x, foot_y = obj.bbox.foot_x, obj.bbox.foot_y

                    # 3.1 Pinhole ground-plane projection
                    ground_coord = self.projector.pixel_to_ground(foot_x, foot_y)
                    obj.metric_x = ground_coord.x_m
                    obj.metric_y = ground_coord.y_m
                    obj.distance_m = ground_coord.distance_m
                    obj.distance_confidence = ground_coord.confidence
                    obj.uncertainty_m = ground_coord.uncertainty_m

                    # 3.2 Kinematic Alpha-Beta state filtering
                    kin = self.kinematics.update(
                        track_id=obj.track_id,
                        ground_x=ground_coord.x_m,
                        ground_y=ground_coord.y_m,
                        timestamp=timestamp_sec
                    )
                    obj.velocity_x_mps = kin.vx_mps
                    obj.velocity_y_mps = kin.vy_mps
                    obj.speed_mps = kin.speed_mps
                    obj.speed_kmh = kin.speed_kmh
                    obj.acceleration_mps2 = kin.acceleration_mps2
                    obj.heading_deg = kin.heading_deg
                    obj.is_approaching = kin.is_approaching

                    # 3.3 Dynamic Time-To-Collision (TTC) & Door-Zone Collision Horizon
                    ttc_res = self.ttc_calculator.calculate(
                        x_m=kin.x_m,
                        y_m=kin.y_m,
                        vx_mps=kin.vx_mps,
                        vy_mps=kin.vy_mps
                    )
                    obj.ttc_seconds = ttc_res.ttc_seconds
                    obj.time_to_exit_seconds = ttc_res.time_to_exit_seconds
                    obj.is_conflict_predicted = ttc_res.is_conflict_predicted
                    obj.hazard_urgency = ttc_res.hazard_urgency

                    if ttc_res.hazard_urgency == "CRITICAL":
                        critical_conflicts += 1
                    elif ttc_res.hazard_urgency == "WARNING":
                        warning_conflicts += 1

                    # 3.4 Forward Trajectory Prediction
                    waypoints = self.predictor.predict(
                        x0=kin.x_m,
                        y0=kin.y_m,
                        vx=kin.vx_mps,
                        vy=kin.vy_mps,
                        ground_to_pixel_fn=self.projector.ground_to_pixel
                    )
                    obj.predicted_path = [wp.model_dump() for wp in waypoints]

                # Prune inactive kinematic trackers
                self.kinematics.prune(active_ids)

                track_ms = (time.perf_counter() - t_track_start) * 1000.0

                frame_total_ms = (time.perf_counter() - t_frame_start) * 1000.0
                total_pipeline_time += frame_total_ms
                processed_count += 1
                current_fps = 1000.0 / frame_total_ms if frame_total_ms > 0 else 0.0

                # 4. Annotate frame with Metric Overlays & Warnings
                annotated_frame = self.annotator.annotate(
                    frame=frame,
                    tracked_objects=tracked_objects,
                    frame_idx=frame_idx,
                    total_frames=target_frames,
                    fps=current_fps,
                    latency_ms=inf_ms
                )

                # 5. Write to output video
                out_writer.write(annotated_frame)

                # 6. Record frame telemetry
                frame_telemetry = FrameTelemetry(
                    frame_index=frame_idx,
                    timestamp_sec=round(timestamp_sec, 3),
                    inference_time_ms=round(inf_ms, 2),
                    tracking_time_ms=round(track_ms, 2),
                    total_time_ms=round(frame_total_ms, 2),
                    fps=round(current_fps, 1),
                    active_tracks_count=len(tracked_objects),
                    critical_conflicts_count=critical_conflicts,
                    warning_conflicts_count=warning_conflicts,
                    objects=tracked_objects
                )
                telemetry_records.append(frame_telemetry.model_dump())

                # Progress callback
                if progress_callback and (processed_count % 5 == 0 or frame_idx == target_frames):
                    progress_callback(processed_count, target_frames, current_fps, inf_ms)

        finally:
            cap.release()
            out_writer.release()

        # Convert raw OpenCV video to browser-native H.264 (+faststart)
        convert_to_web_h264(raw_temp_output, output_video_path)

        total_wall_time = time.perf_counter() - t_pipeline_start
        avg_fps = processed_count / total_wall_time if total_wall_time > 0 else 0.0
        avg_latency_ms = (total_inference_time / processed_count) if processed_count > 0 else 0.0

        # Save structured telemetry JSON
        telemetry_payload = {
            "meta": {
                "input_video": os.path.basename(input_video_path),
                "resolution": f"{width}x{height}",
                "original_fps": round(video_fps, 2),
                "total_frames_processed": processed_count,
                "total_unique_tracks": self.tracker.total_unique_tracks,
                "avg_fps": round(avg_fps, 2),
                "avg_inference_latency_ms": round(avg_latency_ms, 2),
                "total_wall_time_sec": round(total_wall_time, 2),
                "camera_calibration": self.projector.params.model_dump(),
                "target_classes": settings.TARGET_CLASSES,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            },
            "frames": telemetry_records
        }

        with open(telemetry_json_path, "w", encoding="utf-8") as f:
            json.dump(telemetry_payload, f, indent=2)

        logger.info(f"Phase 2 Pipeline finished for {input_video_path}: "
                    f"{processed_count} frames, {self.tracker.total_unique_tracks} unique tracks, "
                    f"avg FPS: {avg_fps:.1f}, avg latency: {avg_latency_ms:.1f}ms")

        return {
            "total_frames_processed": processed_count,
            "total_unique_tracks": self.tracker.total_unique_tracks,
            "avg_fps": round(avg_fps, 2),
            "avg_latency_ms": round(avg_latency_ms, 2),
            "output_video_path": output_video_path,
            "telemetry_json_path": telemetry_json_path
        }
