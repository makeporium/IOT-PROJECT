# Phase 3 Explained: Deterministic Safety Decision Engine & Actuator Veto

> **A Senior Developer's Guide to Automotive Fail-Safe Architecture, Door-Zone Geometry, and Mechanical Latch Veto**
> 
> *Written in plain language for engineers, evaluators, and reviewers.*

---

## 1. What Problem Did Phase 3 Solve?

In Phase 1 and Phase 2, our system gained eyes and physics:
- **Phase 1**: YOLO detects the road user; ByteTrack tracks their identity.
- **Phase 2**: Inverse perspective geometry measures $(X, Y)$ in real meters, Alpha-Beta filtering estimates velocity $(\text{km/h})$, and trajectory math calculates Time-To-Collision ($\text{TTC}$).

However, **perception alone does not prevent an accident.** 
Knowing a bicycle is 4 meters away is meaningless if the vehicle's physical door handle can still be pulled open.

Phase 3 is the **automotive brain and safety governor**. It translates perception metrics into physical, mechanical control:
1. It defines the exact **2D danger envelope** where the vehicle's driver-side door swings open.
2. It runs an **unconditional deterministic rule engine** to classify road threats (`SAFE`, `WARNING`, `CRITICAL`, `FAIL_SAFE`).
3. It controls a **virtual SG90 servo actuator** representing the physical door lock mechanism ($0^\circ = \text{LOCKED}$, $90^\circ = \text{UNLATCHED}$).
4. It intercepts passenger capacitive touch requests and enforces an **absolute safety veto**.

---

## 2. Core Concepts Explained in Simple Terms

### 2.1 The Golden Rule: The Deterministic Veto Principle

> **"Machine learning provides advice; deterministic code makes the safety decision."**

In real automotive engineering (ISO 26262 Functional Safety standards):
- A neural network or machine learning classifier is **probabilistic** (it outputs probabilities, e.g. "93% bicycle"). Under heavy rain, headlight glare, or partial occlusion, neural networks can flicker or fail for several frames.
- A physical door lock cannot rely on probabilistic guesswork. If a passenger pulls the door handle while a cyclist is speeding past at $25\text{ km/h}$, a single missed frame would result in a catastrophic crash ("dooring").

Therefore, DOORSAFE enforces an **absolute separation of concerns**:
- Computer Vision and ML models provide continuous sensory feed.
- The **Deterministic State Machine** holds unconditional, non-negotiable **veto power** over the physical latch actuator. If the math calculates an obstacle entering the door zone within the critical threshold ($< 2.0\text{ s}$), the latch is locked ($0^\circ$)—no AI prediction or user override can force it open.

---

### 2.2 Door Danger Zone Geometry (The Swing Corridor)

A vehicle's door does not open in empty space; it swings through a defined physical polygon on the road asphalt:

```
            +Y (Ahead of Vehicle)
                     │
     ┌───────────────┼───────────────┐
     │  FRONT HOOD   │               │
     ├───────────────┼───────────────┤
     │  DRIVER CABIN │               │
-X ──┼───┐           │               ├── +X (Passenger Side)
     │   │ DOOR ZONE │ VEHICLE BODY  │
     │   │ [-2.20m to│ (Width ~1.8m) │
     │   │  -0.70m]  │               │
     └───┴───────────┼───────────────┘
     │               │  REAR TRUNK   │
     └───────────────┼───────────────┘
                     │
            -Y (Road Approach Direction)
```

In standard automotive coordinates (ISO 8855):
- **Lateral Left ($X$)**: The car's driver flank sits at $X = -0.70\text{ m}$. When the front door swings fully open to $65^\circ$, its outer edge extends to $X = -1.90\text{ m}$. Adding a $0.30\text{ m}$ pedestrian/handlebar clearance buffer yields a danger boundary of **$X \in [-2.20\text{ m}, -0.70\text{ m}]$**.
- **Longitudinal ($Y$)**: The front door spans between $Y = -0.80\text{ m}$ (behind the door panel) and $Y = 2.20\text{ m}$ (forward hinge at A-pillar).

#### Dynamic High-Speed Expansion
When a road user approaches at higher speed (e.g. a scooter at $30\text{ km/h}$ vs a walking pedestrian at $4\text{ km/h}$), aerodynamic turbulence and human reaction latency require a larger safety buffer:
$$\text{Expansion Margin} = \max(0.0, \text{Speed}_{\text{km/h}} \times 0.05\text{ m})$$
At $30\text{ km/h}$, the danger corridor automatically expands by an extra $1.5\text{ meters}$ backward along the closing lane.

---

### 2.3 Trajectory Corridor Intersection Window $[t_{\text{enter}}, t_{\text{exit}}]$

An object does not have to be inside the door zone right now to be dangerous. We must calculate **where it will be in the near future**.

Using the smoothed velocity vector $(V_x, V_y)$ from Phase 2, we project the road user's position forward in time:
$$X(t) = X_0 + V_x \cdot t, \quad Y(t) = Y_0 + V_y \cdot t$$

The state machine steps through time $t \in [0.0\text{ s}, 3.0\text{ s}]$ in increments of $\Delta t = 0.05\text{ s}$:
- $t_{\text{enter}}$: The first timestamp when $(X(t), Y(t))$ penetrates the door polygon.
- $t_{\text{exit}}$: The timestamp when the road user exits the door polygon.

If $t_{\text{enter}} < 2.0\text{ seconds}$, an imminent collision is guaranteed if the door is opened.

---

### 2.4 Measurement Uncertainty & Fail-Safe Guardrail

What happens if an object is heavily blurred, far away, or the ground projection has high variance?

