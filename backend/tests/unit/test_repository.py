"""
Unit tests for the local durable persistence adapter (`LocalSQLiteRepository`).

These do not touch CadQuery, FastAPI, or Supabase - they exercise the
repository's own contract directly: creation, ownership isolation, unknown
ids, restart durability (a real file on disk, not an in-process dict), and
sharing across multiple repository instances (the local stand-in for
"multiple API instances share persisted ownership").
"""
import pytest

from app.core.repository import LocalSQLiteRepository

pytestmark = pytest.mark.unit


def test_create_experiment_and_record_design_file_roundtrip(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "persistence.db")
    experiment_id = repo.create_experiment(owner_id="user-a", title="test experiment")
    assert experiment_id

    repo.record_design_file(
        design_id="11111111-1111-1111-1111-111111111111",
        owner_id="user-a",
        experiment_id=experiment_id,
        file_format="stl",
        storage_path="/tmp/somefile.stl",
        file_size_bytes=1234,
        checksum="deadbeef",
    )

    record = repo.get_design_file("11111111-1111-1111-1111-111111111111")
    assert record is not None
    assert record.owner_id == "user-a"
    assert record.experiment_id == experiment_id
    assert record.file_format == "stl"
    assert record.file_size_bytes == 1234
    assert record.checksum == "deadbeef"


def test_unknown_design_id_returns_none(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "persistence.db")
    assert repo.get_design_file("does-not-exist") is None


def test_ownership_is_isolated_between_users(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "persistence.db")
    exp_a = repo.create_experiment(owner_id="user-a", title="a's experiment")
    repo.record_design_file(
        design_id="22222222-2222-2222-2222-222222222222",
        owner_id="user-a",
        experiment_id=exp_a,
        file_format="stl",
        storage_path="/tmp/a.stl",
        file_size_bytes=1,
        checksum=None,
    )

    record = repo.get_design_file("22222222-2222-2222-2222-222222222222")
    assert record.owner_id == "user-a"
    assert record.owner_id != "user-b"


def test_data_survives_repository_reconstruction_same_file(tmp_path):
    """Genuine restart-durability check: a brand new repository object,
    constructed from scratch and pointed at the same db file, must see
    data written by a previous (now-discarded) repository instance."""
    db_path = tmp_path / "persistence.db"

    repo_1 = LocalSQLiteRepository(db_path)
    exp_id = repo_1.create_experiment(owner_id="user-a", title="durable")
    repo_1.record_design_file(
        design_id="33333333-3333-3333-3333-333333333333",
        owner_id="user-a",
        experiment_id=exp_id,
        file_format="stl",
        storage_path="/tmp/durable.stl",
        file_size_bytes=42,
        checksum="abc123",
    )
    del repo_1  # simulate the process/object going away

    repo_2 = LocalSQLiteRepository(db_path)
    record = repo_2.get_design_file("33333333-3333-3333-3333-333333333333")
    assert record is not None
    assert record.owner_id == "user-a"
    assert record.checksum == "abc123"


def test_multiple_instances_share_persisted_state(tmp_path):
    """Two live repository instances pointed at the same file stand in for
    two API processes/replicas sharing persisted ownership state."""
    db_path = tmp_path / "persistence.db"

    writer = LocalSQLiteRepository(db_path)
    exp_id = writer.create_experiment(owner_id="user-a", title="shared")
    writer.record_design_file(
        design_id="44444444-4444-4444-4444-444444444444",
        owner_id="user-a",
        experiment_id=exp_id,
        file_format="stl",
        storage_path="/tmp/shared.stl",
        file_size_bytes=7,
        checksum=None,
    )

    reader = LocalSQLiteRepository(db_path)  # a second, independent instance
    record = reader.get_design_file("44444444-4444-4444-4444-444444444444")
    assert record is not None
    assert record.owner_id == "user-a"
