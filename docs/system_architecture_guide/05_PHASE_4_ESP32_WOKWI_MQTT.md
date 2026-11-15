# Phase 4 Explained: IoT Controller, ESP32, Wokwi & MQTT Actuation

> **Engineering guide to ESP32 firmware, hardware PWM servos, and MQTT telemetry**
> 
> *Written in plain language for engineers, evaluators, and reviewers.*

---

## 1. What Problem Did Phase 4 Solve?

In Phases 1 through 3, our system lived entirely inside the computer's memory:
- **Phase 1**: YOLO and ByteTrack see the road users.
- **Phase 2**: Camera geometry converts pixels to meters, velocity, and Time-To-Collision ($\text{TTC}$).
- **Phase 3**: The deterministic safety engine decides whether to lock or unlock the door.

However, **a car is not a laptop**. In a real automobile:
1. The camera and AI inference run on an **Automotive Compute Gateway** (the AI central processor).
2. The door lock, handle sensor, warning buzzer, and door latch live inside an **Electronic Control Unit (ECU)** embedded inside the vehicle's door panel.
3. The central computer and the door ECU must communicate over a **fast, decoupled automotive telemetry bus**.

**Phase 4 brings the embedded hardware to life:**
- We build academic prototype C++ firmware for the **ESP32 DevKit v1** microcontroller.
- We integrate a full **MQTT Automotive Telemetry Gateway** connecting Python and embedded C++.
- We simulate the entire physical circuit in **Wokwi** (with an SG90 servo actuator, capacitive touch handle, piezo buzzer, RGB LED, and ultrasonic radar).
- When a hazard approaches, the backend dispatches a sequenced command, the ESP32 rotates the servo to $0^\circ$ (**LOCK ENGAGED**), and the controller publishes the applied position as an acknowledgement.

---

## 2. Why MQTT? The Automotive Telemetry Protocol

Beginners often ask: *“Why not just use regular HTTP REST calls between the ESP32 and the backend?”*

In automotive embedded systems, standard HTTP has major fatal flaws:
1. **High Overhead**: An HTTP request includes hundreds of bytes of headers (`User-Agent`, `Accept`, cookies) for a 10-byte sensor reading.
2. **Polling Waste**: For an ESP32 to receive door lock commands via HTTP, it would have to poll `GET /lock-status` 20 times per second. This saturates the micro-controller's WiFi chip and creates unacceptable latency.
3. **Connection State Ignorance**: If the WiFi wire snaps or interference cuts out, HTTP doesn't know until the next request fails.

### Why MQTT Wins:
- **Publish / Subscribe Decoupling**: The backend publishes a message once to `doorsafe/actuator/command`. Any number of door ECUs (Driver, Passenger, Rear Doors) receive it simultaneously in milliseconds without polling.
- **Microscopic Packets**: MQTT headers are as small as **2 bytes**, making it ultra-fast and lightweight.
- **Last Will and Testament (LWT)**: When the ESP32 connects to the MQTT broker, it registers a "death certificate" (`{"status": "OFFLINE"}`). If the ESP32 loses power or signal, the broker *automatically* broadcasts this to the backend immediately. This triggers our Phase 3 **FAIL_SAFE** mode to keep the door safely locked.

---

## 3. MQTT Topic Hierarchy & Message Payloads

```
                       MQTT Broker (broker.emqx.io:1883)
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            ▼                         ▼                         ▼
   .../actuator/command        .../sensor/touch        .../system/heartbeat
   (Backend -> ESP32)          (ESP32 -> Backend)      (Bidirectional Health)
   Servo: 0° or 90°            Touched: true/false     Status: ONLINE / OFFLINE
```

All topics use the configurable project namespace `doorsafe/college_ayush/v1` by default.

