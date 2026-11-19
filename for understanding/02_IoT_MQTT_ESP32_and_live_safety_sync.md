# DOORSAFE — IoT, MQTT, ESP32 & Live Safety Sync: Complete Explanation

The first document (`01_YOLO_and_perception_pipeline.md`) covered how a video frame
becomes a SAFE/WARNING/CRITICAL verdict. This document covers what happens **after**
that verdict — how it reaches a physical (or simulated) servo motor, how the dashboard
stays in sync with it, and why those two things can fall out of sync if something
breaks silently.

---

## The Big Picture

```
Camera frame → [Doc 1's pipeline] → SafetyFrameResult (state, servo angle, is_locked)
                                              │
                      ┌───────────────────────┼────────────────────────┐
                      ▼                       ▼                        ▼
              IoTBridge.sync_safety_state   Dashboard HUD badge    Flight recorder
                      │                    (reads the SAME frame     (event log)
                      ▼                     data independently,
            MQTT "actuator/command"          Phase 5)
                      │
                      ▼
            ESP32 (real hardware or
             Wokwi simulation) — moves
             the SG90 servo, lights an
             LED, sounds a buzzer
                      │
                      ▼
            MQTT "actuator/state" (feedback: "I applied 0°, door locked")
                      │
                      ▼
            Dashboard polls /api/iot/status and shows ESP32/broker health
```

There are **two independent consumers** of the safety verdict: the physical actuator
(via MQTT) and the dashboard's own HUD badge (reading the same frame data directly in
the browser). They are *supposed* to always agree because they start from the same
source — but they travel through completely different code paths to get there, which
is exactly where desync bugs come from.

---

## Why MQTT? What MQTT actually is

MQTT (Message Queuing Telemetry Transport) is a lightweight publish/subscribe protocol
built for exactly this situation: small, unreliable devices (microcontrollers) talking
to a backend over possibly-flaky networks.

- A **broker** (here: the free public `broker.emqx.io:1883`) is the mail room —
  everyone connects to it, nobody connects directly to each other.
- A **publisher** sends a message to a **topic** (just a string, like a mailbox address).
- A **subscriber** registers for a topic and gets every message sent to it.

Neither side needs to know the other's IP address or even be online at the same time —
that decoupling is exactly what lets a browser-based backend and a Wokwi-simulated
ESP32 talk to each other across the internet with almost no setup.

### The topics this project uses

**File:** `backend/app/iot/topics.py`

```python
class MQTTTopics(str, Enum):
    HAZARD_TELEMETRY  = f"{TOPIC_PREFIX}/telemetry/hazard"    # backend -> anyone listening
    ACTUATOR_COMMAND  = f"{TOPIC_PREFIX}/actuator/command"    # backend -> ESP32
    ACTUATOR_STATE    = f"{TOPIC_PREFIX}/actuator/state"      # ESP32 -> backend (ack)
    SENSOR_TOUCH       = f"{TOPIC_PREFIX}/sensor/touch"        # ESP32 -> backend
    SENSOR_RADAR        = f"{TOPIC_PREFIX}/sensor/radar"        # ESP32 -> backend
    SYSTEM_HEARTBEAT   = f"{TOPIC_PREFIX}/system/heartbeat"    # either direction
```

`TOPIC_PREFIX` is something like `doorsafe/ayush/vehicle_door_demo` — a namespace so
this project's messages don't collide with anyone else's on the shared public broker.

Every payload on every topic is a **Pydantic model**, not a loose dict — e.g.
`ActuatorCommandPayload` requires `command` to match one of exactly four strings
(`LOCK_ENGAGED|UNLOCK_PERMITTED|FAIL_SAFE_LOCK|HAPTIC_WARNING`) and `servo_angle_deg`
to be between 0 and 90. A malformed message is rejected before it's ever sent or acted
on — this is what "deterministic automotive protocol" buys you over "just send JSON".

---

## File Map — The IoT Layer

```
backend/app/iot/
├── topics.py          MQTT topic names + Pydantic payload schemas (shown above)
├── mqtt_client.py      DoorSafeMQTTClient: the actual paho-mqtt connection wrapper
└── bridge.py           IoTBridge: the brain that decides what to publish and when

backend/app/safety_engine/
└── manager.py          SafetyManager: wraps the Doc-1 state machine with audit
                         logging and handle-touch orchestration (used by both the
                         live IoT path AND the dashboard's handle-touch test button)

backend/app/api/
└── routes_iot.py        REST endpoints the dashboard calls: /status, /simulate-touch,
                         /simulate-radar, /sync-observation, /manual-actuator

backend/app/t.ino        The actual ESP32 firmware (Arduino/C++) — runs for real on
                         Wokwi's simulator, or on a physical ESP32 board
```

---

## Step 1 — `DoorSafeMQTTClient`: The Connection

