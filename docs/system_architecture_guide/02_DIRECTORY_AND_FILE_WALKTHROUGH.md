# DOORSAFE: Complete Directory & Codebase Walkthrough

> **A Senior Architect's Tour of Every Directory, File, and Design Decision**
> 
> *Explaining what each file does, why it was made, and how it connects to the system.*

---

## 1. High-Level Monorepo Structure

```
IOT PROJECT/
│
├── backend/                       # Python FastAPI Backend & Perception Server
│   ├── app/                      # Core application logic & algorithms
│   │   ├── api/                  # HTTP REST endpoints & streaming handlers
│   │   ├── computer_vision/      # YOLO detection, video decoding & annotation
│   │   ├── tracking/             # Persistent trajectory history & footpoints
│   │   ├── simulation/           # Ground-truth physics & virtual sensor models
│   │   ├── evaluation/           # Scientific accuracy & latency metrics
│   │   ├── models/               # Strongly-typed Pydantic data schemas
│   │   ├── static/               # Built-in lightweight test dashboard (HTML/JS/CSS)
│   │   └── config.py             # Central configuration settings & path resolver
│   │
│   ├── data/                     # Local storage directories
│   │   ├── samples/              # Bundled benchmark test videos (01_012-3, etc.)
│   │   ├── uploads/              # Custom videos uploaded by the user
│   │   └── outputs/              # Processed annotated MP4s & JSON telemetry
│   │
│   ├── models/                   # Neural network weights
│   │   └── yolo26n.pt            # Local Ultralytics COCO model weights (5.54 MB)
│   │
│   ├── tests/                    # Automated unit & integration tests
│   │   ├── test_detection.py     # Verifies YOLO model loading & inference
│   │   ├── test_tracking.py      # Verifies ByteTrack persistence & footpoints
│   │   ├── test_pipeline.py      # Tests full video processing & JSON generation
│   │   └── test_api.py           # Tests FastAPI endpoints & streaming
│   │
│   ├── main.py                   # Server entrypoint (uvicorn launcher)
│   └── requirements.txt          # Python dependencies
│
├── iot/                          # Embedded Firmware & Hardware Simulation
│   ├── esp32/                    # ESP32 C++ firmware (PlatformIO / Arduino)
│   └── wokwi/                    # Wokwi circuit schematics & pin wiring (diagram.json)
│
├── ml/                           # Machine Learning & Experimental Datasets
│   ├── datasets/                 # Synthetic simulation experiment datasets (CSV)
│   └── models/                   # Serialized Random Forest classifiers (.joblib)
│
├── docs/                         # Engineering Documentation & Guides
│   ├── system_architecture_guide/# In-depth guides for developers & reviewers
│   └── PHASE_1_SUMMARY.md        # Technical specification for Phase 1
│
├── YOLO MODEL/                   # Original workspace files preserved intact
└── .gitignore                    # Clean Git configuration
```

---

## 2. Directory-by-Directory Breakdown

### 2.1 `backend/` — The Core Perception Engine

This directory contains the central brain of the software. We chose Python and FastAPI because Python is the undisputed industry standard for computer vision (OpenCV, PyTorch, YOLO), while FastAPI offers asynchronous high-throughput REST APIs and automatic OpenAPI documentation.

#### `backend/main.py`
* **What it does**: This is the launchpad of the application. It creates the FastAPI server instance, configures Cross-Origin Resource Sharing (CORS) so web browsers can communicate with it, mounts the static dashboard, and registers the API route handlers.
* **Why it was made**: Centralizing server lifecycle hooks (startup and shutdown) in one place ensures the YOLO neural network is pre-warmed into memory before the first user request arrives, eliminating cold-start delays.

#### `backend/requirements.txt`
* **What it does**: Lists all required Python packages with pinned minimum versions: `fastapi`, `uvicorn`, `ultralytics`, `opencv-python`, `torch`, `lap`, `imageio-ffmpeg`, and `pytest`.
* **Why it was made**: Guarantees reproducibility. Any engineer or student can clone the project, run `pip install -r requirements.txt`, and have a functioning environment without version conflicts.

---

### 2.2 `backend/app/config.py` — The Central Command Hub

* **What it does**: Holds all system-wide parameters:
  - Path to the local YOLO model (`backend/models/yolo26n.pt`).
  - Default confidence threshold (`0.35`).
  - Target automotive classes (`person`, `bicycle`, `motorcycle`, `car`, `bus`, `truck`).
  - Storage directories for uploads, outputs, and samples.
* **Why it was made (Senior Dev Rationale)**: **Never hardcode paths or magic numbers inside business logic.** If you want to change the confidence threshold or swap model weights, you modify `config.py` (or a `.env` file) in one single place, and the entire system updates cleanly.

---

### 2.3 `backend/app/models/schemas.py` — The Data Contract

