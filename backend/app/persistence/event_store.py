"""SQLite flight-recorder store for DOORSAFE Phase 5."""

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from queue import Queue
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings


class SupabaseEventSync:
    """Non-blocking PostgREST mirror for the local flight recorder."""

    def __init__(self, url: str, publishable_key: str, table: str):
        self.endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
        self.publishable_key = publishable_key
        self._queue: Queue[Dict[str, Any]] = Queue(maxsize=2000)
        self.last_sync_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.synced_events = 0
        self._worker = threading.Thread(target=self._run, daemon=True, name="doorsafe-supabase-sync")
        self._worker.start()

    def enqueue(self, event: Dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(event)
        except Exception:
            self.last_error = "Supabase event queue is full; local SQLite copy retained."

    def _run(self) -> None:
        # Keep requests out of the real-time ESP32 safety/MQTT callback path.
        import requests

        while True:
            event = self._queue.get()
            try:
                response = requests.post(
                    self.endpoint,
                    headers={
                        "apikey": self.publishable_key,
                        "Authorization": f"Bearer {self.publishable_key}",
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal",
                    },
                    json=event,
                    timeout=4,
                )
                response.raise_for_status()
                self.synced_events += 1
                self.last_sync_at = time.time()
                self.last_error = None
            except Exception as exc:
                # Local SQLite is authoritative; a later sync worker/retry can
                # be added without risking an actuator or sensor callback now.
                self.last_error = str(exc)
            finally:
                self._queue.task_done()

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "pending_events": self._queue.qsize(),
            "synced_events": self.synced_events,
            "last_sync_at": self.last_sync_at,
            "last_error": self.last_error,
        }


class EventStore:
    """Thread-safe append-only event store, designed for later cloud sync."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(self.database_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS vehicle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at REAL NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                safety_state TEXT,
                door_locked INTEGER,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_recorded_at ON vehicle_events(recorded_at DESC)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type ON vehicle_events(event_type)"
        )
        self._connection.commit()
        self._cloud_sync = (
            SupabaseEventSync(
                settings.SUPABASE_URL,
                settings.SUPABASE_PUBLISHABLE_KEY,
                settings.SUPABASE_EVENTS_TABLE,
            )
            if settings.SUPABASE_URL and settings.SUPABASE_PUBLISHABLE_KEY
            else None
        )

    def record(
        self,
        event_type: str,
        source: str,
        payload: Dict[str, Any],
        safety_state: Optional[str] = None,
        door_locked: Optional[bool] = None,
    ) -> int:
        """Append a time-stamped immutable vehicle event."""
        recorded_at = time.time()
        with self._lock:
            cur = self._connection.execute(
                """
                INSERT INTO vehicle_events
                    (recorded_at, event_type, source, safety_state, door_locked, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    recorded_at,
                    event_type,
                    source,
                    safety_state,
                    None if door_locked is None else int(door_locked),
                    json.dumps(payload, default=str, separators=(",", ":")),
                ),
            )
            self._connection.commit()
            event_id = int(cur.lastrowid)

        if self._cloud_sync:
            self._cloud_sync.enqueue({
                "local_event_id": event_id,
                "recorded_at": datetime.fromtimestamp(recorded_at, timezone.utc).isoformat(),
                "event_type": event_type,
                "source": source,
                "safety_state": safety_state,
                "door_locked": door_locked,
                "payload": payload,
            })
        return event_id

    def recent(self, limit: int = 50, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 500))
        query = "SELECT * FROM vehicle_events"
        args: list[Any] = []
        if event_type:
            query += " WHERE event_type = ?"
            args.append(event_type)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._connection.execute(query, args).fetchall()
        return [
            {
                "id": row["id"],
                "recorded_at": row["recorded_at"],
                "event_type": row["event_type"],
                "source": row["source"],
                "safety_state": row["safety_state"],
                "door_locked": None if row["door_locked"] is None else bool(row["door_locked"]),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            total = self._connection.execute("SELECT COUNT(*) FROM vehicle_events").fetchone()[0]
            rows = self._connection.execute(
                "SELECT event_type, COUNT(*) AS count FROM vehicle_events GROUP BY event_type ORDER BY count DESC"
            ).fetchall()
            latest = self._connection.execute("SELECT MAX(recorded_at) FROM vehicle_events").fetchone()[0]
        summary = {
            "storage_mode": "LOCAL_SQLITE",
            "database_path": str(self.database_path),
            "total_events": total,
            "latest_event_at": latest,
            "by_type": {row["event_type"]: row["count"] for row in rows},
        }
        summary["supabase"] = self._cloud_sync.status() if self._cloud_sync else {"enabled": False}
        return summary


_event_store: Optional[EventStore] = None


def get_event_store() -> EventStore:
    global _event_store
    if _event_store is None:
        _event_store = EventStore(settings.EVENT_DB_PATH)
    return _event_store