**File:** `backend/app/iot/mqtt_client.py`

This wraps the `paho-mqtt` Python library. Three things worth understanding:

### Connecting without blocking

```python
def start(self):
    self._client.connect_async(self.broker_host, self.broker_port, self.keepalive)
    self._client.loop_start()
```

`connect_async` + `loop_start()` run the MQTT network loop on a **background thread**.
If the broker is unreachable, this never blocks the FastAPI server — `is_connected`
just stays `False` and every `publish()` call becomes a no-op that returns `False`.
This is why the dashboard can show "BROKER: OFFLINE" and the rest of the app still
works fine — MQTT is additive, not load-bearing for the core safety logic.

### Last Will and Testament (LWT)

```python
self._client.will_set(
    topic=MQTTTopics.SYSTEM_HEARTBEAT.value,
    payload=HeartbeatPayload(device_id=self.client_id, status="OFFLINE", ...).model_dump_json(),
    qos=1,
)
```

This is a broker feature: "if this client disconnects **without saying goodbye**
(crash, network drop), publish this message on my behalf." It's how other clients find
out a node died, without needing a timeout/heartbeat-miss check themselves.

### The message audit ring buffer

```python
self.recent_messages: deque = deque(maxlen=50)
```

Every message in or out gets appended here. `deque(maxlen=50)` auto-discards the
oldest entry once full — a fixed-size rolling log with no manual cleanup needed. This
is what powers the MQTT terminal viewer in the "Virtual ESP32 Hardware" tab.

---

## Step 2 — `IoTBridge`: The Decision Layer

**File:** `backend/app/iot/bridge.py`

This is the most important file in this document. It sits between the safety engine
(Doc 1's state machine) and MQTT, and encodes a lot of real automotive-safety
reasoning as code.

### Rule: never autonomously unlock

```python
# 1. Publish a physical veto only when video safety requires a lock.
if not frame_result.is_door_locked:
    self._last_safety_command_signature = None
else:
    signature = (frame_result.actuator_command.value, frame_result.servo_angle_deg, frame_result.is_door_locked)
    if signature != self._last_safety_command_signature:
        self._publish_actuator_command(..., door_locked=True, ...)
        self._last_safety_command_signature = signature
```

Read this carefully: **a SAFE frame publishes nothing.** The ESP32 is never told
"you may unlock now" just because a frame looks clear — a command is only ever sent
when the system needs to **engage** a lock. Unlocking only happens in response to a
deliberate handle-touch event (next section). This is a deliberate safety choice: the
camera being wrong about "safe" is far less dangerous than the camera being wrong
about "go ahead and open."

The `signature` dedup check also prevents **command spam** — without it, every single
video frame while CRITICAL would re-publish the same lock command dozens of times a
second.

### Rule: a fresh ultrasonic echo overrides the camera

```python
RADAR_INTERLOCK_DISTANCE_M = 1.20
RADAR_FRESHNESS_S = 2.0

def _radar_interlock_active(self) -> bool:
    if not self.last_radar_reading:
        return False
    age_s = time.time() - self.last_radar_reading.get("received_at", 0.0)
    return age_s <= RADAR_FRESHNESS_S and bool(self.last_radar_reading.get("interlock_active"))
```

The ESP32's physical HC-SR04 ultrasonic sensor (an actual distance sensor, not a
camera) measures real-world distance directly at the door. If something is within
1.20 m **and** that reading is less than 2 seconds old, a handle-touch request is
blocked **regardless of what the camera's trajectory analysis concluded**. This is a
second, independent layer of physical evidence — the comment in the code explains why:

```python
# The HC-SR04 has no lateral classification: it is a close-range, fixed-beam
# confirmation sensor. It therefore does not replace camera trajectory
# analysis, but blocks a handle request while a fresh object is in its beam.
```

### Handling the touch event

**File:** `backend/app/iot/bridge.py` — `_handle_incoming_touch()`

```python
if self._radar_interlock_active():
    # ultrasonic says "something is right there" -> lock, full stop
    ...
    return

handle_resp: DoorHandleResponse = self.safety.trigger_handle_touch(is_touched=touched)
command = self._publish_actuator_command(
    command=handle_resp.actuator_command.value,
    servo_angle_deg=handle_resp.servo_angle_deg,
    door_locked=handle_resp.lock_veto_active,
    ...
)
```

