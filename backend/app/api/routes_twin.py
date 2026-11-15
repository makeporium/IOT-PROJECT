"""Phase 6 ground-truth digital-twin API."""

from fastapi import APIRouter, HTTPException, Query
from app.simulation.digital_twin import get_digital_twin

router = APIRouter(prefix="/twin", tags=["Phase 6: 3D Digital Twin"])


@router.get("/scenarios")
def scenarios():
    twin = get_digital_twin()
    return {"scenarios": [{"id": key, "name": value[0]} for key, value in twin.SCENARIOS.items()]}


@router.post("/reset")
def reset(scenario_id: str = "bicycle_overtake"):
    try:
        return get_digital_twin().reset(scenario_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/step")
def step(dt_s: float = Query(0.1, ge=0.02, le=1.0)):
    return get_digital_twin().step(dt_s)


@router.post("/door-request")
def door_request(open: bool = True):
    twin = get_digital_twin()
    return twin.request_door_open() if open else twin.close_door()


@router.get("/state")
def state():
    return get_digital_twin().snapshot()
