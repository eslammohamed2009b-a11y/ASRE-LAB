"""
Real external Supabase Storage test (`SupabaseStorage`).

This is intentionally NOT run against a mock - it requires live
`SUPABASE_URL` + `SUPABASE_KEY` credentials pointed at a real Supabase
project with a storage bucket matching `settings.SUPABASE_STORAGE_BUCKET`
already created. When those credentials are not present in the environment,
the test is skipped (never marked "passed") with an explicit reason, per
the project's rule against fabricating or implying evidence for anything
that was not actually executed.

Required environment for this test to actually run:
- SUPABASE_URL
- SUPABASE_KEY  (a key permitted to upload/download/delete objects in the
  configured storage bucket)

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
def test_supabase_storage_upload_download_delete_roundtrip(tmp_path):
    from app.core.config import settings
    from app.core.persistence import persistence_service
    from app.core.storage import SupabaseStorage, build_object_key

    assert persistence_service.enabled, "Supabase client did not initialize despite configured credentials"
    storage = SupabaseStorage(persistence_service.client, settings.SUPABASE_STORAGE_BUCKET)

    user_id = f"external-storage-user-{uuid.uuid4()}"
    experiment_id = str(uuid.uuid4())
    design_id = str(uuid.uuid4())
    object_key = build_object_key(user_id, experiment_id, design_id, "roundtrip.stl")

    payload = b"external supabase storage roundtrip payload"
    source_path = tmp_path / "roundtrip.stl"
    source_path.write_bytes(payload)

    try:
        storage.save_file(object_key, source_path)

        assert storage.file_exists(object_key)

        downloaded = storage.open_bytes(object_key)
        assert downloaded == payload

        checksum = storage.calculate_checksum(source_path)
        assert len(checksum) == 64  # sha256 hex digest length
    finally:
        # Cleanup: never leave residue in the live bucket.
        storage.delete_file(object_key)
        assert not storage.file_exists(object_key)


@pytest.mark.skipif(not _HAS_LIVE_SUPABASE, reason=skip_reason)
def test_supabase_storage_missing_object_raises():
    from app.core.config import settings
    from app.core.persistence import persistence_service
    from app.core.storage import SupabaseStorage, StorageError, build_object_key

    assert persistence_service.enabled, "Supabase client did not initialize despite configured credentials"
    storage = SupabaseStorage(persistence_service.client, settings.SUPABASE_STORAGE_BUCKET)

    user_id = f"external-storage-user-{uuid.uuid4()}"
    experiment_id = str(uuid.uuid4())
    design_id = str(uuid.uuid4())
    object_key = build_object_key(user_id, experiment_id, design_id, "does-not-exist.stl")

    assert not storage.file_exists(object_key)
    with pytest.raises(StorageError):
        storage.open_bytes(object_key)
