from __future__ import annotations

import pytest

from app.core.config import settings
from app.main import validate_startup_environment


pytestmark = pytest.mark.unit


def _production_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", ["https://app.example.test"])
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_KEY", "server-only-key")
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", "legacy-hs256-secret")
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "")


def test_production_startup_rejects_development_redis_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_settings(monkeypatch)
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    with pytest.raises(RuntimeError, match="CELERY_BROKER_URL"):
        validate_startup_environment()


def test_production_startup_accepts_configured_durable_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production_settings(monkeypatch)
    monkeypatch.setattr(settings, "CELERY_BROKER_URL", "redis://redis.example.test:6379/0")
    monkeypatch.setattr(settings, "CELERY_RESULT_BACKEND", "redis://redis.example.test:6379/1")

    validate_startup_environment()
