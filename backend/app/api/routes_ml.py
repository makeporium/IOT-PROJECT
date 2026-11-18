"""Phase 7 advisory risk-classifier API."""

from typing import Dict
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.ml.risk_classifier import FEATURES, get_risk_classifier

router = APIRouter(prefix="/ml", tags=["Phase 7: Advisory Risk Classifier"])


class RiskFeatures(BaseModel):
    distance_m: float = Field(..., ge=0.1)
    speed_mps: float = Field(..., ge=0)
    lateral_offset_m: float = Field(..., ge=0)
    ttc_s: float = Field(..., ge=0)
    radar_distance_m: float = Field(..., ge=0.1)
    radar_speed_mps: float = Field(..., ge=0)
    measurement_uncertainty_m: float = Field(..., ge=0)


@router.post("/train")
def train_classifier(experiment_count: int = 300) -> Dict:
    return get_risk_classifier().train(experiment_count=experiment_count)


@router.get("/status")
def classifier_status() -> Dict:
    classifier = get_risk_classifier()
    if not classifier.metrics:
        classifier.train()
    return {**classifier.metrics, "features": FEATURES, "dataset_path": str(classifier.dataset_path)}


@router.post("/predict")
def predict_risk(features: RiskFeatures) -> Dict:
    return get_risk_classifier().predict(features.model_dump())
