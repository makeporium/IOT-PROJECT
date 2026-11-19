# DOORSAFE — Simulation, ML, Flight Recorder & Everything Else

This is the third and final documentation file. Docs 1 and 2 covered:
- Doc 1: YOLO detection → tracking → TTC → safety state machine → video annotation
- Doc 2: MQTT → IoTBridge → ESP32 firmware → live safety sync

This doc covers everything that was not in those two:

| Phase | What it is | Key files |
|---|---|---|
| 5 | Flight recorder — durable event log + Supabase sync | `persistence/event_store.py`, `api/routes_events.py` |
| 6 | Digital twin — deterministic simulation without a real video | `simulation/`, `api/routes_twin.py` |
| 7 | ML advisory classifier — Random Forest over synthetic data | `ml/risk_classifier.py` |
| 3 (complement) | Collision decision rules — the second safety evaluator | `safety_engine/decision_rules.py` |
| 9 | Benchmark suite — ties it all together into a single report | `benchmarking/suite.py` |
| Cross-cutting | REST health/safety/calibration endpoints | `api/routes_health.py`, `routes_safety.py`, `routes_calibration.py` |

---

## Phase 5 — The Flight Recorder

**Files:** `backend/app/persistence/event_store.py`, `backend/app/api/routes_events.py`

### Why it exists

Every safety-critical decision — a door unlock, a lock veto, a touch event that was
refused — needs to be logged permanently, not just held in memory, so you can audit
exactly what happened during an incident. The flight recorder is that log.

### Two-layer storage: SQLite first, Supabase second

```python
class EventStore:
    def __init__(self, database_path: Path):
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS vehicle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at REAL NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                safety_state TEXT,
                door_locked INTEGER,
                payload_json TEXT NOT NULL
            )
        """)
        self._cloud_sync = (
            SupabaseEventSync(settings.SUPABASE_URL, settings.SUPABASE_PUBLISHABLE_KEY, ...)
            if settings.SUPABASE_URL and settings.SUPABASE_PUBLISHABLE_KEY
            else None
        )
```

SQLite is the **authoritative** store — it's always written first, synchronously,
while `SupabaseEventSync` works in a background thread from a queue. If the network
is down, the event stays in SQLite and Supabase just never gets it. If the app crashes
after the SQLite write but before the network send, the event is not lost — it's just
not in the cloud.

### The Supabase sync worker

```python
class SupabaseEventSync:
    def __init__(self, url, publishable_key, table):
        self._queue: Queue[Dict] = Queue(maxsize=2000)
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self):
        import requests
        while True:
            event = self._queue.get()
            try:
                requests.post(
                    f"{url}/rest/v1/{table}",
                    headers={"apikey": key, "Authorization": f"Bearer {key}", ...},
                    json=event, timeout=4
                )
            except Exception as exc:
                self.last_error = str(exc)   # logged, never raised
```

`daemon=True` means this thread dies automatically when the FastAPI server exits —
no teardown code needed. `Queue(maxsize=2000)` acts as a buffer: up to 2000 events can
pile up during a network outage without blocking any sensor or MQTT callback.

### What events get recorded

| event_type | Triggered by |
|---|---|
| `DOOR_UNLOCK_PERMITTED` | IoTBridge — touch event, safety said OK |
| `DOOR_LOCK_VETO` | IoTBridge — touch event, safety said no |
| `FAIL_SAFE_LOCK` | IoTBridge — ESP32 went offline / sensor timeout |
| `ACTUATOR_STATE_FEEDBACK` | IoTBridge — ESP32 acknowledged a command |
| `VIDEO_ANALYSIS_COMPLETED` | `routes_video.py` — offline pipeline finished |
| `ESP32_CONNECTED` / `ESP32_OFFLINE` | IoTBridge — heartbeat messages |

### Reading events back

```python
# GET /api/events?limit=50&event_type=DOOR_LOCK_VETO
@router.get("/events")
def list_events(
    limit: int = 50,
    event_type: Optional[str] = None,
    store: EventStore = Depends(get_event_store),
):
    return store.recent(limit=limit, event_type=event_type)

# GET /api/events/summary
@router.get("/events/summary")
def event_summary(store: EventStore = Depends(get_event_store)):
    return store.summary()   # includes supabase: {synced_events, last_error, ...}
```

