"""Trajectory tracking, track lifecycle, and motion history manager."""

import time
import logging
from typing import Dict, List, Optional, Tuple, Any
from app.config import settings
from app.models.schemas import BoundingBox, TrackPoint, TrackedObject

logger = logging.getLogger("doorsafe.tracking")


class TrackRecord:
    """Internal state representation for an individual tracked object."""

    def __init__(
        self,
        track_id: int,
        class_id: int,
        class_name: str,
        confidence: float,
        bbox: BoundingBox,
        frame_idx: int,
        timestamp_sec: float,
        max_history: int = 50
    ):
        self.track_id = track_id
        self.class_id = class_id
        self.class_name = class_name
        self.confidence = confidence
        self.bbox = bbox
        self.max_history = max_history
        self.first_frame_idx = frame_idx
        self.last_frame_idx = frame_idx
        self.total_detections = 1
        
        # History of ground footpoints
        self.history: List[TrackPoint] = [
            TrackPoint(
                x=bbox.foot_x,
                y=bbox.foot_y,
                frame_idx=frame_idx,
                timestamp_sec=timestamp_sec
            )
        ]

    def update(
        self,
        confidence: float,
        bbox: BoundingBox,
        frame_idx: int,
        timestamp_sec: float
    ):
        self.confidence = confidence
        self.bbox = bbox
        self.last_frame_idx = frame_idx
        self.total_detections += 1
        
        # Append new point
        self.history.append(
            TrackPoint(
                x=bbox.foot_x,
                y=bbox.foot_y,
                frame_idx=frame_idx,
                timestamp_sec=timestamp_sec
            )
        )
        # Keep window bounded
        if len(self.history) > self.max_history:
            self.history.pop(0)

    @property
    def age(self) -> int:
        return self.total_detections

    def get_recent_displacement(self, lookback: int = 5) -> Tuple[float, float]:
        """Calculates pixel displacement (dx, dy) over the last N observations."""
        if len(self.history) < 2:
            return (0.0, 0.0)
        idx0 = max(0, len(self.history) - 1 - lookback)
        p_old = self.history[idx0]
        p_cur = self.history[-1]
        return (p_cur.x - p_old.x, p_cur.y - p_old.y)


class TrajectoryTracker:
    """Maintains persistent multi-object tracking history across video frames."""

    def __init__(
        self,
        max_history: int = settings.MAX_TRAJECTORY_POINTS,
        inactive_threshold: int = settings.INACTIVE_TRACK_THRESHOLD
    ):
        self.max_history = max_history
        self.inactive_threshold = inactive_threshold
        self.tracks: Dict[int, TrackRecord] = {}
        self.total_unique_tracks: int = 0

    def update(
        self,
        detections: List[Dict[str, Any]],
        frame_idx: int,
        timestamp_sec: float
    ) -> List[TrackedObject]:
        """
        Updates tracking history with new frame detections.
        
        Returns:
            List of TrackedObject models active in the current frame.
        """
        active_ids_in_frame = set()
        active_tracked_objects: List[TrackedObject] = []
        
        for det in detections:
            track_id = det.get("track_id")
            if track_id is None:
                # Untracked detection (temporary or lost)
                continue
                
            active_ids_in_frame.add(track_id)
            bbox: BoundingBox = det["bbox"]
            confidence: float = det["confidence"]
            class_id: int = det["class_id"]
            class_name: str = det["class_name"]
            
            if track_id in self.tracks:
                # Update existing track record
                self.tracks[track_id].update(
                    confidence=confidence,
                    bbox=bbox,
                    frame_idx=frame_idx,
                    timestamp_sec=timestamp_sec
                )
            else:
                # Register new track
                self.tracks[track_id] = TrackRecord(
                    track_id=track_id,
                    class_id=class_id,
                    class_name=class_name,
                    confidence=confidence,
                    bbox=bbox,
                    frame_idx=frame_idx,
                    timestamp_sec=timestamp_sec,
                    max_history=self.max_history
                )
                self.total_unique_tracks += 1
            
            record = self.tracks[track_id]
            tracked_obj = TrackedObject(
                track_id=record.track_id,
                class_id=record.class_id,
                class_name=record.class_name,
                confidence=record.confidence,
                bbox=record.bbox,
                footpoint=(record.bbox.foot_x, record.bbox.foot_y),
                history=list(record.history),
                track_age_frames=record.age,
                is_active=True
            )
            active_tracked_objects.append(tracked_obj)
            
        # Prune old inactive tracks
        self._prune_inactive_tracks(current_frame=frame_idx)
        
        return active_tracked_objects

    def _prune_inactive_tracks(self, current_frame: int):
        """Removes tracks that have not been observed for longer than inactive_threshold."""
        stale_ids = [
            tid for tid, rec in self.tracks.items()
            if (current_frame - rec.last_frame_idx) > self.inactive_threshold
        ]
        for tid in stale_ids:
            del self.tracks[tid]

    def get_track_history(self, track_id: int) -> List[Tuple[float, float]]:
        """Returns list of (x, y) pixel coordinates for a given track."""
        if track_id in self.tracks:
            return [(p.x, p.y) for p in self.tracks[track_id].history]
        return []

    def reset(self):
        """Clears all tracking history."""
        self.tracks.clear()
        self.total_unique_tracks = 0
