"""Unit tests for YOLO detection and class filtering."""

import os
import pytest
import numpy as np
from app.config import settings
from app.computer_vision.detector import YOLODetector


def test_detector_initialization():
    """Verify that YOLO detector loads the local model and verifies classes."""
    detector = YOLODetector()
    assert detector.model is not None
    assert len(detector.target_class_ids) > 0

    # Ensure required automotive classes are present
    target_names = [detector.model_names[cid] for cid in detector.target_class_ids]
    for required in ["person", "bicycle", "motorcycle", "car", "bus", "truck"]:
        assert required in target_names, f"Missing required class: {required}"


def test_detector_single_frame_inference():
    """Verify that track_frame executes inference without error on a synthetic frame."""
    detector = YOLODetector()
    # Create synthetic 720p color image
    dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    # Add a mock object (e.g. gray rectangle)
    dummy_frame[200:450, 400:600] = 180

    detections, latency_ms = detector.track_frame(dummy_frame, conf_threshold=0.25)
    assert isinstance(detections, list)
    assert latency_ms > 0.0
    print(f"Single frame inference latency: {latency_ms:.2f} ms")


if __name__ == "__main__":
    test_detector_initialization()
    test_detector_single_frame_inference()
    print("All detection tests passed!")
