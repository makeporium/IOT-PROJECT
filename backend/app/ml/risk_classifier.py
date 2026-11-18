"""Phase 7: reproducible synthetic experiments and advisory risk scoring."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, precision_score, recall_score
from sklearn.model_selection import train_test_split

from app.config import BACKEND_DIR


FEATURES = (
    "distance_m", "speed_mps", "lateral_offset_m", "ttc_s",
    "radar_distance_m", "radar_speed_mps", "measurement_uncertainty_m",
)


class AdvisoryRiskClassifier:
    """Random Forest that estimates risk; it never commands the door actuator."""

    def __init__(self) -> None:
        self.model: RandomForestClassifier | None = None
        self.metrics: Dict[str, Any] = {}
        self.dataset_path = BACKEND_DIR / "data" / "phase7_synthetic_experiments.csv"

    def train(self, experiment_count: int = 300, seed: int = 42) -> Dict[str, Any]:
        rng = np.random.default_rng(seed)
        distance = rng.uniform(0.5, 45.0, experiment_count)
        speed = rng.uniform(0.0, 15.0, experiment_count)
        lateral = rng.uniform(0.1, 5.0, experiment_count)
        ttc = np.where(speed > 0.25, distance / speed, 99.0)
        uncertainty = rng.uniform(0.03, 0.70, experiment_count)
        radar_distance = np.maximum(0.1, distance + rng.normal(0, 0.15, experiment_count))
        radar_speed = np.maximum(0.0, speed + rng.normal(0, 0.20, experiment_count))

        # Synthetic ground-truth label derived from controlled geometry. This
        # is intentionally separate from the live deterministic door authority.
        corridor = lateral <= 1.45
        dangerous = corridor & ((ttc <= 3.0) | (distance <= 2.0))
        X = np.column_stack((distance, speed, lateral, ttc, radar_distance, radar_speed, uncertainty))
        y = dangerous.astype(int)

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=seed, stratify=y
        )
        self.model = RandomForestClassifier(
            n_estimators=160, max_depth=8, min_samples_leaf=2,
            random_state=seed, class_weight="balanced",
        )
        self.model.fit(X_train, y_train)
        predictions = self.model.predict(X_test)
        cm = confusion_matrix(y_test, predictions, labels=[0, 1]).tolist()
        self.metrics = {
            "experiment_count": experiment_count,
            "test_count": len(y_test),
            "accuracy": round(float(accuracy_score(y_test, predictions)), 3),
            "precision": round(float(precision_score(y_test, predictions, zero_division=0)), 3),
            "recall": round(float(recall_score(y_test, predictions, zero_division=0)), 3),
            "confusion_matrix": {"labels": ["CLEAR", "RISK"], "values": cm},
            "feature_importance": {
                feature: round(float(value), 3)
                for feature, value in zip(FEATURES, self.model.feature_importances_)
            },
            "advisory_only": True,
        }
        self._write_dataset(X, y)
        return self.metrics

    def predict(self, features: Dict[str, float]) -> Dict[str, Any]:
        if self.model is None:
            self.train()
        row = np.array([[float(features[name]) for name in FEATURES]])
        risk_probability = float(self.model.predict_proba(row)[0][1])
        return {
            "risk_probability": round(risk_probability, 3),
            "advisory_label": "HIGH_RISK" if risk_probability >= 0.5 else "LOW_RISK",
            "advisory_only": True,
            "safety_authority": "Deterministic door-zone state machine",
        }

    def _write_dataset(self, X: np.ndarray, y: np.ndarray) -> None:
        self.dataset_path.parent.mkdir(parents=True, exist_ok=True)
        with self.dataset_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([*FEATURES, "ground_truth_risk"])
            writer.writerows([*row, int(label)] for row, label in zip(X, y))


_classifier = AdvisoryRiskClassifier()


def get_risk_classifier() -> AdvisoryRiskClassifier:
    return _classifier
