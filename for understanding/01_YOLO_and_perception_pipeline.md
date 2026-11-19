# DOORSAFE — YOLO & Perception Pipeline: Complete Explanation

This document explains every step of how the system goes from a raw video frame to
"LOCK the door". We start from first principles so the maths makes sense.

---

## The Big Picture

The system answers one question for every video frame:
**"If the door opens right now, will something hit it?"**

The pipeline runs these steps in order, every frame:

```
Raw video frame
      ↓
[1] YOLO Detection        → "there is a motorcycle at pixel (450, 720)"
      ↓
[2] ByteTrack Tracking    → "that motorcycle is track #7, same one as last frame"
      ↓
[3] Pinhole Projection    → "track #7 is 3.4 m behind the car, 1.2 m to the left"
      ↓
[4] Alpha-Beta Filter     → "track #7 is moving at −4.2 m/s (approaching)"
      ↓
[5] TTC Calculator        → "track #7 will enter the door zone in 1.8 seconds"
      ↓
[6] Decision Rules        → "1.8 s < 2.0 s threshold → CRITICAL threat"
      ↓
[7] State Machine         → "CRITICAL → servo 0° → DOOR LOCKED"
```

Each step is a separate Python file. Let's go through all of them with the actual maths.

---

## Step 1 — YOLO Object Detection

**File:** `backend/app/computer_vision/detector.py`

### What YOLO is

YOLO (You Only Look Once) is a **pretrained neural network** that looks at an image
once and outputs a list of bounding boxes with class labels and confidence scores.

We use **YOLOv8/v11** from the Ultralytics library. It was trained on the **COCO dataset**
(Common Objects in Context — 80 object classes). We only care about these classes:
`car, motorcycle, bus, truck, bicycle, person`.

We did **not** train it ourselves. We download the pretrained weights and run inference.

### What YOLO outputs per detection

```
x1, y1, x2, y2   ← bounding box corners in pixels (top-left and bottom-right)
class_id          ← integer (e.g. 2 = car, 3 = motorcycle)
confidence        ← 0.0 to 1.0 (how sure YOLO is)
track_id          ← integer assigned by ByteTrack (see Step 2)
```

The **foot point** — the most important pixel — is the bottom-center of the bounding box:

```
foot_x = (x1 + x2) / 2
foot_y = y2
```

This is the point where the detected object touches the ground. We use it for all
the distance geometry below.

### Confidence threshold

The dashboard slider labeled **"YOLO Confidence Threshold"** (default 0.35) filters out
weak detections. If YOLO says "I'm 28% sure this is a car" it is ignored.
Lower = more detections but more false positives. Higher = fewer detections but misses
partially visible objects.

### The actual code

`backend/app/computer_vision/detector.py` — `YOLODetector.track_frame()`:

```python
results = self.model.track(
    source=frame,
    persist=persist,          # keep ByteTrack IDs alive across calls
    tracker=settings.TRACKER_TYPE,   # "bytetrack.yaml"
    conf=conf,
    iou=self.iou_threshold,
    classes=self.target_class_ids,   # only car/bike/bus/truck/person/bicycle
    device=self.device,       # "cuda" if available, else "cpu"
    verbose=False
)

boxes = results[0].boxes
for i in range(len(boxes)):
    xyxy = boxes.xyxy[i].cpu().numpy()
    x1, y1, x2, y2 = float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])
    track_id = int(boxes.id[i].item()) if boxes.id is not None else None

    foot_x = (x1 + x2) / 2.0   # bottom-center of the box
    foot_y = y2                # the box's bottom edge = ground contact
```

Notice `model.track()` (not `model.predict()`) — this single Ultralytics call does both
YOLO detection **and** ByteTrack tracking in one pass; `persist=True` is what keeps the
same `track_id` assigned across frames.

---

## Step 2 — ByteTrack: Keeping IDs Across Frames

**Built into YOLO via Ultralytics; managed in** `detector.py`

YOLO detects independently every frame. Without tracking, every frame the motorcycle
gets a fresh random bounding box — there is no concept of "same object as last time".

**ByteTrack** solves this. It is a tracking-by-detection algorithm that matches new
detections to existing tracks using **IoU (Intersection over Union)** between boxes.

