"""Safety decision metrics: False Safe Rate, False Warning Rate, and Lock Accuracy."""

from typing import List, Dict, Any


def calculate_safety_matrix(
    ground_truth_hazards: List[bool],
    system_lock_decisions: List[bool]
) -> Dict[str, Any]:
    """
    Evaluates fail-safe performance:
    - True Positive: Danger was present and door was LOCKED.
    - False Negative (Critical safety hazard): Danger was present but door was UNLOCKED (False Safe).
    - False Positive (Nuisance lockout): Safe condition but door was LOCKED (False Warning).
    - True Negative: Safe condition and door was UNLOCKED.
    """
    if not ground_truth_hazards or len(ground_truth_hazards) != len(system_lock_decisions):
        return {"total_events": 0, "false_safe_rate": 0.0, "false_warning_rate": 0.0}

    tp, fp, fn, tn = 0, 0, 0, 0
    for gt, dec in zip(ground_truth_hazards, system_lock_decisions):
        if gt and dec:
            tp += 1
        elif not gt and dec:
            fp += 1
        elif gt and not dec:
            fn += 1
        else:
            tn += 1

    total = len(ground_truth_hazards)
    false_safe_rate = (fn / (tp + fn)) if (tp + fn) > 0 else 0.0
    false_warning_rate = (fp / (fp + tn)) if (fp + tn) > 0 else 0.0
    accuracy = ((tp + tn) / total) if total > 0 else 0.0

    return {
        "total_evaluations": total,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "critical_false_safe_rate": round(false_safe_rate, 4),
        "false_warning_rate": round(false_warning_rate, 4),
        "door_decision_accuracy": round(accuracy, 4)
    }
