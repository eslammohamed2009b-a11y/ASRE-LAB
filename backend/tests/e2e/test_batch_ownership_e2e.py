"""Real-HTTP Module 1 batch-generation persistence and isolation proof.

This starts uvicorn in a subprocess, drives only HTTP endpoints, executes the
real CadQuery path through Celery eager mode, restarts uvicorn against the same
SQLite database and local object-storage root, and proves two-user isolation.
Eager mode is deterministic test execution, not evidence of a live Redis broker
and separate Celery worker.
"""
import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from jose import jwt

pytestmark = pytest.mark.e2e

JWT_SECRET = "e2e-batch-test-secret-do-not-use-in-prod"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_server(port: int, db_path: Path, storage_root: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "JWT_SECRET_KEY": JWT_SECRET,
            "ENV": "test",
            "ANTHROPIC_API_KEY": "",
            "CELERY_TASK_ALWAYS_EAGER": "true",
            "LOCAL_PERSISTENCE_DB_PATH": str(db_path),
            "LOCAL_STORAGE_ROOT": str(storage_root),
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    last_error = None
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"uvicorn exited early (code={process.returncode}):\n{output}")
        try:
            response = httpx.get(f"{base_url}/health", timeout=1.0)
            if response.status_code == 200:
                return process
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.25)
    process.terminate()
    raise RuntimeError(f"Server did not become ready in time: {last_error}")


def _stop_server(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _auth_headers(user_id: str) -> dict[str, str]:
    token = jwt.encode({"sub": user_id}, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_batch_generation_survives_restart_and_isolates_users_over_real_http(tmp_path):
    db_path = tmp_path / "module1-e2e.db"
    storage_root = tmp_path / "design-files"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    user_a_headers = _auth_headers("e2e-batch-user-a")
    user_b_headers = _auth_headers("e2e-batch-user-b")

    process = _start_server(port, db_path, storage_root)
    try:
        create_response = httpx.post(
            f"{base_url}/api/design/generate-batch",
            json={
                "base_params": {"geometry_type": "pyramid", "height_m": 45},
                "variation_count": 2,
                "vary_fields": ["height_m"],
                "variation_range_pct": 0.1,
            },
            headers=user_a_headers,
            timeout=60.0,
        )
        assert create_response.status_code == 202, create_response.text
        job_id = create_response.json()["job_id"]

        status_response = httpx.get(
            f"{base_url}/api/jobs/{job_id}", headers=user_a_headers, timeout=10.0
        )
        assert status_response.status_code == 200, status_response.text
        assert status_response.json()["status"] == "completed"
        assert status_response.json()["completed_count"] == 2
        assert status_response.json()["progress_percent"] == 100

        results_response = httpx.get(
            f"{base_url}/api/jobs/{job_id}/results", headers=user_a_headers, timeout=10.0
        )
        assert results_response.status_code == 200, results_response.text
        designs = results_response.json()["designs"]
        assert len(designs) == 2

        first_design = designs[0]
        stl_file = next(file for file in first_design["files"] if file["file_format"] == "stl")
        step_file = next(file for file in first_design["files"] if file["file_format"] == "step")
        assert stl_file["file_size_bytes"] > 0
        assert step_file["file_size_bytes"] > 500
        assert len(stl_file["checksum_sha256"]) == 64
        assert len(step_file["checksum_sha256"]) == 64

        download_response = httpx.get(
            f"{base_url}/api/design/export/{stl_file['design_file_id']}",
            headers=user_a_headers,
            timeout=10.0,
        )
        assert download_response.status_code == 200, download_response.text
        assert len(download_response.content) == stl_file["file_size_bytes"]
        assert hashlib.sha256(download_response.content).hexdigest() == stl_file["checksum_sha256"]
    finally:
        _stop_server(process)

    # Reconstruct every process-local adapter by restarting uvicorn while
    # retaining only the durable SQLite file and object-storage directory.
    process = _start_server(port, db_path, storage_root)
    try:
        restarted_status = httpx.get(
            f"{base_url}/api/jobs/{job_id}", headers=user_a_headers, timeout=10.0
        )
        assert restarted_status.status_code == 200
        assert restarted_status.json()["status"] == "completed"

        restarted_download = httpx.get(
            f"{base_url}/api/design/export/{stl_file['design_file_id']}",
            headers=user_a_headers,
            timeout=10.0,
        )
        assert restarted_download.status_code == 200
        assert hashlib.sha256(restarted_download.content).hexdigest() == stl_file["checksum_sha256"]

        assert httpx.get(
            f"{base_url}/api/jobs/{job_id}", headers=user_b_headers, timeout=10.0
        ).status_code == 404
        assert httpx.get(
            f"{base_url}/api/jobs/{job_id}/results", headers=user_b_headers, timeout=10.0
        ).status_code == 404
        assert httpx.get(
            f"{base_url}/api/design/export/{stl_file['design_file_id']}",
            headers=user_b_headers,
            timeout=10.0,
        ).status_code == 404
    finally:
        _stop_server(process)
