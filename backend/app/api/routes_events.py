"""Phase 5 vehicle flight-recorder REST endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.persistence.event_store import EventStore, get_event_store

router = APIRouter(prefix="/events", tags=["Phase 5: Vehicle Flight Recorder"])


@router.get("/summary")
def event_summary(store: EventStore = Depends(get_event_store)):
    return store.summary()


@router.get("")
def recent_events(
    limit: int = Query(50, ge=1, le=500),
    event_type: Optional[str] = Query(None),
    store: EventStore = Depends(get_event_store),
):
    return {"events": store.recent(limit=limit, event_type=event_type)}
