"""Unit tests for multi-object tracking and trajectory history."""

import pytest
from app.tracking.trajectory_tracker import TrajectoryTracker
from app.models.schemas import BoundingBox


def test_trajectory_tracker_lifecycle():
    """Verify that TrajectoryTracker maintains persistent track IDs and histories."""
    tracker = TrajectoryTracker(max_history=10, inactive_threshold=5)

    # Frame 1: Object 10 appears
    bbox1 = BoundingBox(
        x1=100, y1=200, x2=160, y2=320,
        width=60, height=120, center_x=130, center_y=260,
        foot_x=130, foot_y=320
    )
    dets_frame1 = [{
        "track_id": 10,
        "class_id": 1,
        "class_name": "bicycle",
        "confidence": 0.92,
        "bbox": bbox1
    }]
    active_f1 = tracker.update(dets_frame1, frame_idx=1, timestamp_sec=0.033)
    assert len(active_f1) == 1
    assert active_f1[0].track_id == 10
    assert active_f1[0].footpoint == (130, 320)
    assert len(active_f1[0].history) == 1

    # Frame 2: Object 10 moves
    bbox2 = BoundingBox(
        x1=105, y1=205, x2=165, y2=325,
        width=60, height=120, center_x=135, center_y=265,
        foot_x=135, foot_y=325
    )
    dets_frame2 = [{
        "track_id": 10,
        "class_id": 1,
        "class_name": "bicycle",
        "confidence": 0.94,
        "bbox": bbox2
    }]
    active_f2 = tracker.update(dets_frame2, frame_idx=2, timestamp_sec=0.066)
    assert len(active_f2) == 1
    assert active_f2[0].track_id == 10
    assert len(active_f2[0].history) == 2
    assert active_f2[0].track_age_frames == 2

    # Displacement calculation
    dx, dy = tracker.tracks[10].get_recent_displacement()
    assert dx == 5.0
    assert dy == 5.0
    print("Tracking lifecycle test passed successfully!")


if __name__ == "__main__":
    test_trajectory_tracker_lifecycle()
