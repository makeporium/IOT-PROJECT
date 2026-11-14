"""Distance estimation error metrics comparing estimates against ground truth."""

from typing import List, Dict, Any
import numpy as np


def calculate_distance_errors(
    ground_truth_distances: List[float],
    estimated_distances: List[float]
) -> Dict[str, float]:
    """
    Calculates Mean Absolute Error (MAE), Root Mean Squared Error (RMSE),
    and Mean Absolute Percentage Error (MAPE).
    """
    if not ground_truth_distances or len(ground_truth_distances) != len(estimated_distances):
        return {"mae_m": 0.0, "rmse_m": 0.0, "mape_percent": 0.0, "max_error_m": 0.0}

    y_true = np.array(ground_truth_distances, dtype=float)
    y_pred = np.array(estimated_distances, dtype=float)

    abs_errors = np.abs(y_true - y_pred)
    mae = float(np.mean(abs_errors))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    mape = float(np.mean(abs_errors / np.clip(y_true, 0.1, None)) * 100.0)
    max_err = float(np.max(abs_errors))

    return {
        "mae_m": round(mae, 3),
        "rmse_m": round(rmse, 3),
        "mape_percent": round(mape, 2),
        "max_error_m": round(max_err, 3)
    }