The summary response also shows Supabase sync health (how many events have synced,
the last error, the queue depth) — this is what the dashboard's "Event Log" tab reads.

---

## Phase 6 — The Digital Twin

**Files:** `backend/app/simulation/` (5 files), `backend/app/api/routes_twin.py`

### What problem it solves

The camera-based pipeline requires a real video file to analyze. But for testing the
safety logic under controlled, **known** conditions — "what happens when a bicycle is
exactly 3.2 m away at 4.5 m/s?" — you don't want to film that. The digital twin
runs a fully synthetic world: no YOLO, no camera, no real objects. Everything is
mathematically computed.

### The five simulation files and what each does

```
simulation/
├── ground_truth.py        GroundTruthState: the perfect, noiseless view of an object
├── object_motion.py        ObjectMotionSimulator: advances position each step
├── vehicle_physics.py      VehiclePhysics: the parked car's dimensions and door zone
├── sensor_simulator.py     VirtualRadarSimulator: injects Gaussian noise onto ground truth
└── scenario_generator.py   ScenarioGenerator: 3 built-in reproducible test cases
```

### Ground truth vs. measured

The key design is the split between **ground truth** (what is actually happening) and
**measurement** (what a noisy sensor would see). `GroundTruthState` holds the exact
position and velocity — `actual_distance_m`, `actual_ttc_seconds`. Then
`VirtualRadarSimulator.measure()` adds realistic noise before returning a
`VirtualRadarReading`:

```python
# ground_truth.py — the perfect reality
class GroundTruthState(BaseModel):
    actual_distance_m: float    # exact distance, no noise
    actual_ttc_seconds: Optional[float]
    intersects_door_zone: bool  # exact polygon check

# sensor_simulator.py — what a 77 GHz radar would actually report
def measure(self, target: GroundTruthState) -> VirtualRadarReading:
    dist_meas = max(0.1, target.actual_distance_m + random.gauss(0, self.noise_dist))
    vel_meas  = target.velocity_mps + random.gauss(0, self.noise_vel)
    snr       = max(5.0, 30.0 - 0.4 * target.actual_distance_m + random.gauss(0, 1.5))
```

This matters for benchmarking: you can compare the measured distance against the
ground truth to get a real Mean Absolute Error number for the sensor model, without
needing a physical radar or a lab.

### Object motion — straight-line kinematics

```python
# object_motion.py
class ObjectMotionSimulator:
    def step(self, dt_sec: float) -> GroundTruthState:
        self.x += self.vx * dt_sec
        self.y += self.vy * dt_sec
        distance = math.sqrt((self.x - door_x)**2 + (self.y - door_y)**2)
        intersects_door_zone = abs(self.x - door_x) < 1.4 and abs(self.y - door_y) < 1.8
        ...
```

No fancy trajectory smoothing — just Euler integration. The simplicity is intentional:
the purpose of this module is to generate known, reproducible positions for testing,
not to replicate pedestrian behavior realistically.

### The three built-in scenarios

```python
# scenario_generator.py
ScenarioGenerator.create_bicycle_overtake_scenario()
# → bicycle at x=-1.35 m (door corridor center), y=-25.0 m behind, 4.5 m/s (~16 km/h)

ScenarioGenerator.create_motorcycle_fast_approach()
# → motorcycle at x=-1.50 m, y=-45.0 m, 11.0 m/s (~40 km/h), slight lateral drift

ScenarioGenerator.create_safe_distant_traffic()
# → car at x=-3.80 m (adjacent lane, well clear), y=-30.0 m, 8.0 m/s
```

The coordinates use the same ISO 8855 frame as the real camera pipeline (Doc 1):
x is lateral (negative = driver side), y is longitudinal (negative = behind the car).

### DigitalTwinSession — the UI-facing controller

```python
# digital_twin.py
class DigitalTwinSession:
    def step(self, dt_s: float = 0.1) -> Dict:
        states = [participant.step(dt_s) for participant in self.participants]
        readings = [self.radar.measure(state).model_dump() for state in states]
        threatening = [s for s in states if s.intersects_door_zone]
        safety_state = "CRITICAL" if threatening else "SAFE"
        door_allowed = safety_state == "SAFE"
        return {
            "participants": [s.model_dump() for s in states],
            "virtual_radar": readings,
            "safety": {"state": safety_state, "door_locked": not (door_allowed and self.door_open_requested), ...},
        }
```