### 3.1 Actuator Command (`.../actuator/command`)
Published by the backend safety engine; consumed by the ESP32:
```json
{
  "timestamp": 1794695000.12,
  "command": "LOCK_ENGAGED",
  "servo_angle_deg": 0,
  "door_locked": true,
  "chime_alert": true,
  "reason": "Imminent cyclist collision (TTC: 0.8s)",
  "command_id": "7a1d...",
  "session_id": "backend-boot-session...",
  "sequence": 42,
  "ttl_ms": 1500,
  "source_mode": "RECORDED_DEMO"
}
```

### 3.2 Actuator Acknowledgement (`.../actuator/state`)
```json
{
  "device_id": "esp32_driver_door",
  "timestamp": 120.4,
  "command_id": "7a1d...",
  "session_id": "backend-boot-session...",
  "sequence": 42,
  "requested_angle_deg": 0,
  "applied_angle_deg": 0,
  "door_locked": true,
  "status": "APPLIED"
}
```

### 3.3 Capacitive Handle Touch (`.../sensor/touch`)
Published by the ESP32 whenever a passenger touches the door handle:
```json
{
  "device_id": "esp32_driver_door",
  "timestamp": 1794695000.15,
  "touched": true,
  "raw_capacitive_val": 15
}
```

### 3.4 Rear Radar Echo (`.../sensor/radar`)
Published by the ESP32 rear sensor module:
```json
{
  "device_id": "esp32_rear_radar",
  "timestamp": 1794695000.50,
  "distance_cm": 142.5,
  "distance_m": 1.42,
  "relative_speed_mps": -3.2
}
```

### 3.5 Node Heartbeat & Watchdog (`.../system/heartbeat`)
Emitted periodically every 10 seconds to prove controller vitality:
```json
{
  "device_id": "esp32_driver_door",
  "status": "ONLINE",
  "uptime_seconds": 120,
  "free_heap_bytes": 284560,
  "firmware_version": "1.0.0"
}
```

---

## 4. Hardware Circuit & Pin Configuration

The circuit is designed for the **ESP32 DevKit v1** and simulated inside Wokwi:

```
                  ┌─────────────────────────────────────────┐
                  │              ESP32 DevKit v1            │
                  │                                         │
     SG90 PWM ────┤ GPIO 13                             5V  ├─── Servo & Radar VCC
  Touch Handle ───┤ GPIO 4                             GND  ├─── Common Ground
  Piezo Buzzer ───┤ GPIO 14                                 │
   LED (Red)   ───┤ GPIO 25                             3V3 ├─── Logic High
   LED (Green) ───┤ GPIO 26                                 │
   LED (Blue)  ───┤ GPIO 27                        GPIO 5   ├─── HC-SR04 TRIG
                  │                                GPIO 18  ├─── HC-SR04 ECHO
                  └─────────────────────────────────────────┘
```

| Component | Pin Function | ESP32 GPIO | Operating Principle |
| :--- | :--- | :---: | :--- |
| **SG90 Servo** | PWM Actuator | **GPIO 13** | $50\text{ Hz}$ PWM ($500\mu s$ to $2400\mu s$ pulses). $0^\circ = \text{Locked}$, $90^\circ = \text{Unlatched}$. |
| **Touch Button** | Capacitive Grip | **GPIO 4** | Internal `INPUT_PULLUP`. Pulls to GND when touched. |
| **Piezo Buzzer** | Acoustic Chime | **GPIO 14** | $2.0\text{ kHz}$ pulsed tone for safety alert. |
| **RGB LED (Red)** | Hazard Indicator | **GPIO 25** | Illuminates bright RED when latch is locked. |
| **RGB LED (Green)**| Safety Indicator| **GPIO 26** | Illuminates bright GREEN when corridor is clear. |
| **RGB LED (Blue)** | Network Indicator| **GPIO 27** | Illuminates BLUE during WiFi/MQTT handshake. |
| **HC-SR04 Radar** | Ultrasonic Ranging| **GPIO 5, 18** | Measures distance using time-of-flight sound pulses. |

---

## 5. Senior Developer Firmware Design Principles

