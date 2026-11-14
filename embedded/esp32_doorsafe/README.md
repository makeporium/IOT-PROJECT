# DOORSAFE Embedded Firmware & Wokwi Circuit Simulation

> **Phase 4: Embedded ESP32 Microcontroller, Hardware PWM Servo Actuator, Capacitive Sensing & MQTT Gateway**

---

## 1. Overview

This folder contains the complete, production-grade embedded C++ firmware and Wokwi simulation circuit for the **DOORSAFE** vehicle door opening safety system.

The firmware runs directly on an **ESP32 DevKit v1** microcontroller and interfaces with:
- **SG90 Servo Motor** (GPIO 13): Drives the mechanical door latch lock pin ($0^\circ = \text{LOCKED}$, $90^\circ = \text{UNLATCHED}$).
- **Capacitive Touch Sensor** (GPIO 4): Intercepts passenger finger contact on the outer/inner door handle.
- **HC-SR04 Ultrasonic / mmWave Radar Sensor** (TRIG: GPIO 5, ECHO: GPIO 18): Measures physical distance behind the vehicle.
- **Piezoelectric Buzzer** (GPIO 14): Sounds an acoustic $2\text{ kHz}$ urgent chime when unlatching is attempted under critical threat.
- **RGB Status LED** (GPIO 25, 26, 27):
  - 🟢 **Green**: `SAFE` / Unlatch permitted ($90^\circ$).
  - 🔴 **Red**: `CRITICAL` / Physical veto lock engaged ($0^\circ$).
  - 🔵 **Blue**: WiFi / MQTT connecting or standby.

---

## 2. Hardware Wiring Diagram & Pin Map

| Component | Pin Function | ESP32 GPIO Pin | Electrical Spec |
| :--- | :--- | :---: | :--- |
| **SG90 Servo** | PWM Signal | **GPIO 13** | $50\text{ Hz}$ PWM, $500\mu s - 2400\mu s$ pulse width |
| **SG90 Servo** | VCC / GND | **5V / GND** | External $5\text{V}$ power rail |
| **Capacitive Touch** | Signal Out | **GPIO 4** | Internal `INPUT_PULLUP`, active LOW on touch |
| **Piezo Buzzer** | Positive (+) | **GPIO 14** | $3.3\text{V}$ logic, PWM tone generator |
| **RGB LED (Red)** | Anode | **GPIO 25** | $330\ \Omega$ current limiting resistor |
| **RGB LED (Green)**| Anode | **GPIO 26** | $330\ \Omega$ current limiting resistor |
| **RGB LED (Blue)** | Anode | **GPIO 27** | $330\ \Omega$ current limiting resistor |
| **HC-SR04 Radar** | Trigger | **GPIO 5** | $10\mu s$ trigger pulse |
| **HC-SR04 Radar** | Echo | **GPIO 18** | $3.3\text{V}$ tolerant logic level |

---

## 3. How to Run in Wokwi Simulation

You can run this exact simulation online with zero physical hardware:

1. Open [Wokwi ESP32 Simulator](https://wokwi.com/).
2. Select **ESP32**, or use the [Wokwi VS Code Extension](https://marketplace.visualstudio.com/items?itemName=Wokwi.wokwi-vscode).
3. Load the files:
   - `diagram.json` (defines the components and wire routes).
   - `src/main.cpp` (the C++ firmware code).
4. Click **▶ Start Simulation**:
   - The ESP32 will boot and connect to `Wokwi-GUEST` virtual WiFi.
   - It will connect to the MQTT broker at `broker.emqx.io:1883`.
   - The servo will smoothly initialize to $90^\circ$ (Green LED).
   - Click the **Capacitive Touch Handle** button: watch it immediately publish a touch event to `doorsafe/sensor/touch`.
   - When the backend detects a hazard, watch the servo snap to $0^\circ$ (Red LED + warning chime)!

---

## 4. MQTT Automotive Communication Protocol

The ESP32 communicates with the DOORSAFE FastAPI backend using lightweight JSON packets over the following topics:

### 1. Actuator Command (`doorsafe/actuator/command`) - *ESP32 Listens*
```json
{
  "timestamp": 1794695000.12,
  "command": "LOCK_ENGAGED",
  "servo_angle_deg": 0,
  "door_locked": true,
  "chime_alert": true,
  "reason": "Approaching bicycle in corridor (TTC: 0.8s)"
}
```

### 2. Capacitive Touch Event (`doorsafe/sensor/touch`) - *ESP32 Publishes*
```json
{
  "device_id": "esp32_driver_door",
  "timestamp": 12.45,
  "touched": true,
  "raw_capacitive_val": 15
}
```

### 3. Radar Distance Telemetry (`doorsafe/sensor/radar`) - *ESP32 Publishes*
```json
{
  "device_id": "esp32_rear_radar",
  "timestamp": 12.50,
  "distance_cm": 142.5,
  "distance_m": 1.42
}
```

### 4. Controller Heartbeat (`doorsafe/system/heartbeat`) - *ESP32 Publishes every 10s*
```json
{
  "device_id": "esp32_driver_door",
  "status": "ONLINE",
  "uptime_seconds": 120,
  "free_heap_bytes": 284560,
  "firmware_version": "1.0.0"
}
```
