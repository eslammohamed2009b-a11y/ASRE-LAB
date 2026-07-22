"""Live Migration 009 schema and owner-RLS release gate.

This test deliberately requires a disposable/staging project, a service-role key,
and JWTs for two existing test users. Missing configuration is a blocked skip.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.external

_NAMES = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
    "SUPABASE_TEST_USER_A_ID",
    "SUPABASE_TEST_USER_A_JWT",
    "SUPABASE_TEST_USER_B_ID",
    "SUPABASE_TEST_USER_B_JWT",
)
_READY = all(os.environ.get(name) for name in _NAMES)


def _authenticated_client(url: str, anon_key: str, jwt: str):
    from supabase import create_client

    client = create_client(url, anon_key)
    client.postgrest.auth(jwt)
    return client


@pytest.mark.skipif(
    not _READY,
    reason="BLOCKED: live Migration 009/RLS test credentials are not configured; not passing evidence.",
)
def test_migration_009_schema_persistence_and_owner_rls() -> None:
    from postgrest.exceptions import APIError
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    owner_a = str(uuid.UUID(os.environ["SUPABASE_TEST_USER_A_ID"]))
    owner_b = str(uuid.UUID(os.environ["SUPABASE_TEST_USER_B_ID"]))
    service = create_client(url, os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    client_a = _authenticated_client(url, os.environ["SUPABASE_ANON_KEY"], os.environ["SUPABASE_TEST_USER_A_JWT"])
    client_b = _authenticated_client(url, os.environ["SUPABASE_ANON_KEY"], os.environ["SUPABASE_TEST_USER_B_JWT"])
    experiment_id: str | None = None

    try:
        service.table("profiles").upsert({"id": owner_a, "full_name": "ASRE release gate A"}).execute()
        service.table("profiles").upsert({"id": owner_b, "full_name": "ASRE release gate B"}).execute()
        experiment = client_a.table("experiments").insert(
            {"user_id": owner_a, "name": "Migration 009 release gate"}
        ).execute().data[0]
        experiment_id = experiment["id"]
        analysis_id = str(uuid.uuid4())
        record = {
            "id": analysis_id,
            "experiment_id": experiment_id,
            "user_id": owner_a,
            "analysis_type": "release_gate",
            "status": "completed",
            "dataset_hash": "a" * 64,
            "configuration": {"source": "live-release-gate"},
            "result": {"verified": True},
            "warnings": [],
            "source_design_ids": [],
            "source_simulation_ids": [],
            "data_quality": {},
            "engine_version": "release-gate",
            "reproducibility_hash": "b" * 64,
        }
        assert client_a.table("experiment_analyses").insert(record).execute().data[0]["id"] == analysis_id

        # A new authenticated client proves persistence; the second owner must see no row.
        client_a_fresh = _authenticated_client(
            url, os.environ["SUPABASE_ANON_KEY"], os.environ["SUPABASE_TEST_USER_A_JWT"]
        )
        assert client_a_fresh.table("experiment_analyses").select("id").eq("id", analysis_id).execute().data
        assert client_b.table("experiment_analyses").select("id").eq("id", analysis_id).execute().data == []

        with pytest.raises(APIError):
            client_b.table("experiment_analyses").insert(
                {**record, "id": str(uuid.uuid4()), "user_id": owner_a}
            ).execute()
    finally:
        if experiment_id is not None:
            service.table("experiments").delete().eq("id", experiment_id).execute()