In Phase 2, every measurement calculates an estimated uncertainty margin $\sigma_Y$ (e.g. $\pm 0.4\text{ m}$ when close, $\pm 5.0\text{ m}$ when distant):
$$\text{Guardrail Rule: } \text{If } Y < 15.0\text{ m} \text{ and } (\sigma_Y > 3.5\text{ m} \text{ or Confidence} < 35\%), \text{ trigger } \mathbf{FAIL\_SAFE}$$

**Automotive Philosophy**:
A false lock causes a momentary 2-second delay for the passenger. A false unlock into the path of an unmeasured motorcycle causes severe trauma. The system is programmed to **fail safe**—uncertainty automatically escalates to lock engaged.

---

### 2.5 Anti-Chatter Release Hysteresis (Hold Duration)

In physical systems, noisy detections cause a dangerous phenomenon called **relay chatter**:
- Frame 100: Cyclist detected $\to$ Door LOCKS ($0^\circ$).
- Frame 101: Cyclist is briefly occluded behind the car's C-pillar $\to$ No detection!
- If the system reacted instantly, Frame 101 would immediately UNLOCK the door ($90^\circ$). The passenger pulls the handle, the door pops open, and the cyclist crashes into it on Frame 102.

To eliminate this:
- **Locking is Instantaneous ($0\text{ ms}$)**: As soon as a threat is detected, the transition to `CRITICAL` occurs in the very first frame.
- **Unlocking Requires a Hysteresis Hold ($1.0\text{ s}$)**: Once `CRITICAL` is engaged, the state machine starts a countdown timer. Even if all detections vanish, the door latch remains locked for at least **1.0 second** of continuous clear corridor before releasing to `SAFE`.

---

## 3. The 4 Safety States & Actuator Responses

| Safety State | Condition | Servo Angle | Latch State | Acoustic / Visual Alert |
| :--- | :--- | :---: | :---: | :--- |
| **SAFE** | Corridor clear, all objects $>4.0\text{ s}$ away or in outside lanes | $90^\circ$ | **UNLATCHED** | None. Passenger can freely push/pull door open. |
| **WARNING** | Obstacle closing with $\text{TTC} \in [2.0\text{ s}, 4.0\text{ s}]$ or passing parallel within $1.0\text{ m}$ | $90^\circ$ | **UNLATCHED** | Low-pitch chime alert. Door remains mechanically free, but passenger is alerted. |
| **CRITICAL** | Obstacle inside corridor or projected to enter with $\text{TTC} < 2.0\text{ s}$ | $0^\circ$ | **LOCKED (VETO)** | High-frequency warning chime + red warning display. Mechanical latch held locked. |
| **FAIL_SAFE** | Sensor timeout ($>500\text{ ms}$ without telemetry) or high measurement uncertainty | $0^\circ$ | **LOCKED (VETO)** | Continuous safety warning. Defaults to safe locked position. |

---

## 4. Capacitive Door Handle Simulation

In modern vehicles, capacitive sensors inside the door handle grip detect when human fingers make contact:
1. **Passenger touches handle**: The frontend or ESP32 fires an event to `POST /api/safety/handle-touch`.
2. **Deterministic Evaluation**:
   - If state is `SAFE` $\to$ Returns `action_status: UNLATCHED`, Servo holds at $90^\circ$.
   - If state is `CRITICAL` $\to$ Returns `action_status: BLOCKED_BY_SAFETY_VETO`, Servo stays locked at $0^\circ$, `lock_veto_active: true`, and warning chime triggers.
   - If state is `FAIL_SAFE` $\to$ Returns `action_status: BLOCKED_BY_FAIL_SAFE`.

---

## 5. Software Architecture & File Overview

```
backend/app/
├── safety_engine/
│   ├── zone_geometry.py       # 2D DoorZonePolygon, dynamic bounds, trajectory intersection
│   ├── decision_rules.py      # CollisionDecisionRules, ThreatAssessment, uncertainty guardrails
│   ├── state_machine.py       # SafetyStateMachine, hysteresis timer, handle-touch veto logic
│   └── manager.py             # SafetyManager singleton, thread safety, audit log buffer
├── api/
│   └── routes_safety.py       # REST endpoints: /status, /handle-touch, /evaluate, /reset
└── static/
    └── index.html             # Interactive UI: Door Safety & Actuator Console, live telemetry sync
```

---

## 6. How to Verify Phase 3

### 6.1 Automated Unit Tests (100% Pass)
Run the automated test suite verifying geometry, trajectory extrapolation, decision logic, uncertainty guardrails, hysteresis, and REST APIs:
```powershell
$env:PYTHONPATH = ".;backend"
& ".\YOLO MODEL\venv\Scripts\pytest.exe" backend/tests/test_safety.py -v
```

### 6.2 Interactive Dashboard Verification
1. Start the backend: `python -m uvicorn backend.main:app --port 8000`.
2. Open `http://localhost:8000` in your browser.
3. Observe the **Door Safety & Actuator Console** in the left sidebar:
   - Play a video where a cyclist passes the parked vehicle.
   - Watch the state badge change in real time: `SAFE` $\to$ `CRITICAL (VETO ENGAGED)`.
   - Watch the actuator gauge physically transition from $90^\circ$ to $0^\circ$.
   - Click **✋ Touch Door Handle (Capacitive Trigger)** during the cyclist's approach: verify that the system pops up the red alert: `⛔ DOOR UNLATCH VETO ACTIVE! (Servo Locked at 0°)`.
   - Watch the cyclist exit: verify the 1.0s hysteresis hold keeps the latch locked until the path is strictly clear, then smoothly returns to $90^\circ$.