* **What it does**: Defines structured Pydantic models for every data entity that travels through the system:
  - `BoundingBox`: Pixels coordinates $(x_1, y_1, x_2, y_2)$, width, height, centroid, and the critical **ground footpoint** $(foot_x, foot_y)$.
  - `TrackedObject`: Persistent track ID, class name, detection confidence, and history of past positions.
  - `FrameTelemetry`: Frame index, timestamp, inference latency in ms, FPS, active target counts, and object lists.
  - `VideoJobInfo`: Background task status (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`), progress %, and download URLs.
* **Why it was made**: In real automotive software, you cannot pass around unstructured, arbitrary dictionaries. Strongly-typed schemas guarantee that frontend, backend, database, and IoT controllers always speak the exact same data language with zero silent type bugs.

---

### 2.4 `backend/app/computer_vision/` — The Vision Pipeline

This module is responsible for turning raw video bytes into meaningful road target observations.

#### `detector.py` (`YOLODetector`)
* **What it does**: Loads the local `yolo26n.pt` neural network weights. When a video frame arrives, it runs inference, filters out irrelevant objects (like dogs or chairs), retains only road users (bikes, motorcycles, cars, pedestrians), and triggers ByteTrack to track them across time.
* **Why it was made**: It isolates the deep learning library from the rest of the app. If tomorrow you decide to upgrade to YOLOv10, YOLO11, or RT-DETR, you only edit this single file; the rest of the application remains untouched.

#### `annotator.py` (`FrameAnnotator`)
* **What it does**: Draws automotive-grade graphics directly onto the video frames using OpenCV:
  - Sleek corner brackets on bounding boxes.
  - Color-coded badges with ID and confidence (e.g. Cyan for bicycles, Amber for motorcycles).
  - Concentric bullseye target on the **ground contact point**.
  - Smooth trailing motion path showing where the road user came from.
  - Dark slate HUD banner at the top displaying real-time FPS, latency, and frame counts.
* **Why it was made**: Raw numbers in a terminal do not allow human reviewers to verify whether the AI is detecting the right targets. High-quality visual feedback is essential for automotive debugging and project presentation.

#### `pipeline.py` (`VideoProcessingPipeline`)
* **What it does**: The master conductor of the vision service. It:
  1. Opens the input video.
  2. Loops frame-by-frame, calling detection, tracking, and annotation.
  3. Writes the annotated frames to disk.
  4. Transcodes the raw output to web-compliant **H.264 (AVC) with `+faststart`** using `imageio-ffmpeg` so browsers can play it.
  5. Exports a timestamped JSON file containing complete frame-by-frame telemetry.
* **Why it was made**: It decouples video file processing from the web API. It can run in the background without freezing the server.

---

### 2.5 `backend/app/tracking/` — Trajectory & Object Lifetimes

#### `trajectory_tracker.py` (`TrajectoryTracker`)
* **What it does**: Maintains a memory of every road user currently visible:
  - Remembers the history of the object's ground footpoints over the last 50 frames.
  - Measures how long the object has been on screen (track age).
  - Automatically prunes lost objects if they disappear for more than 30 consecutive frames.
  - Calculates pixel velocity vectors $(\Delta x, \Delta y)$ to determine movement direction.
* **Why it was made**: A single video frame tells you *where* someone is right now. Tracking tells you *where they came from and where they are heading*. This history is the raw material needed in Phase 2 for Time-to-Collision (TTC) calculations.

---

### 2.6 `backend/app/simulation/` — Ground-Truth Physics Engine

*(Implemented in response to Change 2)*

* **Why this folder exists**: When a camera estimates that a bicycle is 8.5 meters away, *how do you prove if that is accurate?*
  You cannot prove accuracy without a reliable ground truth. This package creates reproducible road scenarios where every coordinate, speed, and trajectory is known with 100% mathematical precision.
* **Files inside**:
  - `ground_truth.py`: Defines ground-truth coordinates $(X_{gt}, Y_{gt})$ and flags (`source_mode="SIMULATION"`, `measurement_type="ACTUAL"`).
  - `vehicle_physics.py`: Calculates the parked car's physical dimensions and the door swing danger corridor polygon.
  - `object_motion.py`: Simulates continuous kinematics (speed, lateral drift, heading angle) for bicycles and motorcycles.
  - `scenario_generator.py`: Generates standardized test cases (e.g. cyclist overtaking at $4.5\text{ m/s}$, lane-filtering motorcycle at $11.0\text{ m/s}$).
  - `sensor_simulator.py`: Simulates a 77 GHz FMCW mmWave radar with realistic Gaussian measurement noise ($\sigma = 0.15\text{ m}$).

---

### 2.7 `backend/app/evaluation/` — Scientific Validation Metrics

*(Implemented in response to Change 2)*

* **Why this folder exists**: Academic engineering projects require rigorous quantitative proof, not just visual demonstrations.
* **Files inside**:
  - `distance_metrics.py`: Calculates Mean Absolute Error (MAE), Root Mean Squared Error (RMSE), and Mean Absolute Percentage Error (MAPE) by comparing camera estimates against simulation ground truth.
  - `detection_metrics.py`: Analyzes confidence distributions and target class breakdowns across video runs.
  - `latency_metrics.py`: Measures inference latency profiles (Mean, P50, P90, P95) and processing throughput (FPS).
  - `safety_metrics.py`: Evaluates the critical safety matrix: True Positives, False Positives (nuisance warnings), and False Negatives (dangerous False-Safe errors where door was unlocked during a hazard).

---

### 2.8 `backend/app/api/` — The REST API Layer

* `routes_health.py`:
  - `GET /api/health`: Provides system diagnostics, verifying that YOLO weights are loaded and reporting hardware acceleration (CPU vs CUDA GPU).
* `routes_video.py`:
  - `GET /api/video/samples`: Lists bundled benchmark traffic videos (`01_012-3.mp4`, etc.).
  - `POST /api/video/upload`: Accepts user-uploaded MP4/AVI videos.
  - `POST /api/video/process`: Initiates background perception and tracking.
  - `GET /api/video/jobs/{job_id}`: Reports real-time progress %, FPS, and latency.
  - `GET /api/video/jobs/{job_id}/stream`: Streams annotated MP4 video with HTTP 206 byte-range support.
  - `GET /api/video/jobs/{job_id}/telemetry`: Serves the full structured JSON telemetry payload.
  - `GET /api/video/jobs/{job_id}/download`: Downloads the annotated MP4 file.

---

### 2.9 `backend/app/static/index.html` — The Phase 1 Dashboard

*(Implemented in response to Change 4)*

* **What it does**: A lightweight, responsive web application served directly by FastAPI at `http://localhost:8000`.
* **Design Philosophy**: Built according to automotive software standards (clean white background, light blue accents, neutral gray surfaces, dark slate HUD) instead of generic templates.
* **Features**:
  - Video selector for pre-loaded traffic benchmarks.
  - Drag-and-drop custom video uploader.
  - Real-time confidence threshold slider.
  - Live animated progress bar with processed frame counts.
  - HTML5 video player displaying the annotated output.
  - Observed Targets Table showing persistent IDs, class pills, confidences, bounding boxes, and ground footpoints.
  - Structured JSON telemetry viewer with a one-click copy button.
  - Completed Runs Selector: automatically remembers and reloads past analysis runs upon page refresh.

---

### 2.10 `backend/tests/` — Automated Verification Suite

* **Why automated tests matter**: In automotive and IoT engineering, software changes can silently break critical safety logic. Every major capability has an automated regression test:
  - `test_detection.py`: Verifies that YOLO loads and correctly categorizes all 6 required vehicle and pedestrian classes.
  - `test_tracking.py`: Proves that ByteTrack maintains persistent track IDs and updates historical footpoints across consecutive frames.
  - `test_pipeline.py`: Runs an end-to-end perception test on a real video clip, verifying that the output MP4 and telemetry JSON are valid.
  - `test_api.py`: Validates FastAPI endpoints, job creation, health diagnostics, and static file delivery.

---

### 2.11 Future Phase Directories

* `iot/esp32/` & `iot/wokwi/`: Dedicated to Phase 4 for ESP32 C++ firmware, Wokwi `diagram.json` schematics, capacitive touch inputs, and SG90 servo motor control.
* `ml/datasets/` & `ml/models/`: Dedicated to Phase 7 for synthetic scenario dataset generation (100–300 runs) and Scikit-learn Random Forest model training.
* `docs/`: Dedicated home for architecture specifications, circuit diagrams, and phase summaries.

---

## 3. Summary of Key Senior Engineering Decisions

1. **Why ByteTrack via Ultralytics instead of custom tracking?**
   ByteTrack solves low-confidence association during temporary occlusion. By using Ultralytics' tested implementation and writing our own trajectory-history layer on top, we save hundreds of hours of debugging low-level bipartite matching and focus on automotive motion physics.
2. **Why Ground Footpoints $(foot_x, foot_y)$?**
   In 3D perspective projection, bounding box centroids $(\frac{x_1+x_2}{2}, \frac{y_1+y_2}{2})$ float in mid-air (at the cyclist's waist or car's roofline). Only the bottom-center point touches the ground plane ($Z=0$), which is required to calculate accurate metric distances in Phase 2.
3. **Why Transcode to H.264 (+faststart)?**
   Standard OpenCV `mp4v` codec cannot be decoded natively by modern web browsers. Transcoding to H.264 with the `moov` atom placed at the start allows smooth, instant streaming in any browser without third-party plugins.
4. **Why Separate Simulation Ground Truth from Camera Estimates?**
   Without known ground truth, you cannot evaluate error. Our simulation engine provides mathematical ground truth so we can rigorously compute error metrics (MAE, RMSE) for our academic report.
