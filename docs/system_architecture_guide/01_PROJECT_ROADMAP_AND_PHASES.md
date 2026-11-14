# DOORSAFE: The Master Engineering Roadmap & Phase Guide

> **A Senior Developer's Guide to the 9 Development Milestones**
> 
> *Written in plain language for engineers, evaluators, and reviewers.*

---

## 1. Executive Overview: What Are We Building?

**DOORSAFE** is an intelligent automotive safety system designed to prevent "dooring" accidents. A dooring accident happens when someone inside a parked car swings the door open directly into the path of an oncoming cyclist, motorcyclist, or pedestrian.

In a commercial vehicle (like Audi's Exit Warning or Hyundai's Safe Exit Assist), this requires an unbroken chain of real technologies:
1. **Eyes (Perception)**: Cameras and radar watch the road behind the vehicle.
2. **Brain (Motion & Geometry)**: Calculates physical distances, velocities, and predicted paths in meters and seconds.
3. **Judge (Decision Engine)**: Determines if an approaching object will strike the door swing area.
4. **Muscle (IoT & Actuators)**: An ESP32 microcontroller senses the passenger touching the door handle and physically commands a servo lock to keep the door shut until danger clears.
5. **Memory & Control Center (Cloud & Dashboard)**: Logs all telemetry to a database and visualizes the car in a 3D digital twin.

To build this without getting lost in complexity, we divide the entire engineering journey into **9 distinct, progressive phases**.

---

## 2. Summary: How Many Phases Are There?

There are **9 Total Phases**. 

Here is the master roadmap at a glance:

| Phase | Milestone Name | Status | Plain English Goal |
| :---: | :--- | :---: | :--- |
| **Phase 1** | **Computer Vision & Tracking** | **COMPLETED** | Teach the computer to see road participants (bikes, cars, people) in raw video, assign persistent IDs, and trace their movement paths on screen. |
| **Phase 2** | **Motion, Geometry & TTC** | *Next Up* | Convert 2D pixel pixels into real-world meters: calculate how far away objects are, how fast they are moving, and calculate Time-to-Collision (TTC). |
| **Phase 3** | **Door-Zone Collision Engine** | *Upcoming* | Define the physical 3D door swing danger zone on the asphalt and build a fail-safe state machine (`SAFE`, `WARNING`, `CRITICAL`) to make lock/unlock decisions. |
| **Phase 4** | **IoT, ESP32, Wokwi & MQTT** | *Upcoming* | Write real C++ firmware for an ESP32 microcontroller in Wokwi, wire a capacitive door touch sensor and an SG90 servo motor, and connect them over MQTT. |
| **Phase 5** | **Cloud & Database (Supabase)** | *Upcoming* | Store every sensor reading, detection, safety event, and door latch action in a PostgreSQL cloud database (with offline local fallback). |
| **Phase 6** | **3D Digital Twin & Simulation** | *Upcoming* | Build an interactive Three.js 3D automotive environment with moving cyclists, cars, and sensor cones, driven by our ground-truth physics engine. |
| **Phase 7** | **Machine Learning Risk Classifier** | *Upcoming* | Generate 100–300 reproducible experiments, train a Random Forest model on synthetic telemetry, and evaluate its predictive accuracy against our safety rules. |
| **Phase 8** | **Complete Web Dashboard & Chatbot**| *Upcoming* | Unify everything into a polished, automotive-grade monitoring dashboard with live charts, alert center, and a queryable safety assistant. |
| **Phase 9** | **Benchmarking & Final Validation** | *Upcoming* | Measure real-world performance: calculate distance error (MAE), processing latency (ms), false-safe rates, and produce final evaluation reports. |

---

## 3. Detailed Walkthrough of Each Phase

### Phase 1: Computer Vision Integration & Object Tracking (DONE)
* **What it does in simple terms**: We take real traffic video footage, feed it through a lightweight deep learning model (YOLO), identify all bicycles, motorcycles, cars, trucks, buses, and pedestrians, and give each one a persistent badge number (Tracking ID) using ByteTrack so the computer never forgets who is who between frames.
* **Why it matters**: Without persistent tracking, the computer would treat every video frame as a brand-new world. You cannot tell if a cyclist is moving toward your door unless you can track the *same* cyclist across consecutive moments in time.
* **Deliverables achieved**: FastAPI backend, local `yolo26n.pt` weights integration, ByteTrack tracker, trajectory trails, automotive HUD overlays, web video streaming with H.264 transcoding, and interactive dashboard UI.

---

### Phase 2: Motion, Geometry & Time-to-Collision (TTC) (NEXT)
* **What it does in simple terms**: Right now, the computer only knows pixels on a flat 2D screen (e.g. "motorcycle is at pixel $x=640, y=720$"). Phase 2 uses camera optics, mounting height, and road geometry to convert those pixels into real physical measurements: "motorcycle is 12.4 meters behind the car, traveling forward at 8.2 meters per second, with a Time-to-Collision of 1.5 seconds."
* **Why it matters**: You cannot make automotive safety decisions using screen pixels. A car needs to know real distances in meters and real speeds in meters per second.
* **Key concept**: We use the *bottom-center footpoint* of the bounding box because that is where the road user's wheels or shoes touch the flat ground plane.

