from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
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
    assert first["founding_user_number"] == second["founding_user_number"] == 1


def test_concurrent_allocation_is_unique_and_ordered(account_db: Path) -> None:
    with ThreadPoolExecutor(max_workers=16) as pool:
        rows = list(pool.map(
            lambda index: account_router._provision_account(
                f"user-{index}", f"user-{index}@example.test"
            ),
            range(80),
        ))
    assert sorted(row["founding_user_number"] for row in rows) == list(range(1, 81))


def test_deleted_ordinal_is_not_reused(account_db: Path) -> None:
    first = account_router._provision_account("user-a", None)
    with sqlite3.connect(account_db) as db:
        db.execute("delete from asre_accounts where user_id='user-a'")
    second = account_router._provision_account("user-b", None)
    assert first["founding_user_number"] == 1
    assert second["founding_user_number"] == 2


def test_no_ordinal_exceeds_one_thousand(account_db: Path) -> None:
    rows = [account_router._provision_account(f"user-{index}", None) for index in range(1002)]
    assigned = [row["founding_user_number"] for row in rows if row["founding_user_number"]]
    assert assigned == list(range(1, 1001))
    assert rows[-1]["founding_user_number"] is None
    assert rows[-1]["usage_access"] == "standard"


def test_sql_migration_locks_and_denies_entitlement_writes() -> None:
    root = Path(__file__).resolve().parents[3]
    source = root / "database" / "migrations" / "013_accounts_and_founders.sql"
    mirror = root / "backend" / "supabase" / "migrations" / "20260730010000_accounts_and_founders.sql"
    assert source.read_bytes() == mirror.read_bytes()
    sql = source.read_text().lower()
    for token in (
        "pg_advisory_xact_lock",
        "insert into public.profiles(id)",
        "no cycle",
        "if allocated > 1000 then allocated := null",
        "on conflict (user_id) do nothing",
        "revoke all on public.asre_accounts from anon, authenticated",
        "grant select on public.asre_accounts to authenticated",
        "revoke all on sequence public.asre_founding_user_ordinal_seq",
        "using (user_id = auth.uid())",
    ):
        assert token in sql
