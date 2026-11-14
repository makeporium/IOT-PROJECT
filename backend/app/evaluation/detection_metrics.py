"""Computer vision detection evaluation and statistical summary."""

from typing import List, Dict, Any
import numpy as np


def calculate_detection_stats(frames_telemetry: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes summary statistics across processed video frames."""
    if not frames_telemetry:
        return {"total_frames": 0, "total_detections": 0, "class_counts": {}}

    class_counts: Dict[str, int] = {}
    confidences: List[float] = []
    unique_tracks = set()

    for frame in frames_telemetry:
        objects = frame.get("objects", [])
        for obj in objects:
            cname = obj.get("class_name", "unknown")
            class_counts[cname] = class_counts.get(cname, 0) + 1
            conf = obj.get("confidence")
            if conf is not None:
                confidences.append(conf)
            tid = obj.get("track_id")
            if tid is not None:
                unique_tracks.add(tid)

    return {
        "total_frames": len(frames_telemetry),
        "total_detections": len(confidences),
        "unique_track_count": len(unique_tracks),
        "mean_confidence": float(np.mean(confidences)) if confidences else 0.0,
        "median_confidence": float(np.median(confidences)) if confidences else 0.0,
        "class_breakdown": class_counts
    }
