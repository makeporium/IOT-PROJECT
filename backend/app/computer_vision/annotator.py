"""Automotive-grade OpenCV video frame annotator with Phase 2 Metric HUD, TTC, and Trajectory Prediction."""

from typing import List, Tuple, Dict, Any, Optional
import cv2
import numpy as np
from app.models.schemas import TrackedObject, BoundingBox


# BGR Color Palette (Modern Automotive UI)
CLASS_COLORS: Dict[str, Tuple[int, int, int]] = {
    "bicycle": (212, 182, 6),      # Cyan
    "motorcycle": (11, 158, 245),   # Amber/Orange
    "person": (94, 63, 244),       # Coral/Rose
    "car": (246, 130, 59),         # Clean Sky Blue
    "bus": (246, 92, 139),         # Purple
    "truck": (200, 70, 160),       # Indigo
}
DEFAULT_COLOR: Tuple[int, int, int] = (160, 160, 160)

# Hazard Urgency Colors
COLOR_CRITICAL: Tuple[int, int, int] = (68, 68, 239)   # Red BGR
COLOR_WARNING: Tuple[int, int, int] = (11, 158, 245)   # Amber BGR
COLOR_SAFE: Tuple[int, int, int] = (129, 185, 16)      # Emerald BGR


class FrameAnnotator:
    """Renders automotive detection boxes, track badges, trajectory trails, and HUD banner."""

    def __init__(
        self,
        show_hud: bool = True,
        show_boxes: bool = True,
        show_trajectories: bool = True,
        show_footpoints: bool = True,
        show_future_prediction: bool = True
    ):
        self.show_hud = show_hud
        self.show_boxes = show_boxes
        self.show_trajectories = show_trajectories
        self.show_footpoints = show_footpoints
        self.show_future_prediction = show_future_prediction

    def annotate(
        self,
        frame: np.ndarray,
        tracked_objects: List[TrackedObject],
        frame_idx: int,
        total_frames: int,
        fps: float,
        latency_ms: float
    ) -> np.ndarray:
        """Annotates the video frame with Phase 2 metric distance, TTC, and trajectory projections."""
        out = frame.copy()
        h, w = out.shape[:2]

        critical_count = sum(1 for o in tracked_objects if o.hazard_urgency == "CRITICAL")
        warning_count = sum(1 for o in tracked_objects if o.hazard_urgency == "WARNING")

        # 1. Draw Historical Trajectory Trails (behind boxes)
        if self.show_trajectories:
            for obj in tracked_objects:
                base_color = CLASS_COLORS.get(obj.class_name.lower(), DEFAULT_COLOR)
                pts = [(int(p.x), int(p.y)) for p in obj.history]
                if len(pts) > 1:
                    for j in range(1, len(pts)):
                        thickness = max(1, int(1 + 2 * (j / len(pts))))
                        cv2.line(out, pts[j - 1], pts[j], base_color, thickness, cv2.LINE_AA)
                    cv2.circle(out, pts[-1], 3, base_color, -1, cv2.LINE_AA)

        # 2. Draw Predicted Future Trajectory (Dashed / Waypoint line)
        if self.show_future_prediction:
            for obj in tracked_objects:
                if obj.is_approaching and obj.predicted_path:
                    color = COLOR_CRITICAL if obj.hazard_urgency == "CRITICAL" else (
                        COLOR_WARNING if obj.hazard_urgency == "WARNING" else (100, 200, 255)
                    )
                    future_pts = []
                    for wp in obj.predicted_path:
                        u, v = wp.get("pixel_u"), wp.get("pixel_v")
                        if u is not None and v is not None:
                            future_pts.append((int(u), int(v)))

                    # Connect current footpoint to future waypoints
                    foot_pt = (int(obj.bbox.foot_x), int(obj.bbox.foot_y))
                    all_pred = [foot_pt] + future_pts
                    for k in range(1, len(all_pred)):
                        # Dotted forward line
                        cv2.line(out, all_pred[k-1], all_pred[k], color, 2, cv2.LINE_AA)
                        cv2.circle(out, all_pred[k], 4, color, -1, cv2.LINE_AA)

        # 3. Draw Bounding Boxes, Metric Badges, and Footpoints
        if self.show_boxes:
            for obj in tracked_objects:
                # Color priority: CRITICAL red > WARNING amber > class color
                if obj.hazard_urgency == "CRITICAL":
                    border_color = COLOR_CRITICAL
                    badge_bg = COLOR_CRITICAL
                    badge_text_color = (255, 255, 255)
                elif obj.hazard_urgency == "WARNING":
                    border_color = COLOR_WARNING
                    badge_bg = COLOR_WARNING
                    badge_text_color = (15, 23, 42)
                else:
                    border_color = CLASS_COLORS.get(obj.class_name.lower(), DEFAULT_COLOR)
                    badge_bg = border_color
                    badge_text_color = (15, 23, 42)

                b = obj.bbox
                x1, y1, x2, y2 = int(b.x1), int(b.y1), int(b.x2), int(b.y2)

                # Draw bounding box outline (thicker if hazard)
                box_thickness = 3 if obj.hazard_urgency in ["CRITICAL", "WARNING"] else 2
                cv2.rectangle(out, (x1, y1), (x2, y2), border_color, box_thickness, cv2.LINE_AA)

                # HUD Corner brackets for automotive aesthetic
                corner_len = min(15, int(b.width / 4), int(b.height / 4))
                if corner_len > 4:
                    cv2.line(out, (x1, y1), (x1 + corner_len, y1), border_color, box_thickness + 1, cv2.LINE_AA)
                    cv2.line(out, (x1, y1), (x1, y1 + corner_len), border_color, box_thickness + 1, cv2.LINE_AA)
                    cv2.line(out, (x2, y1), (x2 - corner_len, y1), border_color, box_thickness + 1, cv2.LINE_AA)
                    cv2.line(out, (x2, y1), (x2, y1 + corner_len), border_color, box_thickness + 1, cv2.LINE_AA)
                    cv2.line(out, (x1, y2), (x1 + corner_len, y2), border_color, box_thickness + 1, cv2.LINE_AA)
                    cv2.line(out, (x1, y2), (x1, y2 - corner_len), border_color, box_thickness + 1, cv2.LINE_AA)
                    cv2.line(out, (x2, y2), (x2 - corner_len, y2), border_color, box_thickness + 1, cv2.LINE_AA)
                    cv2.line(out, (x2, y2), (x2, y2 - corner_len), border_color, box_thickness + 1, cv2.LINE_AA)

                # Construct Rich Metric Label:
                # "ID #07 | MOTORCYCLE | 8.4m | 24km/h | TTC 1.3s"
                dist_str = f"{obj.distance_m:.1f}m" if obj.distance_m is not None else "--"
                speed_str = f"{obj.speed_kmh:.0f}km/h" if obj.speed_kmh is not None and obj.speed_kmh > 1.0 else ""
                
                label_parts = [f"#{obj.track_id}", obj.class_name.upper(), dist_str]
                if speed_str:
                    label_parts.append(speed_str)
                if obj.ttc_seconds is not None:
                    label_parts.append(f"TTC {obj.ttc_seconds:.1f}s")

                label = " | ".join(label_parts)
                (txt_w, txt_h), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)

                badge_y1 = max(0, y1 - txt_h - 8)
                badge_y2 = y1
                badge_x2 = min(w, x1 + txt_w + 12)

                # Filled badge header
                cv2.rectangle(out, (x1, badge_y1), (badge_x2, badge_y2), badge_bg, -1)
                cv2.putText(
                    out,
                    label,
                    (x1 + 6, badge_y2 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    badge_text_color,
                    1,
                    cv2.LINE_AA
                )

                # 4. Ground Footpoint Marker & Spatial Coordinates
                if self.show_footpoints:
                    fx, fy = int(b.foot_x), int(b.foot_y)
                    # Concentric rings
                    cv2.circle(out, (fx, fy), 5, (255, 255, 255), 1, cv2.LINE_AA)
                    cv2.circle(out, (fx, fy), 2, border_color, -1, cv2.LINE_AA)

                    # Text tag displaying real metric coordinates: "(X: -1.2m, Y: 8.4m) +/-0.4m"
                    if obj.metric_x is not None and obj.metric_y is not None:
                        coord_tag = f"({obj.metric_x:+.1f}m, {obj.metric_y:.1f}m)"
                        if obj.uncertainty_m and obj.uncertainty_m > 0.1:
                            coord_tag += f" ~{obj.uncertainty_m:.1f}m"

                        cv2.putText(
                            out,
                            coord_tag,
                            (fx + 8, fy + 4),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.36,
                            (255, 255, 255),
                            1,
                            cv2.LINE_AA
                        )

        # 5. Top Telemetry HUD Banner
        if self.show_hud:
            hud_h = 38
            overlay = out.copy()
            cv2.rectangle(overlay, (0, 0), (w, hud_h), (15, 23, 42), -1)  # dark slate bar
            cv2.addWeighted(overlay, 0.88, out, 0.12, 0, out)

            hud_text = (
                f"DOORSAFE PERCEPTION & KINEMATICS  |  "
                f"FRAME: {frame_idx:04d}/{total_frames:04d}  |  "
                f"FPS: {fps:4.1f}  |  "
                f"ACTIVE: {len(tracked_objects)}  |  "
                f"WARN: {warning_count}  |  "
                f"CRIT: {critical_count}"
            )
            cv2.putText(
                out,
                hud_text,
                (14, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (241, 245, 249),
                1,
                cv2.LINE_AA
            )

            # High-visibility pulsing warning indicator if hazard is critical
            if critical_count > 0:
                alert_text = "DANGER: DOOR OPENING RESTRICTED"
                (at_w, at_h), _ = cv2.getTextSize(alert_text, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 2)
                alert_x = w - at_w - 24
                cv2.rectangle(out, (alert_x - 8, 5), (w - 8, hud_h - 5), COLOR_CRITICAL, -1)
                cv2.putText(
                    out,
                    alert_text,
                    (alert_x, 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.48,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA
                )
                cv2.line(out, (0, hud_h), (w, hud_h), COLOR_CRITICAL, 3)
            elif warning_count > 0:
                cv2.line(out, (0, hud_h), (w, hud_h), COLOR_WARNING, 3)
            else:
                cv2.line(out, (0, hud_h), (w, hud_h), (59, 130, 246), 2)

        return out
