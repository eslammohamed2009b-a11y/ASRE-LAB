from __future__ import annotations

from pathlib import Path

import pytest

from app.v2 import account_router

pytestmark = pytest.mark.unit


@pytest.fixture
def account_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "accounts.db"
    monkeypatch.setattr(account_router.persistence_service, "enabled", False)
    monkeypatch.setattr(account_router, "default_local_db_path", lambda: path)
    return path


def test_provisioning_is_idempotent(account_db: Path) -> None:
    first = account_router._provision_account("user-a", "a@example.test")
    second = account_router._provision_account("user-a", "a@example.test")
    assert first["user_id"] == second["user_id"] == "user-a"
    assert first["created_at"] == second["created_at"]


def test_local_provisioning_keeps_legacy_columns_inert(account_db: Path) -> None:
    row = account_router._provision_account("user-a", None)
    assert row["founding_user_number"] is None
    assert row["usage_access"] == row["usage_access_period"] == "standard"


def test_account_response_excludes_retired_entitlement_fields(account_db: Path) -> None:
    response = account_router._account_response({"id": "user-a", "email": "a@example.test"})
    assert set(response.model_dump()) == {"user_id", "email", "created_at", "updated_at"}


def test_retirement_migration_is_mirrored_and_preserves_owner_scoping() -> None:
    root = Path(__file__).resolve().parents[3]
    source = root / "database" / "migrations" / "017_retire_founding_user_program.sql"
    mirror = root / "backend" / "supabase" / "migrations" / "20261031010000_retire_founding_user_program.sql"
    assert source.read_bytes() == mirror.read_bytes()
    sql = source.read_text().lower()
    for token in (
        "insert into public.profiles(id)",
        "on conflict (user_id) do nothing",
        "revoke all on public.asre_accounts from anon, authenticated",
        "using (user_id = auth.uid())",
        "founding_user_number = null",
        "usage_access = 'standard'",
        "usage_access_period = 'standard'",
    ):
        assert token in sql
    assert "nextval" not in sql
