"""Phase 4 end-to-end proof: the real FastAPI app, started as a live server
process and driven purely over real HTTP (not FastAPI's in-process
TestClient), exercising `/health`, `/openapi.json`, and the real
`/api/design/generate-single` endpoint end to end with a real JWT and the
real CadQuery kernel doing the geometry generation.
"""
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

JWT_SECRET = "e2e-test-secret-do-not-use-in-prod"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    port = _free_port()
    env = os.environ.copy()
    env["JWT_SECRET_KEY"] = JWT_SECRET
    env["ENV"] = "test"
    env["ANTHROPIC_API_KEY"] = ""  # force the deterministic nl_parser fallback, no live LLM calls

    proc = subprocess.Popen(
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
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"uvicorn process exited early (code={proc.returncode}):\n{output}")
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1.0)
            if resp.status_code == 200:
                break
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.25)
    else:
        proc.terminate()
        raise RuntimeError(f"Server did not become ready in time: {last_error}")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _make_token() -> str:
    return jwt.encode({"sub": "e2e-user"}, JWT_SECRET, algorithm="HS256")


def test_health_endpoint_over_real_http(live_server):
    resp = httpx.get(f"{live_server}/health", timeout=5.0)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_openapi_schema_is_served(live_server):
    resp = httpx.get(f"{live_server}/openapi.json", timeout=5.0)
    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/design/generate-single" in schema["paths"]


def test_generate_single_over_real_http_produces_real_cad_files(live_server):
    token = _make_token()
    started = time.perf_counter()
    resp = httpx.post(
        f"{live_server}/api/design/generate-single",
        json={"prompt": "A pyramid with a height of 90 meters"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    duration = time.perf_counter() - started

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "design_id" in payload
    assert "stl_object_key" in payload
    assert "step_object_key" in payload

    download = httpx.get(
        f"{live_server}/api/design/export/{payload['design_id']}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert download.status_code == 200, download.text
    assert len(download.content) > 0

    print(
        f"[e2e evidence] status={resp.status_code} duration={duration:.3f}s "
        f"stl_object_key={payload['stl_object_key']} "
        f"step_object_key={payload['step_object_key']} "
        f"downloaded_stl_bytes={len(download.content)}"
    )


def test_generate_single_requires_auth(live_server):
    resp = httpx.post(
        f"{live_server}/api/design/generate-single",
        json={"prompt": "A pyramid with a height of 90 meters"},
        timeout=5.0,
    )
    assert resp.status_code == 401