```
IoU = (area of overlap) / (area of union)
```

If two boxes (from consecutive frames) overlap enough, ByteTrack assigns them the same
`track_id`. This gives us a stable identity for each road user across time, which is
essential for computing velocity (you need position at time t and time t+1).

The **"ID"** column in the dashboard's "Observed Road Obstacles" table is the ByteTrack
`track_id`.

ByteTrack is configured as a YAML file name (`settings.TRACKER_TYPE = "bytetrack.yaml"`)
passed straight into Ultralytics — it is a built-in tracker shipped with the library,
not code we wrote ourselves.

---

## Step 3 — Pinhole Camera Ground Projection (Pixel → Metres)

**File:** `backend/app/distance_estimation/camera_geometry.py`

This is the most important maths in the system. We know where something is in
**pixels**. We need to know where it is in **metres on the road**.

### Camera model

We model the camera as a **pinhole camera** mounted on the car door at:
- Height `h = 1.15 m` above road surface
- Pointing down at pitch angle `θ_p = 8.5°` below horizontal
- Horizontal field of view `HFOV = 72°`

From HFOV and image width `W`, we calculate the **focal length in pixels**:

```
fx = (W / 2) / tan(HFOV / 2)
```

For 1920×1080 and 72° HFOV:
```
fx = 960 / tan(36°) = 960 / 0.7265 ≈ 1321 pixels
```

The **optical centre** is the middle of the image: `cx = W/2 = 960`, `cy = H/2 = 540`.

### From pixel to angle

For a foot point at pixel `(u, v)`, the vertical angle from optical centre is:

```
α = arctan((v - cy) / fy)
```

The **total depression angle** below horizontal (combining camera pitch + pixel offset):

```
γ = θ_p + α
```

For example: a foot point at `v = 720` in a 1080-height image:
```
α = arctan((720 - 540) / 1321) = arctan(0.1363) ≈ 7.77°
γ = 8.5° + 7.77° = 16.27°
```

### From angle to ground distance (Y)

Using basic trigonometry on the right-angle triangle formed by camera height and
the ground ray:

```
Y = h / tan(γ)
```

Continuing the example:
```
Y = 1.15 / tan(16.27°) = 1.15 / 0.2917 ≈ 3.94 m
```

The object is 3.94 metres behind the car. This is the **longitudinal distance** shown
in the dashboard's DIST column.

### Lateral position (X)

A pixel at column `u` is displaced from centre by `(u - cx)` pixels. With full
perspective correction for the camera pitch:

```
X = Y × (u - cx) / fx × cos(α) / cos(γ)
```

The `cos(α)/cos(γ)` term is the pitch correction. Without it, objects far to the left
or right at distance would have their lateral position understated.

