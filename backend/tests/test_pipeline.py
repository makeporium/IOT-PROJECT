"""End-to-end integration test for the perception and tracking pipeline."""

import os
import json
import pytest
from app.config import settings
from app.computer_vision.detector import YOLODetector
from app.computer_vision.pipeline import VideoProcessingPipeline


def test_video_pipeline_end_to_end(tmp_path):
    """Processes a test video clip and verifies annotated MP4 and telemetry JSON generation."""
    sample_video = settings.SAMPLES_DIR / "01_034-3.mp4"
    if not sample_video.exists():
        pytest.skip(f"Sample video not found at {sample_video}")

    output_mp4 = str(tmp_path / "test_annotated.mp4")
    telemetry_json = str(tmp_path / "test_telemetry.json")

    detector = YOLODetector()
    pipeline = VideoProcessingPipeline(detector=detector)

    # Process first 20 frames for fast test verification
    summary = pipeline.process_video(
        input_video_path=str(sample_video),
        output_video_path=output_mp4,
        telemetry_json_path=telemetry_json,
        conf_threshold=0.30,
        max_frames=20
    )

    # Assertions
    assert summary["total_frames_processed"] == 20
    assert summary["avg_fps"] > 0
    assert os.path.exists(output_mp4)
    assert os.path.getsize(output_mp4) > 1000  # valid MP4 file
    assert os.path.exists(telemetry_json)

    # Check JSON structure
    with open(telemetry_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "meta" in data
    assert "frames" in data
    assert len(data["frames"]) == 20
    assert data["meta"]["total_frames_processed"] == 20
    assert "camera_calibration" in data["meta"]

    # Verify Phase 2 metric and kinematic telemetry fields
    frame0 = data["frames"][0]
    if frame0.get("objects"):
        first_obj = frame0["objects"][0]
        assert "distance_m" in first_obj
        assert "metric_x" in first_obj
        assert "metric_y" in first_obj
        assert "hazard_urgency" in first_obj

    print(f"\nPipeline integration test passed!")
    print(f"Processed 20 frames at {summary['avg_fps']:.1f} FPS, avg latency: {summary['avg_latency_ms']:.1f} ms.")
    print(f"Annotated video size: {os.path.getsize(output_mp4) / 1024:.1f} KB")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        from pathlib import Path
        test_video_pipeline_end_to_end(Path(temp_dir))
