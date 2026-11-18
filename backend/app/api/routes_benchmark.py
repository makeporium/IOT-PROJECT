"""Phase 9 validation report API."""

from fastapi import APIRouter
from app.benchmarking.suite import run_benchmark

router = APIRouter(prefix="/benchmark", tags=["Phase 9: Validation"])


@router.post("/run")
def run(iterations: int = 100):
    return run_benchmark(iterations)
