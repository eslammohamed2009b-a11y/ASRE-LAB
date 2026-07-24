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
import hashlib
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


@pytest.mark.skipif(
    not all(
        os.environ.get(name)
        for name in (
            "SUPABASE_URL",
            "SUPABASE_KEY",
            "SUPABASE_ANON_KEY",
            "SUPABASE_TEST_USER_A_ID",
            "SUPABASE_TEST_USER_A_JWT",
            "SUPABASE_TEST_USER_B_ID",
            "SUPABASE_TEST_USER_B_JWT",
        )
    ),
    reason="BLOCKED: complete two-user storage credentials are not configured.",
)
def test_protected_storage_owner_isolation_checksum_and_corruption(tmp_path):
    import httpx

    from app.core.config import settings
    from app.core.persistence import persistence_service
    from app.core.storage import SupabaseStorage, build_object_key
    from supabase import create_client

    owner_a = str(uuid.UUID(os.environ["SUPABASE_TEST_USER_A_ID"]))
    owner_b = str(uuid.UUID(os.environ["SUPABASE_TEST_USER_B_ID"]))
    service = persistence_service.client
    storage = SupabaseStorage(service, settings.SUPABASE_STORAGE_BUCKET)
    service.table("profiles").upsert({"id": owner_a, "full_name": "ASRE storage gate A"}).execute()
    service.table("profiles").upsert({"id": owner_b, "full_name": "ASRE storage gate B"}).execute()

    experiment_id = None
    object_key = None
    corrupted_key = None
    try:
        experiment = service.table("experiments").insert(
            {"user_id": owner_a, "name": "protected storage live gate"}
        ).execute().data[0]
        experiment_id = experiment["id"]
        model = service.table("design_models").insert(
            {
                "experiment_id": experiment_id,
                "user_id": owner_a,
                "geometry_family": "pyramid",
                "variation_index": 0,
            }
        ).execute().data[0]
        object_key = build_object_key(owner_a, experiment_id, model["id"], "integrity.stl")
        original = b"authoritative staging artifact"
        source = tmp_path / "integrity.stl"
        source.write_bytes(original)
        expected_checksum = hashlib.sha256(original).hexdigest()
        storage.save_file(object_key, source)
        file_row = service.table("design_files").insert(
            {
                "design_model_id": model["id"],
                "experiment_id": experiment_id,
                "user_id": owner_a,
                "file_format": "stl",
                "storage_provider": "supabase",
                "object_key": object_key,
                "file_size_bytes": len(original),
                "checksum_sha256": expected_checksum,
                "media_type": "model/stl",
            }
        ).execute().data[0]

        def authenticated(jwt: str):
            client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
            client.postgrest.auth(jwt)
            return client

        assert authenticated(os.environ["SUPABASE_TEST_USER_A_JWT"]).table(
            "design_files"
        ).select("*").eq("id", file_row["id"]).execute().data
        assert authenticated(os.environ["SUPABASE_TEST_USER_B_JWT"]).table(
            "design_files"
        ).select("*").eq("id", file_row["id"]).execute().data == []

        for jwt in (
            os.environ["SUPABASE_TEST_USER_A_JWT"],
            os.environ["SUPABASE_TEST_USER_B_JWT"],
        ):
            raw = httpx.get(
                f"{os.environ['SUPABASE_URL']}/storage/v1/object/authenticated/{object_key}",
                headers={
                    "apikey": os.environ["SUPABASE_ANON_KEY"],
                    "Authorization": f"Bearer {jwt}",
                },
                timeout=20,
            )
            assert raw.status_code in {400, 401, 403, 404}

        downloaded = storage.open_bytes(object_key)
        assert hashlib.sha256(downloaded).hexdigest() == expected_checksum

        corrupted = b"corrupted staging artifact"
        corrupted_key = build_object_key(
            owner_a, experiment_id, model["id"], "integrity-corrupted.stl"
        )
        service.storage.from_(settings.SUPABASE_STORAGE_BUCKET).upload(
            corrupted_key, corrupted, {"content-type": "model/stl"}
        )
        service.table("design_files").update(
            {"object_key": corrupted_key}
        ).eq("id", file_row["id"]).execute()
        corrupted_download = storage.open_bytes(corrupted_key)
        assert hashlib.sha256(corrupted_download).hexdigest() != expected_checksum
    finally:
        if object_key:
            storage.delete_file(object_key)
        if corrupted_key:
            storage.delete_file(corrupted_key)
        if experiment_id:
            service.table("experiments").delete().eq("id", experiment_id).execute()