The dashboard calls `POST /api/twin/step` repeatedly to advance the simulation and
watches the `safety.state` field flip from SAFE to CRITICAL as the participant
approaches the door zone. `request_door_open()` lets the UI simulate a passenger
pressing the handle — the door only actually opens when `safety_state == "SAFE"`.

### Why the digital twin's safety logic is simpler than the real pipeline's

The digital twin checks `intersects_door_zone` on exact positions — no TTC thresholds,
no hysteresis, no alpha-beta filter, no projection from pixel space. It is **not** a
replacement for the real safety state machine; it is a visual demonstration tool. The
real pipeline in Doc 1 is what actually gates the physical servo.

---

## Phase 3 Complement — `CollisionDecisionRules`

**File:** `backend/app/safety_engine/decision_rules.py`

Doc 1 described the `SafetyStateMachine` — the system that holds a final SAFE/WARNING/
CRITICAL/FAIL_SAFE verdict with hysteresis. `CollisionDecisionRules` is a **separate
evaluator** that runs per-object, not per-frame, and applies deterministic rule
chains before the state machine ever sees the result.

```python
def evaluate_object(self, obj: Dict[str, Any]) -> ThreatAssessment:
    # 1. Missing coordinates → UNCERTAIN (escalate, don't assume safe)
    if x is None or y is None:
        return ThreatAssessment(threat_level=ThreatLevel.UNCERTAIN, ...)

    # 2. High measurement uncertainty on a close object → UNCERTAIN
    if y < 15.0 and uncertainty > self.max_uncertainty_m:
        return ThreatAssessment(threat_level=ThreatLevel.UNCERTAIN, is_conflict=True, ...)

    # 3. Already inside the door zone polygon → CRITICAL immediately
    if self.door_zone.contains_point(x, y, speed_kmh):
        return ThreatAssessment(threat_level=ThreatLevel.CRITICAL, ttc_seconds=0.0, ...)

    # 4. Trajectory intersection + TTC → CRITICAL or WARNING
    intersects, entry_t, _ = self.door_zone.intersects_trajectory(x, y, vx, vy, ...)
    is_conflict = intersects or obj.get("is_conflict_predicted", False)
    if is_conflict and effective_ttc < self.critical_ttc_s: ...
```

**Rule 2 is the most important safety insight here**: an object that is close (within
15m) but whose camera measurement has high uncertainty doesn't get a confident SAFE
— it gets UNCERTAIN, which the state machine treats like a soft CRITICAL. The
comment in the code explains the reasoning: a vehicle can't be close and approaching
while remaining in its own lane if the corridor check is uncertain — better to hold
the door shut and be wrong than let it open and be wrong.

The key difference from `TTCCalculator` (Doc 1): TTCCalculator computes a precise
time value using 1D interval overlap math. `CollisionDecisionRules` applies layered
thresholds and uncertainty guardrails on top of that value and the zone polygon check.
Both run; the state machine takes the worst of their outputs.

---

## Phase 7 — ML Advisory Risk Classifier

**File:** `backend/app/ml/risk_classifier.py`

### What "advisory only" means

