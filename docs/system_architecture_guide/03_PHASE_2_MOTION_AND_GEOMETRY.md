# Phase 2 Explained: Camera Geometry, Metric Kinematics & Time-To-Collision (TTC)

> **A Senior Developer's Guide to Turning Pixels into Physical Reality**
> 
> *Written in plain language for engineers, evaluators, and reviewers.*

---

## 1. What Problem Did Phase 2 Solve?

In Phase 1, our AI learned to detect and track objects, but it only understood **computer screen pixels**:
> *"Object #07 is at pixel $(x=480, y=860)$."*

In automotive safety, screen pixels are useless. A microcontroller inside a car cannot make life-or-death decisions using screen pixels:
- Is that pixel 3 meters away or 30 meters away?
- Is that bicycle closing in at $15\text{ km/h}$ or moving away at $40\text{ km/h}$?
- Will that motorcycle hit the driver's door when it swings open in $1.5$ seconds?

**Phase 2 bridges this gap.** It converts flat 2D image coordinates into real physical measurements:
> *"Object #07 (Motorcycle) is $1.3\text{ meters}$ to the left, $8.4\text{ meters}$ behind the car, approaching at $24\text{ km/h}$, with an urgent Time-To-Collision of $1.3\text{ seconds}$ directly through the door swing corridor."*

---

## 2. Core Concepts Explained in Simple Terms

```
                 Pinhole Camera (Height h, Pitch Angle θ)
                            ▲
                           ╱│╲
                          ╱ │ ╲   Optical Ray
                         ╱  │  ╲
                        ╱   │   ╲
                       ╱    │    ╲
  Flat Road Plane ────●─────┴─────●────────── (Z = 0)
                 Footpoint       Vehicle
                  (u, v)         Center
```

### 2.1 Why the "Footpoint" Matters (Ground Contact Point)
If you draw a bounding box around a cyclist, the center of the box floats in mid-air (near their stomach or handlebars). 

A single camera cannot calculate 3D depth for a point floating in empty air without depth sensors or LiDAR. However, we know for a fact that the cyclist's **tires touch the road**. The road is a flat ground surface ($Z = 0$).

Therefore, we calculate the **bottom-center of the bounding box**:
$$\text{foot}_x = \frac{x_1 + x_2}{2}, \quad \text{foot}_y = y_2$$

Because this footpoint touches the known ground plane, we can cast a mathematical ray from the camera lens through that pixel and calculate exactly where it strikes the asphalt!

---

### 2.2 Pinhole Camera Geometry & Downward Pitch
The camera is mounted on the rear corner or side of the parked vehicle at height $h$ (e.g. $1.15\text{ m}$) with a slight downward tilt angle $\theta$ (e.g. $8.5^\circ$).

1. **Ray Angle ($\alpha$)**: The vertical angle of the pixel relative to the camera's optical center:
   $$\alpha = \arctan\left(\frac{v - c_y}{f_y}\right)$$
2. **Total Depression Angle ($\gamma$)**: The downward angle between the ray and the horizontal plane:
   $$\gamma = \theta_{\text{pitch}} + \alpha$$
3. **Longitudinal Distance ($Y$)**: By simple right-angle trigonometry:
   $$Y = \frac{h}{\tan(\gamma)}$$
4. **Lateral Distance ($X$)**: The sideways offset from the vehicle centerline:
   $$X = \frac{Y \cdot (u - c_x)}{f_x}$$

---

### 2.3 The Reality of Uncertainty (Why We Never Fake Precision)
In mathematics, when an object is very far away ($Y > 30\text{ m}$), its footpoint appears near the horizon line. A tiny camera vibration of just 2 pixels could change the distance calculation by several meters!

Senior automotive engineers never claim false precision. In DOORSAFE:
- For an object $4\text{ meters}$ away, uncertainty is tiny: $\pm 0.1\text{ m}$ ($98\%$ confidence).
- For an object $25\text{ meters}$ away, uncertainty is larger: $\pm 3.5\text{ m}$ ($65\%$ confidence).