Order of evaluation: ultrasonic interlock first (fastest, most physically direct),
then the full camera-based state machine from Doc 1 (`trigger_handle_touch` calls into
`SafetyStateMachine.handle_touch_trigger`, which you've already seen).

### Debouncing a bouncy switch

```python
TOUCH_DEBOUNCE_S = 0.4
...
if now - self._last_touch_trigger_time < TOUCH_DEBOUNCE_S:
    return
```

A real (or simulated) pushbutton doesn't produce one clean electrical edge — it
"bounces," generating several rapid on/off transitions for a single physical press.
Without this check, one press could fire the whole lock-decision pipeline five or six
times in a few milliseconds. 400ms is comfortably longer than any realistic bounce,
short enough that a deliberate second touch still registers.

### Stale "offline" message protection

```python
STALE_LWT_GRACE_S = 6.0

if status == "OFFLINE" and self.last_esp32_heartbeat:
    last_seen = float(self.last_esp32_heartbeat.get("received_at", 0.0))
    if last_status == "ONLINE" and time.time() - last_seen <= STALE_LWT_GRACE_S:
        logger.info("Ignored stale duplicate-session OFFLINE LWT...")
        return
```

Remember the LWT from Step 1? If you reload the Wokwi simulator tab, the **old**
browser tab's connection drops and the broker fires *its* LWT — "OFFLINE" — even
though a **new** tab is reconnecting at almost the same instant and is about to prove
it's alive. Without this guard, reloading the simulator page would flash the dashboard
to "ESP32 OFFLINE" every time, even when nothing is actually wrong.

### Recovering lost commands after a reconnect

```python
if recovered_after_gap and self.last_actuator_command:
    previous = dict(self.last_actuator_command)
    self._publish_actuator_command(..., reason=f"Controller recovered; replaying: {previous['reason']}", ...)
```

MQTT with QoS 1 guarantees delivery **to currently-connected subscribers** — if the
ESP32 was disconnected when a lock command was published, it simply never arrives.
When the controller reconnects (proven by a fresh heartbeat after a gap longer than
`CONTROLLER_RECOVERY_GAP_S`), the bridge re-sends whatever the last command was, so a
lock engaged while the browser tab was backgrounded doesn't get silently lost.

---

## Step 3 — The ESP32 Firmware

**File:** `backend/app/t.ino` (Arduino/C++, runs on real hardware or Wokwi's simulator)

### Rejecting stale/out-of-order commands

```cpp
unsigned long seq = d["sequence"] | 0;
const char *sid = d["session_id"] | "";
if (lastSid != String(sid)) { lastSid = sid; lastSeq = 0; }
if (!seq || seq <= lastSeq) return;   // ignore anything not newer than the last applied command
```

Every `ActuatorCommandPayload` carries an incrementing `sequence` number scoped to a
`session_id` (the backend's current boot — see `self._session_id = str(uuid.uuid4())`
in `bridge.py`). MQTT doesn't guarantee message order across a flaky connection; if a
network hiccup delivers command #47 *after* command #48 has already been applied, the
ESP32 must ignore the late #47 — otherwise a brief network reorder could un-lock a door
that a newer command had already locked. A new `session_id` (backend restart) resets
the counter, since sequence numbers from a previous server boot are meaningless now.

### Applying a command and sounding the alarm

```cpp
void apply(int angle, bool lock, bool alert, ...) {
    locked = lock;
    servo.write(angle);
    digitalWrite(RED, lock);
    digitalWrite(GREEN, !lock);
    if (lock && alert) dangerAlarm();
    if (!lock) { unlockedAt = millis(); openChime(); }
    feedback(id, sid, seq, angle, lock ? "LOCKED" : "UNLOCKED");   // ack back over MQTT
}
```

Every applied command gets an acknowledgement published back on `actuator/state` —
this is what `IoTBridge._handle_actuator_state()` listens for, and what lets the
backend measure round-trip latency (`last_actuator_feedback["round_trip_ms"]`).

### The ultrasonic sensor read

```cpp
float distanceCm() {
    digitalWrite(TRIG, LOW); delayMicroseconds(2);
    digitalWrite(TRIG, HIGH); delayMicroseconds(10);
    digitalWrite(TRIG, LOW);
    long us = pulseIn(ECHO, HIGH, 35000);
    return us ? us * 0.0343 / 2.0 : 400.0;
}
```

This is the standard HC-SR04 protocol: send a 10-microsecond pulse on `TRIG`, then
measure how long the `ECHO` pin stays high. Sound travels at 0.0343 cm/microsecond;
divide by 2 because the pulse travels to the object **and back**. If nothing reflects
the pulse within the 35ms timeout, `pulseIn` returns 0, and the code reports 400 cm
(out of range) rather than a confusing zero-distance reading.

---

## Step 4 — How the Dashboard Talks to All This

**File:** `backend/app/api/routes_iot.py` — the REST surface the browser actually calls.

| Endpoint | What it does |
|---|---|
| `GET /api/iot/status` | Full snapshot: broker connection, ESP32 online/offline, last radar reading, last actuator feedback, current `SafetyManager` state |
| `POST /api/iot/sync-observation` | Feeds the *current video frame's* object list into the live `SafetyManager`, so the live engine's state tracks whatever frame you're watching |
| `POST /api/iot/simulate-touch` | Simulates a handle-touch event without real hardware — runs the exact same code path as a real ESP32 touch |
| `POST /api/iot/simulate-radar` | Simulates an ultrasonic reading |
| `POST /api/iot/manual-actuator` | Diagnostic LOCK/UNLOCK button — LOCK always works, UNLOCK still goes through the full safety gate |

### Why `/sync-observation` exists

While you play a recorded analysis run, the dashboard calls this endpoint roughly 4
times a second (`index.html`, throttled to every 250ms) with whatever frame is
currently on screen:

```javascript
fetch('/api/iot/sync-observation', {
  method: 'POST',
  body: JSON.stringify({ timestamp_sec: frame.timestamp_sec, objects: frame.objects, source_mode: 'RECORDED_DEMO' })
});
```

This keeps the **live** `SafetyManager` (the one driving MQTT and the handle-touch
test button) synchronized with whatever frame you're currently looking at — so if you
click "Test handle touch" while paused on a dangerous frame, the live engine evaluates
*that* frame's objects, not some stale state from minutes ago.

```python
# routes_iot.py
@router.post("/sync-observation")
def sync_observation(req: ObservationSyncRequest, bridge: IoTBridge = Depends(get_iot_bridge)):
    result = bridge.process_observations(req.objects, req.timestamp_sec, req.source_mode)
    return {"safety_state": result.current_state.value, "servo_angle_deg": result.servo_angle_deg, ...}
```

**Important failure mode from this project's own debugging history:** if the
dashboard's `fullTelemetryData` never loaded (e.g. the telemetry fetch 500'd), the
browser has no frame objects to send here — the handle-touch test button then sends an
**empty object list**, which naturally evaluates to SAFE and opens the door, even
while the burned-in video overlay shows CRITICAL. The fix applied in this project was
to refuse to run the handle-touch test at all when no telemetry is loaded, rather than
silently treating "no data" as "no threat." The general lesson: **never let "I don't
know" collapse into "it's fine."**

---

## Step 5 — `SafetyManager`: One State Machine, Two Callers

**File:** `backend/app/safety_engine/manager.py`

This wraps the Doc-1 `SafetyStateMachine` and is shared by **two different callers**:

1. The offline video pipeline (`pipeline.py`) creates its own `SafetyStateMachine`
   directly and evaluates every frame once, writing results into the telemetry JSON.
2. The live IoT path (`IoTBridge` → `SafetyManager` → the *same* `SafetyStateMachine`
   class, a separate singleton instance) evaluates frames pushed to it live via
   `/sync-observation`, in real wall-clock time, while you interact with the dashboard.

These are **two different instances of the same class**, each with their own
independent hysteresis timer. This is intentional — the one-time offline analysis
pass and your live interactive session are different experiences — but it's also
exactly why the overlay (from instance #1, baked into the video forever) and the live
dashboard state (from instance #2, evaluated only while you're actually watching) can
diverge if the sync pipeline between them breaks.

```python
# manager.py
def trigger_handle_touch(self, is_touched, objects=None):
    keep_snapshot_valid = (
        objects is None
        and self.active_frame_result is not None
        and self.active_source_mode in {"RECORDED_DEMO", "SIMULATION"}
    )
    response = self.state_machine.handle_touch_trigger(is_touched, objects, enforce_timeout=not keep_snapshot_valid)
```

This `keep_snapshot_valid` check exists because of another real bug from this
project's history: the live state machine has a 30-second sensor-timeout watchdog
(`sensor_timeout_s`) that normally fail-safe-locks the door if no fresh telemetry
arrives in time. But if you *pause* a recorded video to inspect a frame for longer
than 30 seconds, that's not a sensor failure — it's just you looking at something. This
check tells the watchdog "this is an intentional paused snapshot, not dead telemetry,"
so it doesn't falsely fail-safe-lock.

---

## Why So Many Layers?

It would be simpler to have the video pipeline directly flip a GPIO pin. The actual
design has: pipeline → telemetry JSON → dashboard JS → REST call → `IoTBridge` →
`SafetyManager` → `SafetyStateMachine` → MQTT → ESP32 firmware → physical servo, with
an acknowledgement traveling all the way back. Every one of those hops exists because
this is modeling a **real automotive architecture**, where:

- The perception system (camera + YOLO) and the actuator (door latch) are physically
  separate ECUs connected by a vehicle bus (here: MQTT standing in for CAN/LIN).
- Commands need sequence numbers and session IDs because a real vehicle bus can
  reorder or drop messages.
- A watchdog/heartbeat is needed because a real ECU can lose power or crash.
- Independent physical sensors (the ultrasonic "radar") exist because a single point
  of failure (just the camera) is not acceptable in a safety-critical door lock.

Every file in this document models one of those real-world requirements, which is why
removing any single layer would make the system less realistic, not just shorter.