### 5.1 Bounded Runtime Execution
The steady-state control loop uses **timestamp-based polling (`millis()`)** for buzzer, radar, and heartbeat scheduling. The ultrasonic read remains bounded by a 35 ms timeout, and connection setup uses bounded retries. This is suitable for the academic simulator but is not an automotive production timing guarantee.
- The MQTT network loop runs continuously on every cycle.
- Buzzer durations (600ms), radar polling (500ms), and heartbeats (10s) operate as independent, non-blocking scheduled tasks.

### 5.2 Direct Hardware PWM Actuation
When an MQTT packet arrives on the project-scoped actuator command topic, the callback executes immediately:
```cpp
void onMqttMessageReceived(char* topic, byte* payload, unsigned int length) {
    // Zero-latency physical actuation
    doorServo.write(targetAngle);
    if (lockEngaged) {
        setStatusLed(true, false, false); // RED
        tone(PIN_BUZZER, 2000);           // 2kHz Chime
    }
}
```
The firmware applies commands in the MQTT callback and publishes the resulting state. Phase 9 will measure actual network, controller, and mechanical latency.

### 5.3 Fail-Safe and Stale-Command Behavior
- The ESP32 boots at the locked $0^\circ$ position.
- Loss of MQTT for more than two seconds returns the servo to locked.
- Commands carry monotonic sequence numbers; duplicate or older commands are rejected.
- Touch events are edge events and are never retained by the broker.
- Playback, radar, and handle requests update one authoritative backend safety manager.

### 5.4 Animation and Model Ownership
- Phase 4 owns the compact hardware-twin animation: controller status, touch state, radar value, and SG90 position.
- This animation lives in the frontend presentation layer and consumes backend state plus `actuator/state` feedback.
- Phase 6 owns the complete Three.js vehicle, road-user models, opening-door animation, danger zone, and sensor fields of view.

---

## 6. Directory Structure Overview

```
embedded/esp32_doorsafe/
├── diagram.json          # Wokwi visual circuit layout and wiring
├── wokwi.toml            # Wokwi simulator configuration
├── platformio.ini        # PlatformIO dependency and board config
├── README.md             # Embedded quick-start guide
└── src/
    └── main.cpp          # Complete C++ firmware code
backend/app/iot/
├── topics.py             # MQTT topic hierarchy and Pydantic message schemas
├── mqtt_client.py        # Resilient Paho MQTT gateway client with ring buffer
├── bridge.py             # Bi-directional IoT bridge linking Safety Engine to MQTT
└── __init__.py           # Package exports
backend/app/api/
└── routes_iot.py         # REST endpoints: /status, /simulate-touch, /simulate-radar
```

---

## 7. How to Verify & Test Phase 4

### 7.1 Automated Unit Tests (100% Pass)
Run the automated test suite verifying serialization, gateway publishing, fail-safe latch veto, and roundtrip timing:
```powershell
$env:PYTHONPATH = ".;backend"
& ".\YOLO MODEL\venv\Scripts\pytest.exe" backend/tests/test_iot_mqtt.py -v
```

### 7.2 Interactive Dashboard Verification
1. Start the backend: `python -m uvicorn backend.main:app --port 8000`.
2. Open `http://localhost:8000` in your browser.
3. Observe the top bar: verify that the **MQTT badge** turns green: `● MQTT: broker.emqx.io (ONLINE)`.
4. Scroll to the **IoT Controller, ESP32 Hardware & MQTT Automotive Bus** console:
   - Notice the live packet counter and auto-updating log terminal.
   - Click **✋ Simulate ESP32 Capacitive Touch (MQTT)**: watch the live packet appear in the log:
     - `▶ OUT doorsafe/sensor/touch: {"touched": true}`
     - `◀ IN doorsafe/actuator/command: {"servo_angle_deg": 0, "door_locked": true}`
   - Click **📡 Simulate Rear Radar Echo (1.4m)**: verify radar telemetry packet is broadcast.
