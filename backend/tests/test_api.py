"""Integration tests for FastAPI endpoints."""

import pytest
from starlette.testclient import TestClient
from main import app

client = TestClient(app)


def test_api_health():
    """Verify health endpoint returns ONLINE status and model details."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ONLINE"
    assert data["model_loaded"] is True
    assert "yolo26n.pt" in data["model_name"]
    assert "person" in data["target_classes"]
    assert "bicycle" in data["target_classes"]


def test_api_list_samples():
    """Verify sample videos are listed."""
    response = client.get("/api/video/samples")
    assert response.status_code == 200
    samples = response.json()
    assert isinstance(samples, list)
    assert len(samples) > 0
    filenames = [s["filename"] for s in samples]
    assert any("01_012-3.mp4" in f or "01_034-3.mp4" in f for f in filenames)


def test_api_root_serves_html():
    """Verify root / serves the HTML web dashboard."""
    response = client.get("/")
    assert response.status_code == 200
    assert "DOORSAFE" in response.text
    assert "Perception" in response.text


def test_api_process_job_lifecycle():
    """Verify job creation and polling."""
    # List samples to get valid filename
    samples = client.get("/api/video/samples").json()
    sample_file = samples[0]["filename"]

    # Start processing
    req_payload = {
        "source_type": "SAMPLE",
        "sample_filename": sample_file,
        "conf_threshold": 0.35,
        "stride": 2
    }
    create_res = client.post("/api/video/process", json=req_payload)
    assert create_res.status_code == 200
    job = create_res.json()
    job_id = job["job_id"]
    assert job["status"] in ["PENDING", "PROCESSING"]

    # Query status
    status_res = client.get(f"/api/video/jobs/{job_id}")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["job_id"] == job_id


def test_api_calibration():
    """Verify camera calibration and projection endpoints."""
    # Test get calibration
    res = client.get("/api/calibration/camera")
    assert res.status_code == 200
    data = res.json()
    assert "camera_height_m" in data
    assert "pitch_deg" in data

    # Test project pixel to ground
    proj_res = client.post("/api/calibration/project", json={"pixel_u": 960, "pixel_v": 850})
    assert proj_res.status_code == 200
    coord = proj_res.json()
    assert "x_m" in coord
    assert "y_m" in coord
    assert "distance_m" in coord
    assert coord["distance_m"] > 0.0