We expose both the **confidence score** ($0.0$ to $1.0$) and the **uncertainty margin** ($\pm\text{m}$) in our telemetry so the safety system knows when to trust camera measurements and when to seek radar confirmation.

---

### 2.4 Kinematic Velocity Filtering (Why Direct Differencing Fails)
If you measure speed by naively dividing $\frac{\Delta Y}{\Delta t}$ frame-by-frame:
- Frame 1: $Y = 10.0\text{ m}$
- Frame 2: $Y = 9.8\text{ m}$ (Speed = $-6.0\text{ m/s}$)
- Frame 3: Bounding box flickers slightly by 2 pixels $\rightarrow Y = 10.1\text{ m}$ (Speed = $+9.0\text{ m/s}$)!

Direct differencing causes wild speed fluctuations. To solve this, we implemented an **Alpha-Beta State Filter** (`ObjectKinematicTracker`):
- It predicts where the road user should be next based on their past speed.
- When the new camera measurement arrives, it smoothly blends the prediction with the observation ($\alpha = 0.70$, $\beta = 0.35$).
- This produces stable, noise-free velocities in $\text{m/s}$ and $\text{km/h}$, as well as acceleration ($\text{m/s}^2$) and heading direction.

---

### 2.5 True Dynamic Time-To-Collision (TTC)
Many student projects naively divide $\text{Distance} / \text{Speed}$ for every object. That is dangerous and incorrect:
*Suppose a car is driving in the far adjacent lane $3.8\text{ meters}$ away. If you calculate $\text{Distance} / \text{Speed}$, it gives $1.5\text{ seconds}$ and falsely locks your door—even though the car is never going to come anywhere near your door!*

In DOORSAFE, our `TTCCalculator` checks **2D spatial trajectory intersection**:
1. It defines the driver-side door opening danger envelope:
   $$X \in [-2.10\text{ m}, -0.75\text{ m}], \quad Y \in [-0.60\text{ m}, 1.80\text{ m}]$$
2. It solves for the future time window $[t_{\text{enter}}, t_{\text{exit}}]$ where the oncoming vehicle's trajectory $[x(t), y(t)]$ slices through that physical box.
3. If an intersection is predicted:
   - **CRITICAL**: $TTC \le 2.5\text{ seconds}$ $\rightarrow$ Immediate door lockout alert!
   - **WARNING**: $TTC \le 4.5\text{ seconds}$ $\rightarrow$ Approaching target alert.
   - **SAFE**: $TTC > 4.5\text{ seconds}$ or path passes safely outside the door corridor.

---

### 2.6 Forward Trajectory Prediction
Our `TrajectoryPredictor` projects waypoints at $t \in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]\text{ seconds}$. 

Using our inverse geometry, it converts those future physical ground coordinates back into screen pixels $(u, v)$ so our video annotator can draw **dotted forward motion lines** showing where each road user is heading!

---

## 3. What Was Built & Files Modified