---

### Phase 3: Door-Zone Collision & Safety Decision Engine
* **What it does in simple terms**: We draw an invisible danger boundary on the road representing the physical area where the car door swings open. The safety engine constantly calculates whether an approaching vehicle's path will slice through that door zone before the passenger can safely get out.
* **Why it matters**: A cyclist 10 meters away who is riding straight down the sidewalk poses zero threat. But a cyclist 10 meters away who is riding 1 meter from the driver's door is in immediate danger. The safety engine makes deterministic decisions: `SAFE`, `WARNING`, or `CRITICAL`.
* **Fail-Safe Principle**: The safety engine is deterministic software (a strict rule-based state machine). It *never* guesses. If a sensor drops out or information is missing, the system defaults to keeping the door safely locked.

---

### Phase 4: IoT Controller, ESP32, Wokwi & MQTT Actuation
* **What it does in simple terms**: We transition from pure software to embedded hardware. We create a simulated ESP32 microcontroller circuit on Wokwi. When an occupant touches the door handle, a capacitive touch sensor sends a digital signal to the ESP32. The ESP32 checks the current safety status over MQTT and drives an SG90 servo motor to either allow the door to unlock or physically keep it locked.
* **Why it matters**: This proves the system is an actual IoT automotive product, not just a video playback script. The microcontroller executes actual C/C++ firmware with PWM actuator signals, interrupt routines, and MQTT publish/subscribe messaging.

---

### Phase 5: Cloud Persistence & Database (Supabase + Local SQLite)
* **What it does in simple terms**: We create a flight recorder (black box) for the vehicle. Every time a door-touch occurs, every time a cyclist approaches, and every time the lock engages, the exact timestamp, distance, speed, and actuator status are saved into a database.
* **Why it matters**: Commercial vehicle safety systems must maintain audit logs for safety certification, incident investigation, and fleet analytics.
* **Architecture**: We implement a dual-mode database pattern: it syncs to cloud Supabase (PostgreSQL) when online, but falls back seamlessly to a local SQLite database if the network is disconnected.

---

### Phase 6: Ground-Truth Simulation Engine & 3D Digital Twin
* **What it does in simple terms**: We build a 3D digital twin of our car, road, and approaching traffic using Three.js and React Three Fiber. Underneath the graphics, a dedicated physics engine simulates exact vehicle speeds, positions, and virtual mmWave radar readings with configurable noise.
* **Why it matters**: This solves the biggest problem in computer vision research: *How do you know if your camera's distance estimate was right?* Because our simulation knows the true physical ground truth ($8.00\text{ m}$), we can compare it against our camera's measurement ($8.35\text{ m}$) to scientifically calculate our error ($0.35\text{ m}$).

---

### Phase 7: Machine Learning Risk Classifier (Random Forest)
* **What it does in simple terms**: We run 100 to 300 automated simulation experiments with different speeds, weather, distances, and road users. We collect this telemetry into a dataset and train a Random Forest machine learning classifier to predict collision risk based on multi-sensor patterns.
* **Why it matters**: This provides an intelligent second opinion (advisory risk scoring) that can learn complex multi-sensor patterns.
* **Golden Rule**: As requested in our architecture, the ML model is strictly an *advisory* assistant. It can never independently unlock a door if our deterministic safety state machine says there is danger.

---

### Phase 8: Professional Web Dashboard & Automotive Assistant
* **What it does in simple terms**: We bring all the components together into a single, unified web application. The driver/operator can see live 3D digital twin views, real video analysis, radar charts, ESP32 connectivity indicators, door lock animations, alert logs, and ask questions to a built-in safety chatbot.
* **Why it matters**: Demonstrates product-grade presentation for course evaluation and portfolio presentation.

---

### Phase 9: Comprehensive Benchmarking & Performance Evaluation
* **What it does in simple terms**: We stress-test the entire system and generate quantitative proof that it works. We measure:
  - Computer vision accuracy and inference latency (FPS / ms).
  - Distance estimation Mean Absolute Error (MAE in meters).
  - IoT message latency and ESP32 servo response time (ms).
  - Machine learning Precision, Recall, and Confusion Matrices.
  - Critical safety evaluation: False-Safe Rate (must be 0%) and False-Warning Rate.
* **Why it matters**: This provides empirical data, charts, and metrics needed for academic research papers, project reports, and viva presentations.

---

## 4. Why This Specific Sequence Matters

Beginner developers often make the mistake of building the 3D car animation or the web dashboard first. When they finally try to connect real video, real sensors, or an ESP32, the entire system collapses because the underlying data models and math never existed.

By building **vertically**:
1. We proved the computer can see and track road users reliably (**Phase 1**).
2. We convert those visual tracks into real physical meters and speeds (**Phase 2**).
3. We teach the system to judge whether those physical speeds will hit the door (**Phase 3**).
4. We wire that decision to an IoT microcontroller and physical servo motor (**Phase 4**).
5. Only then do we add cloud storage, 3D visualization, ML analytics, and full dashboard integration (**Phases 5-9**).

Every phase builds on concrete, tested reality.
