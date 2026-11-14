# DOORSAFE System Architecture & Engineering Handbook

Welcome to the engineering documentation for **DOORSAFE: AI-Powered IoT-Based Vehicle Door Opening Safety System**.

This folder contains in-depth, plain-English documentation detailing the complete 9-phase roadmap and a comprehensive tour of every file, directory, and architectural decision in the codebase.

---

## 📚 Handbook Navigation

### 1. [Master Roadmap & The 9 Development Phases](file:///c:/Users/astro/Desktop/College%20Ayush/IOT/IOT%20PROJECT/docs/system_architecture_guide/01_PROJECT_ROADMAP_AND_PHASES.md)
* How many total phases exist (**9 Total Phases**).
* Summary table of all 9 milestones.
* Plain-English explanation of each phase (from Computer Vision to 3D Twin, ESP32 IoT, and Machine Learning).
* Why our vertical development sequence protects against integration failure.

### 2. [Complete Directory & Codebase Walkthrough](file:///c:/Users/astro/Desktop/College%20Ayush/IOT/IOT%20PROJECT/docs/system_architecture_guide/02_DIRECTORY_AND_FILE_WALKTHROUGH.md)
* Comprehensive breakdown of all directories and files.
* Senior Developer design rationale: why each component exists and how it connects to the system.
* Key automotive design decisions (ground footpoints, ByteTrack association, H.264 browser streaming, and simulation ground-truth separation).

### 3. [Phase 2 Guide: Motion, Geometry & Time-To-Collision (TTC)](file:///c:/Users/astro/Desktop/College%20Ayush/IOT/IOT%20PROJECT/docs/system_architecture_guide/03_PHASE_2_MOTION_AND_GEOMETRY.md)
* How monocular pinhole camera geometry works (height, pitch, focal length).
* Why the bottom-center footpoint $(foot_x, foot_y)$ is used for ground contact ($Z=0$).
* Why naively dividing distance by speed is dangerous and how 2D door corridor intersection works.
* Alpha-Beta velocity filtering and forward trajectory prediction.

### 4. [Phase 1 Technical Specification & Verification Summary](file:///c:/Users/astro/Desktop/College%20Ayush/IOT/IOT%20PROJECT/docs/PHASE_1_SUMMARY.md)
* Technical details, API schemas, test results, and command references for Phase 1.