This is a scikit-learn Random Forest. It outputs a **probability** ("there is a 73%
chance this is a dangerous situation"). It never issues an actuator command. The only
authority to command the servo is the deterministic door-zone state machine (Doc 1).

This is an explicit architectural decision documented in the code:
```python
return {
    "risk_probability": round(risk_probability, 3),
    "advisory_label": "HIGH_RISK" if risk_probability >= 0.5 else "LOW_RISK",
    "advisory_only": True,
    "safety_authority": "Deterministic door-zone state machine",   # <-- this line
}
```

A probabilistic model that sometimes outputs "78% dangerous" can't be the thing that
commands a physical door lock — a 22% chance of being wrong on a moving vehicle is
unacceptable for safety-critical actuation.

### Where the training data comes from

There is no real labeled dataset of vehicles near open doors. Instead, the classifier
is trained on **synthetic data generated at runtime**:

```python
def train(self, experiment_count: int = 300, seed: int = 42) -> Dict:
    rng = np.random.default_rng(seed)
    distance = rng.uniform(0.5, 45.0, experiment_count)
    speed    = rng.uniform(0.0, 15.0, experiment_count)
    lateral  = rng.uniform(0.1, 5.0,  experiment_count)
    ttc      = np.where(speed > 0.25, distance / speed, 99.0)

    # Synthetic label: dangerous if inside door corridor AND (ttc ≤ 3s OR already close)
    corridor  = lateral <= 1.45
    dangerous = corridor & ((ttc <= 3.0) | (distance <= 2.0))
```

The ground-truth label is derived from the **same geometry** as the deterministic
rule engine — `lateral <= 1.45 m` is the door corridor width, `ttc <= 3.0 s` matches
the `critical_ttc_threshold`. This means the ML model is trained to approximate what
the rule engine already decides deterministically. It's a demonstration that the same
decision boundary can be learned by a statistical model — not a replacement for it.

### Features the model uses

```python
FEATURES = (
    "distance_m",          # how far away
    "speed_mps",            # how fast approaching
    "lateral_offset_m",    # how aligned with the door
    "ttc_s",                # time to collision
    "radar_distance_m",    # what the radar measured (may differ from camera)
    "radar_speed_mps",      # radar velocity estimate
    "measurement_uncertainty_m",   # camera projection uncertainty
)
```

After training, `model.feature_importances_` tells you which of these the forest
found most predictive — typically `lateral_offset_m` and `ttc_s` come out on top,
which matches the intuition: the door corridor alignment matters more than raw distance.

### Training happens lazily (on first predict)

```python
_classifier = AdvisoryRiskClassifier()

def predict(self, features):
    if self.model is None:
        self.train()     # first call trains on synthetic data
    ...
```

No model file is saved to disk between server restarts — the forest is re-generated
from the same RNG seed every time, so it's reproducible but not persisted.

---

## Phase 9 — The Benchmark Suite

**File:** `backend/app/benchmarking/suite.py`

This is the "run everything and get a report" function. It:

1. Creates a `DigitalTwinSession` and steps through all 3 scenarios 100 times
2. Measures simulation step latency (mean + p95)
3. Compares `virtual_radar.measured_distance_m` against `ground_truth.actual_distance_m`
   to compute Mean Absolute Error for the simulated sensor
4. Counts **false-safe frames**: frames where the participant `intersects_door_zone`
   but the safety state was returned as SAFE — this should be 0 for a correct system
5. Trains the ML classifier on 300 synthetic samples and captures its metrics
6. Writes the full report to `backend/data/phase9_benchmark_report.json`

```python
# benchmarking/suite.py
result = {
    "simulation_step_latency_ms": {"mean": ..., "p95": ...},
    "virtual_radar_distance_mae_m": ...,   # how accurate the simulated sensor is
    "deterministic_false_safe_rate": ...,  # should always be 0.0
    "ml": ml_metrics,
    "limitations": [
        "Radar error is virtual sensor error, not a real HC-SR04 laboratory measurement.",
        "IoT round-trip latency requires a live hardware session and is not inferred here.",
    ],
}
```

The `limitations` list in the report is important: the benchmark only measures what
it can measure without real hardware. A real evaluation would also need to measure
the MQTT round-trip time (from Python `publish()` to ESP32 `servo.write()` to Python
receiving the ack on `actuator/state`) — that requires the Wokwi simulator or physical
hardware to be running.

```bash
# Triggered via dashboard "Run Benchmark" button, or directly:
# GET /api/benchmark/run?iterations=100
```

---

## The REST API Surface (remaining endpoints not covered in docs 1-2)

### Health & System Status

**File:** `backend/app/api/routes_health.py`

```
GET /api/health          Simple liveness check: {"status": "ok", "version": "1.0.0"}
GET /api/health/system   Full system snapshot: broker status, YOLO model loaded,
                         Supabase sync health, active job count, event DB size
```

### Safety Status

**File:** `backend/app/api/routes_safety.py`

```
GET /api/safety/status   Current state of the live SafetyManager instance:
                         state (SAFE/WARNING/CRITICAL/FAIL_SAFE), audit log tail,
                         handle-touch statistics (how many vetoed vs allowed)
GET /api/safety/audit    Last 200 safety state transitions with timestamps
```

### Camera Calibration

**File:** `backend/app/api/routes_calibration.py`

```
GET  /api/calibration              Current camera parameter set being used
POST /api/calibration              Update camera parameters (focal length, mount height, pitch)
GET  /api/calibration/ground-test  Test pixel-to-metric projection at known points
```

The calibration parameters (`focal_length_px`, `camera_height_m`, `pitch_deg`,
`principal_point_x/y`) are what `PinholeGroundProjector` in Doc 1 uses to convert
pixel bounding-box bottoms into metric ground coordinates. Wrong calibration = wrong
distances = wrong TTC = wrong safety decisions. The `/ground-test` endpoint lets you
verify the projection is correct before running a real video through the pipeline.

### ML endpoints

**File:** `backend/app/api/routes_ml.py`

```
POST /api/ml/train    Re-train the classifier (optional iteration count)
POST /api/ml/predict  Single prediction given the 7 feature values
GET  /api/ml/status   Whether model is trained, training metrics, feature importances
```

### Event log endpoints

**File:** `backend/app/api/routes_events.py`

```
GET /api/events?limit=50&event_type=DOOR_LOCK_VETO   Recent events, filterable by type
GET /api/events/summary    Total counts by type + Supabase sync status
```

### Digital twin endpoints

**File:** `backend/app/api/routes_twin.py`

```
POST /api/twin/reset?scenario_id=bicycle_overtake   Pick a scenario and reset
POST /api/twin/step?dt_s=0.1                        Advance by dt_s seconds
POST /api/twin/door/open                            Simulate handle press
POST /api/twin/door/close
GET  /api/twin/snapshot                             Current state without advancing
GET  /api/twin/scenarios                            List of available scenario IDs
```

---

## The Pydantic Schema Layer

**File:** `backend/app/models/schemas.py`

Every piece of data that crosses an API boundary — request bodies, response bodies,
job status objects, sample video metadata — is a Pydantic model. This gives you:
- Automatic validation (wrong type → 422, not 500)
- Auto-generated OpenAPI docs at `/docs`
- Type-checked serialization in editors

Key models:

| Model | Used for |
|---|---|
| `VideoJobInfo` | Response from `/api/video/process` and `/api/video/jobs/{id}` |
| `ProcessVideoRequest` | Request body for starting a new analysis job |
| `SampleVideo` | One entry in the `/api/video/samples` list |
| `JobStatus` | Enum: PENDING / PROCESSING / COMPLETED / FAILED |
| `BoundingBox` | Pixel coordinates of a detection box |
| `TrackPoint` | One point in a track's trajectory history |

---

## How All the Phases Connect

```
                        ┌─────────────────────────────────────────────────┐
                        │                   DOORSAFE                       │
                        │                                                   │
  Upload MP4 ──────────►│  Phase 1-2: YOLO + ByteTrack (detector.py)      │
                        │  Phase 2-3: Kinematic tracking + TTC (Doc 1)     │
                        │  Phase 3: CollisionDecisionRules (this doc)      │◄── Virtual
                        │  Phase 3: SafetyStateMachine (Doc 1)             │    Scenarios
                        │            │                                      │    (Phase 6)
                        │            ▼                                      │
                        │  Phase 4: IoTBridge → MQTT → ESP32 (Doc 2)       │
                        │            │                                      │
                        │            ▼                                      │
                        │  Phase 5: EventStore → SQLite + Supabase          │
                        │                                                   │
                        │  Phase 6: DigitalTwin (pure simulation, no YOLO)  │
                        │  Phase 7: AdvisoryRiskClassifier (never commands) │
                        │  Phase 9: BenchmarkSuite (automated report)       │
                        └─────────────────────────────────────────────────┘
```

A video upload flows through Phases 1-3 during analysis (Doc 1), then Phase 4 when
you interact with the result (Doc 2), and Phase 5 for every event along the way (this
doc). Phases 6-9 are entirely separate demo/validation tools that run without any
real video or ESP32 — they exist so the safety logic can be tested and benchmarked
in a pure software environment.
