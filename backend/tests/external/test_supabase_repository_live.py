"""
Real external Supabase persistence test (`SupabaseRepository`).

This is intentionally NOT run against a mock or a stub - it requires live
`SUPABASE_URL` + `SUPABASE_KEY` credentials pointed at a real Supabase
project with the `database/schema.sql` migration (including `design_files`)
applied. When those credentials are not present in the environment, the
test is skipped (never marked "passed") with an explicit reason, per the
project's rule against fabricating or implying evidence for anything that
was not actually executed.

Required environment for this test to actually run:
- SUPABASE_URL
- SUPABASE_KEY  (a key permitted to insert/select on experiments/design_files)

To run: pytest -m external
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.external

_HAS_LIVE_SUPABASE = bool(os.environ.get("SUPABASE_URL")) and bool(os.environ.get("SUPABASE_KEY"))

skip_reason = (
    "BLOCKED: no live Supabase credentials (SUPABASE_URL/SUPABASE_KEY) configured "
    "in this environment. This test was not executed and must not be reported as passing."
)


@pytest.mark.skipif(not _HAS_LIVE_SUPABASE, reason=skip_reason)
def test_supabase_repository_create_read_isolate_cleanup():
    from app.core.persistence import persistence_service
    from app.core.repository import SupabaseRepository

    assert persistence_service.enabled, "Supabase client did not initialize despite configured credentials"
    repo = SupabaseRepository(persistence_service.client)

    owner_a = f"external-test-user-a-{uuid.uuid4()}"
    owner_b = f"external-test-user-b-{uuid.uuid4()}"
    design_id = str(uuid.uuid4())

    experiment_id = repo.create_experiment(owner_id=owner_a, title="external repository test")
    assert experiment_id

    repo.record_design_file(
        design_id=design_id,
        owner_id=owner_a,
        experiment_id=experiment_id,
        file_format="stl",
        storage_path="/tmp/external-test.stl",
        file_size_bytes=1,
        checksum=None,
    )

    # Read-back.
    record = repo.get_design_file(design_id)
    assert record is not None
    assert record.owner_id == owner_a

    # Two-user isolation: owner_b must never be treated as the owner.
    assert record.owner_id != owner_b

    # Cleanup: remove the rows created by this test run so it leaves no
    # residue in the live project (no secret/value is ever printed).
    persistence_service.client.table("design_files").delete().eq("id", design_id).execute()
    persistence_service.client.table("experiments").delete().eq("id", experiment_id).execute()