**Sign convention (ISO 8855):**
- Negative X = left side of vehicle (driver's door side, camera-left)
- Positive X = right side of vehicle (passenger side)

The door zone lives entirely at **negative X** (−2.20 to −0.70 m).

### Uncertainty estimate

The projection gets less accurate as objects get farther away. We estimate:

```
dY/dv ≈ Y² / (h × fy)
uncertainty_m = dY/dv × σ_pixel
```

Where `σ_pixel = 4.0` (assumed YOLO foot-point pixel noise). An object at 10 m has
~0.6 m uncertainty; at 30 m it's ~5 m. High uncertainty at close range (bad detection)
triggers `UNCERTAIN` → fail-safe lock.

### The actual code

`backend/app/distance_estimation/camera_geometry.py` — `PinholeGroundProjector.pixel_to_ground()`:

```python
alpha = math.atan((v - self.cy) / self.fy)   # vertical ray angle from optical center
gamma = self.pitch_rad + alpha               # total depression angle below horizontal

if gamma <= 0.015:    # ray points above the horizon -- no valid ground intersection
    return GroundCoordinate(x_m=0.0, y_m=100.0, distance_m=100.0,
                             confidence=0.05, uncertainty_m=35.0, is_valid=False)

y = self.height / math.tan(gamma)            # longitudinal distance
x = (y * (u - self.cx) / self.fx) * (math.cos(alpha) / math.cos(gamma))  # lateral, pitch-corrected
dist = math.sqrt(x**2 + y**2)

dY_dv = (y ** 2) / (self.height * self.fy)
uncertainty_y = abs(dY_dv * self.params.footpoint_pixel_sigma)
```

The `gamma <= 0.015` check is the horizon guard: if the pitch-corrected ray angle is
basically horizontal, the maths would divide by a near-zero `tan(gamma)` and produce
a nonsense huge distance — the code instead returns a fixed low-confidence fallback
(100 m, confidence 0.05) rather than a wild number.

---

## Step 4 — Alpha-Beta Filter: Smoothing Position & Computing Velocity

**File:** `backend/app/trajectory/kinematics.py`

The raw ground coordinates from Step 3 are **noisy** — YOLO's bounding box jitters
by a few pixels each frame, which translates to 0.1–0.3 m of position noise in the
metric space. Computing velocity as `(x_now − x_prev) / dt` on noisy data gives
wildly wrong velocities.

The **alpha-beta filter** is a lightweight alternative to a Kalman filter that
smooths both position and velocity together.

### Prediction step (where we expect the object to be)

```
x_pred = x + vx × dt
y_pred = y + vy × dt
```

### Residual (how far off we were)

```
res_x = measured_x − x_pred
res_y = measured_y − y_pred
```

### Correction step (blend prediction and measurement)

```
x_new = x_pred + α × res_x        (α = 0.70 — trust new measurement 70%)
y_new = y_pred + α × res_y

vx_new = vx + (β / dt) × res_x    (β = 0.35 — velocity update)
vy_new = vy + (β / dt) × res_y
```

**Intuition:** α is how quickly position tracks the measurement. β is how quickly
velocity catches up. High α → responsive but noisy. Low α → smooth but lags on
sudden movements. These values (0.70 / 0.35) are tuned for road traffic at ~30 fps.

### Sign convention for velocity

- **vy < 0** means the object is getting closer (Y distance shrinking) → approaching
- **vy > 0** means the object is moving away
- The threshold for "approaching" is `vy < −0.30 m/s` to ignore near-stationary noise

The **SPEED** column in the dashboard shows:
```
speed_kmh = sqrt(vx² + vy²) × 3.6
```

### The actual code

`backend/app/trajectory/kinematics.py` — `ObjectKinematicTracker.update()`:

```python
dt = timestamp - self.last_timestamp
if dt <= 0.001:
    dt = 0.033   # ~30 FPS fallback, guards against a zero/negative frame gap

pred_x = self.x + self.vx * dt
pred_y = self.y + self.vy * dt
res_x = meas_x - pred_x
res_y = meas_y - pred_y

# If a track suddenly teleports (occlusion recovery, bad detection), widen
# the gain so the filter snaps to the new position instead of lagging for
# several frames while it "catches up".
gain_scale = 1.5 if abs(res_y) > 10.0 else 1.0

self.x = pred_x + min(1.0, self.alpha * gain_scale) * res_x
self.y = pred_y + min(1.0, self.alpha * gain_scale) * res_y
self.vx = self.vx + (self.beta / dt) * res_x
self.vy = self.vy + (self.beta / dt) * res_y

self.vy = max(-36.0, min(36.0, self.vy))   # clamp to 130 km/h — no road user is faster
self.vx = max(-15.0, min(15.0, self.vx))
```

The `gain_scale` boost on large residuals is a practical patch: YOLO/ByteTrack
occasionally loses a track for a frame or two (occlusion) and re-acquires it a few
pixels off from where plain extrapolation predicted. Without the boost, the filter
would need several frames to "trust" the new position.

---

## Step 5 — Time-To-Collision (TTC) Calculator

**File:** `backend/app/trajectory/ttc_calculator.py`

TTC answers: **"How many seconds until this object enters the door-opening zone?"**

The door zone is a rectangle in the X-Y metric plane:
```
X: −2.20 m to −0.70 m   (from the car side outward to the max door swing)
Y: −0.80 m to +2.20 m   (rear to front of the door panel)
```

### 1D time interval for one axis

If an object is at position `p₀` moving at velocity `v`, it enters the zone boundary
`[b_min, b_max]` between times:

```
t1 = (b_min − p₀) / v
t2 = (b_max − p₀) / v
t_enter = min(t1, t2)
t_exit  = max(t1, t2)
```

(If `v = 0`, the object never enters unless it's already inside.)

### 2D collision window

Compute the X interval `[tx_enter, tx_exit]` and Y interval `[ty_enter, ty_exit]`
separately. The object is inside the zone only when it is simultaneously inside
**both** intervals:

```
t_conflict_enter = max(tx_enter, ty_enter)
t_conflict_exit  = min(tx_exit,  ty_exit)
```

A **collision exists** if and only if:
```
t_conflict_enter ≤ t_conflict_exit   AND   t_conflict_exit ≥ 0
```

The **TTC** shown in the dashboard is `max(0, t_conflict_enter)` — time until the
object first enters the zone.

### Urgency thresholds

```
TTC ≤ 2.50 s  →  CRITICAL  →  door locks immediately
TTC ≤ 4.50 s  →  WARNING   →  haptic buzz, door stays unlocked
TTC > 4.50 s  →  SAFE
```

The **MIN TTC** metric on the dashboard shows the smallest TTC across all tracked
objects in the current frame.

### Passenger-side gate

If `x_m > +0.70` (object is on the right side of the car), it physically cannot
reach the left-side door zone without passing through the car body. The calculator
returns SAFE immediately without computing TTC.

### The actual code

`backend/app/trajectory/ttc_calculator.py` — the 1D interval helper and the overlap check:

```python
def _get_1d_time_interval(self, p0, v, bound_min, bound_max):
    """[t_min, t_max] where bound_min <= p0 + v*t <= bound_max."""
    if abs(v) < 1e-4:
        if bound_min <= p0 <= bound_max:
            return (-float('inf'), float('inf'))   # not moving, already inside
        return (float('inf'), -float('inf'))        # not moving, never enters
    t1 = (bound_min - p0) / v
    t2 = (bound_max - p0) / v
    return (min(t1, t2), max(t1, t2))

# In calculate():
tx_enter, tx_exit = self._get_1d_time_interval(x_m, vx_mps, self.x_min, self.x_max)
ty_enter, ty_exit = self._get_1d_time_interval(y_m, vy_mps, self.y_min, self.y_max)
t_enter = max(tx_enter, ty_enter)
t_exit  = min(tx_exit,  ty_exit)

if t_enter <= t_exit and t_exit >= 0.0:
    actual_ttc = max(0.0, t_enter)
    urgency = "CRITICAL" if actual_ttc <= self.critical_ttc else \
              ("WARNING" if actual_ttc <= self.warning_ttc else "SAFE")
```

> **Edge case fixed in this project:** when an object is stationary (`abs(v) < 1e-4`)
> and already inside the zone bounds on *both* axes, `t_exit` comes back as
> `float('inf')` — there's no computable exit time for something that never moves.
> Python's `json.dump` happily writes that as the bareword `Infinity`, which is not
> valid JSON. When the dashboard later re-requested that frame, FastAPI's JSON
> encoder (`allow_nan=False`) threw and the endpoint returned HTTP 500 — which is
> what silently blanked the BEV radar and froze the HUD badge in one real debugging
> session on this project. The fix: when `t_exit` isn't finite, store `None` instead
> of the raw infinity.

---

## Step 6 — Collision Decision Rules

**File:** `backend/app/safety_engine/decision_rules.py`

This layer re-evaluates each object using both the TTC result AND the geometric zone
directly. It runs seven checks in order, returning the first one that matches:

| Check | Condition | Result |
|-------|-----------|--------|
| 1 | No metric coordinates | UNCERTAIN |
| 2 | High uncertainty near car | UNCERTAIN |
| 3 | Object already inside door zone | CRITICAL (TTC=0) |
| 4+5 | Trajectory intersects zone & TTC < 2.0 s | CRITICAL |
| 4+5 | Trajectory intersects zone & TTC < 4.0 s | WARNING |
| 6 | Within 1.0 m of zone edge, close, moving | WARNING |
| 7 | None of the above | SAFE |

The **THREAT** column in the dashboard table shows the result of this rule engine per
object.

**UNCERTAIN** is treated the same as CRITICAL by the state machine (fail-safe lock).
If the system cannot measure safely, it defaults to locked.

### The actual code

`backend/app/safety_engine/decision_rules.py` — the core of `evaluate_object()`:

```python
# 3. Object already inside the physical door danger corridor
if self.door_zone.contains_point(x, y, speed_kmh):
    return ThreatAssessment(threat_level=ThreatLevel.CRITICAL, ttc_seconds=0.0, ...)

# 4. Trajectory Corridor Intersection Check
intersects, entry_t, _ = self.door_zone.intersects_trajectory(
    x=x, y=y, vx=vx, vy=vy, time_horizon_s=self.warning_ttc_s
)
effective_ttc = ttc if ttc is not None else entry_t
is_conflict = intersects or obj.get("is_conflict_predicted", False)

# 5. Critical Threshold Evaluation
if is_conflict and effective_ttc is not None:
    if effective_ttc < self.critical_ttc_s:        # 2.0 s
        return ThreatAssessment(threat_level=ThreatLevel.CRITICAL, ...)
    elif effective_ttc <= self.warning_ttc_s:       # 4.0 s
        return ThreatAssessment(threat_level=ThreatLevel.WARNING, ...)
```

Notice this engine checks the door zone **twice**, two different ways: `contains_point`
(is the object physically inside the rectangle *right now*) and `intersects_trajectory`
(will its predicted path pass through the rectangle). Either one alone is enough
evidence of a conflict — TTC then decides how urgent it is.

---

## Step 7 — Safety State Machine

**File:** `backend/app/safety_engine/state_machine.py`

Collects all threat assessments from Step 6 and decides the final actuator command.

### States

```
SAFE       → servo 90° (door unlatched), no alert
WARNING    → servo 90° (door unlatched), haptic buzz
CRITICAL   → servo 0°  (door physically locked)
FAIL_SAFE  → servo 0°  (locked due to sensor failure or uncertainty)
```

### Hysteresis (the 1-second hold)

A vehicle can momentarily disappear from YOLO (occlusion, motion blur). Without
hysteresis, the door would unlock the instant the motorcycle is hidden by a truck
for one frame. That is dangerous.

The rule: **once CRITICAL is set, it cannot be released for at least 1.0 second**,
even if the next frame sees no threats.

```python
elapsed_since_crit = now - last_critical_timestamp
if state is CRITICAL and new_raw_state != CRITICAL:
    if elapsed_since_crit < 1.0s:
        keep_state = CRITICAL   # hold lock
```

### The actual code

`backend/app/safety_engine/state_machine.py` — `SafetyStateMachine.update()`:

```python
if critical_list or uncertain_list:
    raw_target_state = SafetyState.CRITICAL if critical_list else SafetyState.FAIL_SAFE
    self.last_critical_timestamp = now     # monotonic clock, not video time
elif warning_list:
    raw_target_state = SafetyState.WARNING
else:
    raw_target_state = SafetyState.SAFE

elapsed_since_crit = now - self.last_critical_timestamp
if self.state in (SafetyState.CRITICAL, SafetyState.FAIL_SAFE) and raw_target_state != SafetyState.CRITICAL:
    if elapsed_since_crit < self.lock_hold_duration_s:   # 1.0 s
        raw_target_state = self.state    # refuse to leave CRITICAL yet

if self.state == SafetyState.CRITICAL:
    self.actuator_command = ActuatorCommand.LOCK_ENGAGED
    self.servo_angle_deg = 0
```

Note `self.last_critical_timestamp = now` uses `time.perf_counter()` (wall-clock,
monotonic), **not** the video's `timestamp_sec`. This matters: during a recorded-video
analysis run, this same state machine class also runs live in the browser's IoT bridge
(Phase 4 — covered in the next document), where frame updates arrive at real wall-clock
speed. Keeping the hysteresis on a monotonic clock means both the one-time offline
analysis and the live interactive session use identical hysteresis behavior.

### HUD badge on dashboard

The badge in the top-right of the video card shows:
- **"90° UNLATCHED (SAFE)"** — servo at 90°, any state except CRITICAL/FAIL_SAFE
- **"0° LOCKED (VETO)"** — servo at 0°, CRITICAL or FAIL_SAFE state

---

## The Burned-In Video Overlay vs. the Live HUD Badge

The annotated video you watch has text burned permanently into its pixels —
`backend/app/computer_vision/annotator.py`:

```python
critical_count = sum(1 for o in tracked_objects if o.hazard_urgency == "CRITICAL")
warning_count  = sum(1 for o in tracked_objects if o.hazard_urgency == "WARNING")
...
if critical_count > 0:
    alert_text = "DANGER: DOOR OPENING RESTRICTED"
```

This is written **once**, during the one-time offline analysis pass, and never changes
again — it's just pixels in an MP4 file from then on.

The **HUD badge** at the top of the dashboard (`"90° UNLATCHED (SAFE)"` /
`"0° LOCKED (VETO)"` — "VETO" meaning the safety engine has overridden/blocked a door
open request) is computed separately, *live, in the browser*, every time the displayed
video frame changes — `index.html`'s `updateLiveTargetsFromFrame()`:

```javascript
frame.objects.forEach(obj => {
  if (obj.hazard_urgency === 'CRITICAL') { frameMaxUrgency = 'CRITICAL'; }
  else if (obj.hazard_urgency === 'WARNING' && frameMaxUrgency !== 'CRITICAL') { frameMaxUrgency = 'WARNING'; }
});
setActuatorVisuals(frameMaxUrgency === 'CRITICAL' ? 0 : 90, frameMaxUrgency === 'CRITICAL');
```

Both read the exact same `hazard_urgency` field, so they should always agree. The only
way they can disagree is if the badge's data source — the telemetry JSON fetched over
HTTP — never loaded in the browser (a 404, a 500, a network blip). If that fetch fails,
the badge has nothing to read and just freezes on whatever it last showed, while the
video (which doesn't depend on that fetch at all) keeps showing the correct burned-in
state. This exact failure mode — and the `Infinity`-in-JSON bug that caused it — is
covered in the TTC section above and in the troubleshooting notes of this project.

---

## What the Dashboard Displays — Field by Field

### "Observed Road Obstacles" table

| Column | Meaning |
|--------|---------|
| ID | ByteTrack `track_id` — same object across frames |
| CLASS | YOLO class label (car, motorcycle, bus, etc.) |
| DIST | Euclidean distance from camera in metres: `√(X² + Y²)` |
| SPEED | Ground speed in km/h: `√(vx² + vy²) × 3.6` |
| TTC | Time-To-Collision in seconds (from Step 5). `--` if no conflict predicted |
| THREAT | SAFE / WARNING / CRITICAL (from Step 6 decision rules) |

### Metrics bar under the video

| Metric | Meaning |
|--------|---------|
| CORRIDOR CONFLICTS | Count of objects where `is_conflict_predicted = True` |
| MIN TTC | Smallest TTC value across all objects in the current frame |
| INFERENCE LATENCY | Time YOLO took to run on this frame in milliseconds |
| THROUGHPUT | Frames per second the pipeline processed |

### Top-Down Bird's Eye View (BEV) Radar

The radar shows the road behind the car from above. The car icon sits at the bottom
centre.

- **Each dot** = one tracked object at its metric (X, Y) position
- **Dot colour**: green = SAFE, orange = WARNING, red = CRITICAL
- **Dashed arcs**: distance rings at 10 m and 20 m
- **Coloured lines from each dot**: the predicted trajectory over the next ~2 seconds
  (linear extrapolation using the filtered velocity from Step 4)

The hatched rectangle to the left of the car icon is the **door zone** (−2.20 to
−0.70 m lateral, −0.80 to +2.20 m longitudinal).

---

## File Map — All Backend Files

```
backend/app/
│
├── config.py                    Settings: YOLO model path, directories, target classes,
│                                camera defaults. All constants in one place.
│
├── computer_vision/
│   ├── detector.py              Wraps Ultralytics YOLO. Runs inference on one frame,
│   │                            returns List[TrackedObject] with bbox + track_id.
│   ├── annotator.py             Draws overlays on the video frame: bbox, track ID,
│   │                            speed, TTC, hazard colour, trajectory lines.
│   └── pipeline.py              The main loop. Opens a video file, runs Steps 1-7
│                                on every frame, writes the annotated output MP4,
│                                saves telemetry JSON.
│
├── tracking/
│   └── trajectory_tracker.py    Stores the last N (x,y,t) positions per track_id.
│                                Used to feed the kinematic filter with history.
│
├── distance_estimation/
│   └── camera_geometry.py       Pinhole projection: pixel foot point → metric (X,Y).
│                                Also the reverse: metric (X,Y) → pixel (for BEV).
│
├── trajectory/
│   ├── kinematics.py            Alpha-beta filter. One ObjectKinematicTracker per
│   │                            track_id. Outputs smooth position + velocity.
│   ├── ttc_calculator.py        2D AABB interval overlap TTC. Returns hazard_urgency.
│   └── predictor.py             Simple linear waypoint predictor for the BEV arrows.
│
├── safety_engine/
│   ├── zone_geometry.py         DoorZonePolygon: the rectangle in metric space,
│   │                            speed-dependent expansion, contains_point, intersects.
│   ├── decision_rules.py        7-rule evaluation engine. Combines TTC + zone check
│   │                            + uncertainty guardrail → ThreatLevel per object.
│   └── state_machine.py         SAFE/WARNING/CRITICAL/FAIL_SAFE with 1s hysteresis.
│                                Drives servo_angle_deg and is_door_locked.
│
├── api/
│   ├── routes_video.py          Upload, list samples, start YOLO job, poll progress,
│   │                            stream video, fetch telemetry JSON.
│   ├── routes_safety.py         Live door-handle trigger, current safety state.
│   ├── routes_iot.py            MQTT sync endpoint (/api/iot/sync-observation).
│   ├── routes_health.py         System health, YOLO model status.
│   ├── routes_twin.py           Phase 6 digital twin simulation endpoints.
│   ├── routes_ml.py             Phase 7 risk classifier training/inference.
│   ├── routes_benchmark.py      Phase 9 validation benchmarking.
│   ├── routes_events.py         Phase 5 flight recorder event log.
│   ├── routes_assistant.py      Phase 8 AI command-centre chat.
│   └── routes_calibration.py    Camera calibration parameter updates.
│
├── iot/
│   ├── bridge.py                IoTBridge: receives a telemetry frame, pushes the
│   │                            actuator command to MQTT for the ESP32.
│   └── mqtt_client.py           Paho MQTT wrapper for broker.emqx.io connection.
│
├── models/
│   └── schemas.py               Pydantic data models: TrackedObject, FrameTelemetry,
│                                BoundingBox, JobStatus, etc.
│
├── simulation/
│   └── ...                      Phase 6: synthetic scenario generator, virtual radar,
│                                digital twin session management.
│
├── evaluation/
│   └── ...                      Phase 9: detection stats, distance error analysis,
│                                safety matrix computation.
│
├── ml/
│   └── risk_classifier.py       Phase 7: lightweight classifier trained on extracted
│                                features (distance, speed, TTC) → risk advisory.
│
├── persistence/
│   └── event_store.py           Phase 5: SQLite + optional Supabase event log for the
│                                flight recorder tab.
│
└── static/
    └── index.html               The entire dashboard in one HTML file (vanilla JS,
                                 no framework). ~2500 lines.
```

---

## Why So Many Files?

The project was built in phases. Only the core chain (detector → camera_geometry →
kinematics → ttc_calculator → state_machine) is strictly needed to lock the door.

The rest are from later project phases:
- **simulation/** — test the system with synthetic traffic (no real camera needed)
- **evaluation/** — measure accuracy against ground truth
- **ml/** — train a risk classifier on top of YOLO features
- **persistence/** — log every event for the flight recorder tab
- **iot/** — push commands to the physical ESP32 via MQTT

Each phase added new files rather than modifying existing ones, so the core chain
stayed stable and testable independently.

---

## Why No Custom Model Training?

YOLO pretrained on COCO already detects every road participant class we care about
(car, motorcycle, bicycle, bus, truck, person) with good accuracy. The collision
decision is **pure geometry** — it doesn't depend on what the object looks like, only
on where it is and how fast it's moving. There's nothing to train for that; it's
calculated analytically.

The only optional training is the Phase 7 `AdvisoryRiskClassifier`, which learns a
small decision tree on top of the numeric features (distance, speed, TTC) to give
a risk score. It is advisory only and does not control the door lock.
