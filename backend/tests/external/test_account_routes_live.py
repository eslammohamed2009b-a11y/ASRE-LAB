"""Live Supabase proof for account entitlements and every owner-scoped collection."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from supabase import create_client

from app.main import app

pytestmark = pytest.mark.external

_REQUIRED = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
    "SUPABASE_TEST_USER_A_ID",
    "SUPABASE_TEST_USER_A_JWT",
    "SUPABASE_TEST_USER_B_ID",
    "SUPABASE_TEST_USER_B_JWT",
)
_READY = all(os.environ.get(name) for name in _REQUIRED)


def _authenticated(jwt: str):
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
    client.postgrest.auth(jwt)
    return client


@pytest.mark.skipif(not _READY, reason="BLOCKED: live Supabase credentials are not configured.")
def test_account_entitlements_and_all_collection_routes_are_owner_scoped() -> None:
    owner_a = str(uuid.UUID(os.environ["SUPABASE_TEST_USER_A_ID"]))
    owner_b = str(uuid.UUID(os.environ["SUPABASE_TEST_USER_B_ID"]))
    jwt_a = os.environ["SUPABASE_TEST_USER_A_JWT"]
    jwt_b = os.environ["SUPABASE_TEST_USER_B_JWT"]
    service = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    user_a = _authenticated(jwt_a)

    created_ids: list[str] = []
    record_types = (
        "run_manifest",
        "job_attempt",
        "engineering_decision",
        "reasoning_event",
        "research_report",
    )
    try:
        for record_type in record_types:
            for owner in (owner_a, owner_b):
                row = (
                    service.table("engineering_evidence_records")
                    .insert(
                        {
                            "user_id": owner,
                            "record_type": record_type,
                            "status": "complete",
                            "schema_version": "2.0",
                            "payload": {"owner_probe": owner},
                            "payload_checksum": uuid.uuid4().hex,
                        }
                    )
                    .execute()
                    .data[0]
                )
                created_ids.append(row["id"])

        client = TestClient(app)
        headers_a = {"Authorization": f"Bearer {jwt_a}"}
        headers_b = {"Authorization": f"Bearer {jwt_b}"}
        routes = (
            "/api/v2/execution/runs",
            "/api/v2/execution/manifests",
            "/api/v2/execution/attempts",
            "/api/v2/decisions",
            "/api/v2/reasoning",
            "/api/v2/reports",
        )
        for route in routes:
            result_a = client.get(route, headers=headers_a)
            result_b = client.get(route, headers=headers_b)
            assert result_a.status_code == 200
            assert result_b.status_code == 200
            assert all(item["user_id"] == owner_a for item in result_a.json()["items"])
            assert all(item["user_id"] == owner_b for item in result_b.json()["items"])
            assert owner_b not in result_a.text
            assert owner_a not in result_b.text

        for headers, owner, other in (
            (headers_a, owner_a, owner_b),
            (headers_b, owner_b, owner_a),
        ):
            account = client.get("/api/v2/account/me", headers=headers)
            dashboard = client.get("/api/v2/dashboard", headers=headers)
            assert account.status_code == 200
            assert account.json()["user_id"] == owner
            assert dashboard.status_code == 200
            assert dashboard.json()["account"]["user_id"] == owner
            assert other not in dashboard.text

        original = (
            service.table("asre_accounts")
            .select("founding_user_number,usage_access,usage_access_period")
            .eq("user_id", owner_a)
            .single()
            .execute()
            .data
        )
        with pytest.raises(Exception):
            (
                user_a.table("asre_accounts")
                .update(
                    {
                        "founding_user_number": 1,
                        "usage_access": "unlimited",
                        "usage_access_period": "early_access",
                    }
                )
                .eq("user_id", owner_a)
                .execute()
            )
        unchanged = (
            service.table("asre_accounts")
            .select("founding_user_number,usage_access,usage_access_period")
            .eq("user_id", owner_a)
            .single()
            .execute()
            .data
        )
        assert unchanged == original

        with pytest.raises(Exception):
            user_a.rpc(
                "provision_asre_account",
                {"requested_user_id": owner_b, "requested_email": "forged@asre.test"},
            ).execute()
    finally:
        if created_ids:
            service.table("engineering_evidence_records").delete().in_("id", created_ids).execute()
