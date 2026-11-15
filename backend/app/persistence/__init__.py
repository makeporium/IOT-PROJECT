"""Phase 5 durable vehicle-event persistence."""

from .event_store import EventStore, get_event_store

__all__ = ["EventStore", "get_event_store"]
