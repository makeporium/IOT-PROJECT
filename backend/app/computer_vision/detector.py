"""YOLO Detection & ByteTrack tracking service for DOORSAFE."""

import time
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import torch
import numpy as np

# NumPy 2.x backward compatibility safeguard
if not hasattr(np, 'matrix'):
    try:
        from numpy.matrixlib import matrix
        np.matrix = matrix
    except Exception:
        class matrix(np.ndarray):
            pass
        np.matrix = matrix

from ultralytics import YOLO

from app.config import settings
from app.models.schemas import BoundingBox, DetectedObject

logger = logging.getLogger("doorsafe.detector")


class YOLODetector:
    """Wrapper around Ultralytics YOLO with ByteTrack tracking integration."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        conf_threshold: Optional[float] = None,
        iou_threshold: Optional[float] = None,
    ):
        self.model_path = model_path or settings.MODEL_PATH
        self.conf_threshold = conf_threshold or settings.DEFAULT_CONF_THRESHOLD
        self.iou_threshold = iou_threshold or settings.DEFAULT_IOU_THRESHOLD
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        
        logger.info(f"Initializing YOLO detector on device '{self.device}' with model: {self.model_path}")
        
        # Verify model file exists
        if not Path(self.model_path).exists():
            # Fallback check in YOLO MODEL folder
            fallback = Path(settings.PROJECT_ROOT) / "YOLO MODEL" / "yolo26n.pt"
            if fallback.exists():
                logger.warning(f"Model not found at {self.model_path}, using fallback at {fallback}")
                self.model_path = str(fallback)
            else:
                raise FileNotFoundError(f"YOLO model weights file not found at {self.model_path}")

        # Load Ultralytics model
        self.model = YOLO(self.model_path)
        self.model_names: Dict[int, str] = self.model.names
        
        # Filter for automotive / road safety target classes
        self.target_classes = settings.TARGET_CLASSES
        self.target_class_ids: List[int] = [
            cid for cid, name in self.model_names.items() if name in self.target_classes
        ]
        
        logger.info(f"YOLO model loaded successfully. Target classes ({len(self.target_class_ids)}): "
                    f"{[self.model_names[cid] for cid in self.target_class_ids]}")

    def track_frame(
        self,
        frame: np.ndarray,
        conf_threshold: Optional[float] = None,
        persist: bool = True
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        Runs YOLO object detection and ByteTrack tracking on a single video frame.
        
        Returns:
            Tuple of:
              - list of detection dictionaries:
                {
                   'track_id': int or None,
                   'class_id': int,
                   'class_name': str,
                   'confidence': float,
                   'bbox': BoundingBox
                }
              - inference latency in milliseconds
        """
        conf = conf_threshold if conf_threshold is not None else self.conf_threshold
        t0 = time.perf_counter()
        
        # Ultralytics track invocation with ByteTrack
        results = self.model.track(
            source=frame,
            persist=persist,
            tracker=settings.TRACKER_TYPE,
            conf=conf,
            iou=self.iou_threshold,
            classes=self.target_class_ids,
            device=self.device,
            verbose=False
        )
        
        inference_latency_ms = (time.perf_counter() - t0) * 1000.0
        
        detections: List[Dict[str, Any]] = []
        
        if results and len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes
            for i in range(len(boxes)):
                xyxy = boxes.xyxy[i].cpu().numpy()
                x1, y1, x2, y2 = float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])
                
                cls_id = int(boxes.cls[i].item())
                class_name = self.model_names.get(cls_id, f"class_{cls_id}")
                confidence = float(boxes.conf[i].item())
                
                # Extract ByteTrack tracking ID if available
                track_id = None
                if boxes.id is not None:
                    track_id = int(boxes.id[i].item())
                
                width = x2 - x1
                height = y2 - y1
                center_x = (x1 + x2) / 2.0
                center_y = (y1 + y2) / 2.0
                foot_x = center_x
                foot_y = y2  # Ground contact assumption
                
                bbox = BoundingBox(
                    x1=round(x1, 2),
                    y1=round(y1, 2),
                    x2=round(x2, 2),
                    y2=round(y2, 2),
                    width=round(width, 2),
                    height=round(height, 2),
                    center_x=round(center_x, 2),
                    center_y=round(center_y, 2),
                    foot_x=round(foot_x, 2),
                    foot_y=round(foot_y, 2)
                )
                
                detections.append({
                    "track_id": track_id,
                    "class_id": cls_id,
                    "class_name": class_name,
                    "confidence": round(confidence, 3),
                    "bbox": bbox
                })
                
        return detections, inference_latency_ms

    def reset_tracker(self):
        """Resets ByteTrack state tracker in Ultralytics model."""
        try:
            if hasattr(self.model, "predictor") and self.model.predictor is not None:
                if hasattr(self.model.predictor, "trackers"):
                    if self.model.predictor.trackers:
                        for trk in self.model.predictor.trackers:
                            if hasattr(trk, "reset"):
                                trk.reset()
                    delattr(self.model.predictor, "trackers")
                if hasattr(self.model.predictor, "vid_path"):
                    delattr(self.model.predictor, "vid_path")
        except Exception as e:
            logger.debug(f"Tracker reset notice: {e}")
