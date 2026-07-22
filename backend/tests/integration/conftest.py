"""Fixtures for integration tests.

Integration tests exercise the real FastAPI app in-process via TestClient.
Unlike unit tests, they must NEVER run against a stubbed `cadquery` module.
This conftest fails the whole integration session fast (collection error)
if the real CadQuery/OCP native kernel is not importable, so a green run
can never be silently backed by a mock.
"""
from collections.abc import Generator

import pytest

try:
    from app.core import native_runtime as _native_runtime  # noqa: F401
    import cadquery  # noqa: F401
    from cadquery import Workplane  # noqa: F401
except ImportError as exc:  # pragma: no cover - fail-fast path
    pytest.exit(
        "Integration tests require the REAL `cadquery` package (with working "
        "OCP native bindings). It is not importable in this environment "
        f"({exc!r}). Integration tests must not run against a stub. Install "
        "the real dependency stack (see backend/TESTING.md) before running "
        "`pytest -m integration`.",
        returncode=1,
    )

from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authorized_client() -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-test", "role": "researcher"}
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
