"""Latency and real-time processing throughput metrics."""

from typing import List, Dict, Any
import numpy as np


def calculate_latency_profile(frames_telemetry: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculates inference latency percentiles and FPS distributions."""
    if not frames_telemetry:
        return {"avg_fps": 0.0, "p50_latency_ms": 0.0, "p95_latency_ms": 0.0}

    inf_latencies = [f["inference_time_ms"] for f in frames_telemetry if "inference_time_ms" in f]
    fps_values = [f["fps"] for f in frames_telemetry if "fps" in f and f["fps"] > 0]

    return {
        "mean_latency_ms": float(np.mean(inf_latencies)) if inf_latencies else 0.0,
        "p50_latency_ms": float(np.percentile(inf_latencies, 50)) if inf_latencies else 0.0,
        "p90_latency_ms": float(np.percentile(inf_latencies, 90)) if inf_latencies else 0.0,
        "p95_latency_ms": float(np.percentile(inf_latencies, 95)) if inf_latencies else 0.0,
        "min_latency_ms": float(np.min(inf_latencies)) if inf_latencies else 0.0,
        "max_latency_ms": float(np.max(inf_latencies)) if inf_latencies else 0.0,
        "mean_fps": float(np.mean(fps_values)) if fps_values else 0.0
    }