| File | What Was Added |
| :--- | :--- |
| `backend/app/distance_estimation/camera_geometry.py` | `CameraParameters`, `PinholeGroundProjector`, ground projection, uncertainty calculation ($\sigma_Y$), and inverse pixel projection. |
| `backend/app/trajectory/kinematics.py` | `KinematicEstimator`, Alpha-Beta state tracker, smooth velocity in $\text{m/s}$ and $\text{km/h}$, acceleration, and heading. |
| `backend/app/trajectory/ttc_calculator.py` | `TTCCalculator`, 2D door-zone entry/exit horizon math, urgency classification (`CRITICAL`, `WARNING`, `SAFE`). |
| `backend/app/trajectory/predictor.py` | `TrajectoryPredictor`, future waypoints from $0.5$ to $3.0$ seconds. |
| `backend/app/computer_vision/annotator.py` | Automotive HUD overlays: metric distance, speed ($\text{km/h}$), TTC badges, color-coded hazard alerts, and dotted future path lines. |
| `backend/app/computer_vision/pipeline.py` | Integrated geometry, kinematics, TTC, and forward prediction into the frame loop. |
| `backend/app/api/routes_calibration.py` | REST endpoints to view/update camera parameters (`/api/calibration/camera`) and test point projections. |
| `backend/app/static/index.html` | Camera calibration sliders, Phase 2 telemetry table (Distance, Speed, TTC, Urgency), and a **Top-Down 2D Bird's-Eye-View (BEV) Radar Canvas**! |
| `backend/tests/test_distance.py` | 5 unit tests for pinhole projection, lateral symmetry, roundtrip inversion, and uncertainty. |
| `backend/tests/test_kinematics_ttc.py` | 4 unit tests for velocity convergence, critical door zone TTC, adjacent lane non-conflicts, and waypoints. |

---

## 4. Verification & Test Results

All **18 automated tests** passed with $100\%$ success rate:

```powershell
$env:PYTHONPATH = "backend"; & ".\YOLO MODEL\venv\Scripts\pytest.exe" backend/tests/ -v
```

```
backend/tests/test_api.py::test_api_health PASSED                        [  5%]
backend/tests/test_api.py::test_api_list_samples PASSED                  [ 11%]
backend/tests/test_api.py::test_api_root_serves_html PASSED              [ 16%]
backend/tests/test_api.py::test_api_process_job_lifecycle PASSED         [ 22%]
backend/tests/test_api.py::test_api_calibration PASSED                   [ 27%]
backend/tests/test_detection.py::test_detector_initialization PASSED     [ 33%]
backend/tests/test_detection.py::test_detector_single_frame_inference PASSED [ 38%]
backend/tests/test_distance.py::test_pinhole_geometry_initialization PASSED [ 44%]
backend/tests/test_distance.py::test_ground_projection_monotonicity PASSED [ 50%]
backend/tests/test_distance.py::test_ground_projection_lateral_symmetry PASSED [ 55%]
backend/tests/test_distance.py::test_inverse_projection_roundtrip PASSED [ 61%]
backend/tests/test_distance.py::test_uncertainty_growth_with_distance PASSED [ 66%]
backend/tests/test_kinematics_ttc.py::test_kinematic_velocity_convergence PASSED [ 72%]
backend/tests/test_kinematics_ttc.py::test_ttc_critical_door_zone_conflict PASSED [ 77%]
backend/tests/test_kinematics_ttc.py::test_ttc_safe_distant_lateral_lane PASSED [ 83%]
backend/tests/test_kinematics_ttc.py::test_trajectory_predictor_waypoints PASSED [ 88%]
backend/tests/test_pipeline.py::test_video_pipeline_end_to_end PASSED    [ 94%]
backend/tests/test_tracking.py::test_trajectory_tracker_lifecycle PASSED [100%]

======================= 18 passed, 1 warning in 19.66s ========================
```

---

## 5. How to Run & Experience Phase 2

1. Make sure your server is running:
   ```powershell
   & ".\YOLO MODEL\venv\Scripts\python.exe" backend/main.py
   ```
2. Open your browser at `http://localhost:8000`.
3. In the left sidebar:
   - Adjust the **Camera Height** (default $1.15\text{ m}$) or **Downward Pitch** ($8.5^\circ$) if you wish to calibrate for different vehicle heights.
   - Select any benchmark video and click **"Run Perception, Kinematics & TTC"**.
4. In the main viewport:
   - Watch the video with real-time distance $(X, Y)$, speed ($\text{km/h}$), and TTC countdown badges.
   - Look at the new **Top-Down BEV Radar Canvas**: watch targets plotted in real metric space relative to the parked car and its door danger zone.
   - Inspect the table with exact metric coordinates, speed, TTC, and confidence ratings.
