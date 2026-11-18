"""Small, reproducible Phase 9 benchmark suite."""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone

from app.config import BACKEND_DIR
from app.ml.risk_classifier import get_risk_classifier
from app.simulation.digital_twin import DigitalTwinSession


def run_benchmark(iterations: int = 100) -> dict:
    iterations = max(10, min(int(iterations), 500))
    twin = DigitalTwinSession()
    latencies = []
    errors = []
    false_safe = 0
    checked_danger = 0
    scenario_ids = list(twin.SCENARIOS)

    for index in range(iterations):
        scenario_id = scenario_ids[index % len(scenario_ids)]
        twin.reset(scenario_id)
        started = time.perf_counter()
        frame = twin.step(0.12)
        latencies.append((time.perf_counter() - started) * 1000)
        participant = frame["participants"][0]
        radar = frame["virtual_radar"][0]
        if radar.get("target_detected"):
            errors.append(abs(radar["measured_distance_m"] - participant["actual_distance_m"]))
        if participant["intersects_door_zone"]:
            checked_danger += 1
            if frame["safety"]["state"] == "SAFE":
                false_safe += 1

    classifier = get_risk_classifier()
    ml_metrics = classifier.train(300)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "iterations": iterations,
        "simulation_step_latency_ms": {
            "mean": round(statistics.mean(latencies), 3),
            "p95": round(sorted(latencies)[int(len(latencies) * .95) - 1], 3),
        },
        "virtual_radar_distance_mae_m": round(statistics.mean(errors), 3) if errors else None,
        "deterministic_false_safe_rate": round(false_safe / checked_danger, 4) if checked_danger else 0.0,
        "danger_frames_checked": checked_danger,
        "ml": ml_metrics,
        "limitations": [
            "Radar error is virtual sensor error, not a real HC-SR04 laboratory measurement.",
            "IoT round-trip latency requires a live hardware session and is not inferred here.",
        ],
    }
    output = BACKEND_DIR / "data" / "phase9_benchmark_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["report_path"] = str(output)
    return result
