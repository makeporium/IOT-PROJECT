# DOORSAFE — Phase 1: Computer Vision & Object Tracking Engine

## Overview
Phase 1 delivers the foundational computer vision, tracking, and video analysis pipeline for **DOORSAFE** (AI-Powered IoT-Based Vehicle Door Opening Safety System).

---

## 1. Implemented Components

### 1.1 Computer Vision & Detector (`backend/app/computer_vision/detector.py`)
- Wraps Ultralytics YOLO using the local pretrained `yolo26n.pt` (5.54 MB) weights.
- Targets road participants: `person` (0), `bicycle` (1), `car` (2), `motorcycle` (3), `bus` (5), `truck` (7).
- Automatically configures execution device (`cuda` if GPU available, otherwise `cpu`).
- Integrated with ByteTrack tracker configuration (`bytetrack.yaml`) via `model.track(..., tracker="bytetrack.yaml", persist=True)`.

### 1.2 Trajectory & State Tracker (`backend/app/tracking/trajectory_tracker.py`)
- Maintains persistent object identity across frames.
- Stores historical trajectory of ground footpoints `((x1 + x2)/2, y2)` with timestamps.
- Records track lifetime/age (number of observed frames).
- Prunes stale or lost tracks exceeding inactivity threshold.
- Calculates pixel displacement vectors $(\Delta x, \Delta y)$ for velocity estimation.

### 1.3 Automotive Frame Annotator (`backend/app/computer_vision/annotator.py`)
- Class-coded high contrast automotive color palette:
  - **Bicycle**: Electric Cyan (`#06B6D4`)
  - **Motorcycle**: Amber/Orange (`#F59E0B`)
  - **Person**: Coral/Rose (`#F43F5E`)
  - **Car**: Sky Blue (`#3B82F6`)
  - **Bus / Truck**: Violet/Purple (`#8B5CF6`)
- Top HUD banner displaying real-time FPS, inference latency (ms), frame count, and active target counts.
- Corner bracket automotive bounding boxes.
- Persistent ID badge (`ID #{id} | {CLASS} {CONF}`).
- Concentric ground contact footpoint indicator.
- Fading motion trajectory polyline trails.

### 1.4 Video Pipeline & Asynchronous Processing (`backend/app/computer_vision/pipeline.py`)
- Handles input video decoding, frame-by-frame inference, tracking, annotation, and encoding to MP4.
- Generates structured frame-by-frame telemetry JSON payload.

### 1.5 Scaffolding for Simulation Engine & Evaluation (`backend/app/simulation/`, `backend/app/evaluation/`)
- Incorporates user feedback (Change 2):
  - `simulation/ground_truth.py`: Ground truth telemetry model with `source_mode`, `measurement_type`, exact positions and velocities.
  - `simulation/sensor_simulator.py`: Virtual 77 GHz mmWave radar simulator with configurable Gaussian noise.
  - `simulation/vehicle_physics.py`: Vehicle geometry and driver-side door opening danger polygon.
  - `simulation/object_motion.py`: Participant kinematics.
  - `simulation/scenario_generator.py`: Reproducible overtaking and approach scenarios.
  - `evaluation/distance_metrics.py`: MAE, RMSE, MAPE calculations against ground truth.
  - `evaluation/detection_metrics.py`: Class distributions and confidence statistics.
  - `evaluation/latency_metrics.py`: P50, P90, P95 latency profiles and throughput.
  - `evaluation/safety_metrics.py`: False Safe Rate, False Warning Rate, Decision Accuracy.

### 1.6 Web Interface (`backend/app/static/index.html`)
- Automotive UI aesthetic (white background, light blue accents, dark slate HUD).
- Pre-loaded benchmark video selector (`01_012-3.mp4`, `01_034-3.mp4`, `01_045-3.mp4`).
- Drag-and-drop real video uploader.
- Configurable confidence threshold slider.
- Real-time animated progress bar with ETA and live FPS/latency stats.
- Synchronized video player streaming the annotated output.
- Target telemetry table with track IDs, classes, confidences, bounding boxes, and ground footpoints.
- Full structured JSON viewer and annotated MP4 video downloader.

---

## 2. API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Serves the interactive Video Perception & Tracking Web Dashboard |
| `GET` | `/api/health` | Returns system diagnostic info, model status, and device acceleration |
| `GET` | `/api/video/samples` | Lists bundled benchmark traffic videos |
| `GET` | `/api/video/samples/{filename}/stream` | Streams raw source video for preview |
| `POST` | `/api/video/upload` | Multipart file upload for custom traffic footage |
| `POST` | `/api/video/process` | Asynchronously initiates YOLO detection + ByteTrack pipeline |
| `GET` | `/api/video/jobs` | Lists all active and completed processing jobs |
| `GET` | `/api/video/jobs/{job_id}` | Polls progress percentage, frames processed, FPS, and latency |
| `GET` | `/api/video/jobs/{job_id}/stream` | Streams the annotated MP4 video |
| `GET` | `/api/video/jobs/{job_id}/download` | Downloads the annotated MP4 file |
| `GET` | `/api/video/jobs/{job_id}/telemetry` | Downloads complete frame-by-frame JSON telemetry |

---

## 3. How to Run

### Step 1: Start the Backend Server
```powershell
& ".\YOLO MODEL\venv\Scripts\python.exe" backend/main.py
```
Or with Uvicorn:
```powershell
$env:PYTHONPATH = "backend"
& ".\YOLO MODEL\venv\Scripts\uvicorn.exe" app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Step 2: Open the Web Dashboard
Navigate to `http://localhost:8000` in any web browser.

### Step 3: Run the Test Suite
```powershell
$env:PYTHONPATH = "backend"
& ".\YOLO MODEL\venv\Scripts\pytest.exe" backend/tests/ -v
```
