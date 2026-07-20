"""
Unit tests for the FileStorage abstraction (`app.core.storage`).

Exercises `LocalFileStorage` and the shared `build_object_key` /
`validate_object_key` safety checks in isolation, with no CadQuery/FastAPI
dependency. `SupabaseStorage` is exercised separately in
`tests/external/test_supabase_storage_live.py` (skipped without live
credentials).
"""
import pytest

from app.core.storage import (
    LocalFileStorage,
    StorageError,
    build_object_key,
    sha256_of_file,
)

pytestmark = pytest.mark.unit


def test_build_object_key_is_namespaced_per_user():
    key = build_object_key("user-a", "exp-1", "design-1", "design-1.stl")
    assert key == "users/user-a/experiments/exp-1/designs/design-1/design-1.stl"


def test_build_object_key_sanitizes_unsafe_filename_characters():
    key = build_object_key("user-a", "exp-1", "design-1", "../../etc/passwd")
    assert ".." not in key
    assert "/" not in key.rsplit("/", 1)[1]


def test_save_and_open_roundtrip(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    source = tmp_path / "scratch.stl"
    source.write_bytes(b"solid test-geometry")

    key = build_object_key("user-a", "exp-1", "design-1", "design-1.stl")
    storage.save_file(key, source)

    assert storage.file_exists(key)
    assert storage.open_bytes(key) == b"solid test-geometry"


def test_checksum_matches_sha256_of_source_file(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    source = tmp_path / "scratch.stl"
    source.write_bytes(b"deterministic-content")

    assert storage.calculate_checksum(source) == sha256_of_file(source)


def test_missing_object_raises_storage_error(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    key = build_object_key("user-a", "exp-1", "design-1", "design-1.stl")
    with pytest.raises(StorageError):
        storage.open_bytes(key)


def test_file_exists_is_false_for_missing_object(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    key = build_object_key("user-a", "exp-1", "design-1", "design-1.stl")
    assert storage.file_exists(key) is False


def test_path_traversal_object_key_is_rejected(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    with pytest.raises(StorageError):
        storage.open_bytes("users/user-a/experiments/e/designs/d/../../../etc/passwd")


def test_absolute_path_object_key_is_rejected(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    with pytest.raises(StorageError):
        storage.open_bytes("/etc/passwd")


def test_malformed_object_key_missing_prefix_is_rejected(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    with pytest.raises(StorageError):
        storage.open_bytes("not-a-namespaced-key.stl")


def test_delete_is_best_effort_and_does_not_raise_on_missing(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    key = build_object_key("user-a", "exp-1", "design-1", "design-1.stl")
    storage.delete_file(key)  # must not raise even though nothing was ever saved


def test_delete_removes_saved_file(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    source = tmp_path / "scratch.stl"
    source.write_bytes(b"data")
    key = build_object_key("user-a", "exp-1", "design-1", "design-1.stl")
    storage.save_file(key, source)
    assert storage.file_exists(key)

    storage.delete_file(key)
    assert storage.file_exists(key) is False


def test_duplicate_filenames_across_different_designs_do_not_collide(tmp_path):
    storage = LocalFileStorage(tmp_path / "storage-root")
    source_a = tmp_path / "a.stl"
    source_a.write_bytes(b"design-a-bytes")
    source_b = tmp_path / "b.stl"
    source_b.write_bytes(b"design-b-bytes")

    key_a = build_object_key("user-a", "exp-1", "design-a", "model.stl")
    key_b = build_object_key("user-a", "exp-1", "design-b", "model.stl")
    storage.save_file(key_a, source_a)
    storage.save_file(key_b, source_b)

    assert storage.open_bytes(key_a) == b"design-a-bytes"
    assert storage.open_bytes(key_b) == b"design-b-bytes"


def test_restart_using_same_local_storage_root_still_retrievable(tmp_path):
    root = tmp_path / "storage-root"
    storage_1 = LocalFileStorage(root)
    source = tmp_path / "scratch.stl"
    source.write_bytes(b"persisted-across-restart")
    key = build_object_key("user-a", "exp-1", "design-1", "design-1.stl")
    storage_1.save_file(key, source)
    del storage_1  # simulate process restart

    storage_2 = LocalFileStorage(root)  # brand new instance, same root dir
    assert storage_2.file_exists(key)
    assert storage_2.open_bytes(key) == b"persisted-across-restart"
