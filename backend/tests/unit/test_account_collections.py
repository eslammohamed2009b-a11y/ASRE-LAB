from __future__ import annotations

from pathlib import Path

import pytest

from app.v2 import account_router
from app.v2.repository import EvidenceRepository

pytestmark = pytest.mark.unit


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EvidenceRepository:
    repo = EvidenceRepository(str(tmp_path / "collections.db"))
    monkeypatch.setattr(account_router, "EvidenceRepository", lambda: repo)
    return repo


def _record(repo: EvidenceRepository, user: str, kind: str, index: int):
    return repo.create(user, {
        "record_type": kind,
        "status": "completed",
        "experiment_id": None,
        "simulation_id": None,
        "parent_record_id": None,
        "payload": {"index": index},
    })


def test_collection_is_owner_scoped_and_paginated(repository: EvidenceRepository) -> None:
    for index in range(5):
        _record(repository, "owner-a", "research_report", index)
        _record(repository, "owner-b", "research_report", index)
    first = account_router._collection("research_report", "owner-a", 2, 0)
    second = account_router._collection("research_report", "owner-a", 2, 2)
    assert len(first.items) == len(second.items) == 2
    assert first.has_more is True
    assert all(item["user_id"] == "owner-a" for item in first.items + second.items)
    assert {item["id"] for item in first.items}.isdisjoint(item["id"] for item in second.items)


def test_collection_empty_state_does_not_disclose_other_owner(repository: EvidenceRepository) -> None:
    _record(repository, "owner-b", "engineering_decision", 1)
    result = account_router._collection("engineering_decision", "owner-a", 20, 0)
    assert result.items == []
    assert result.has_more is False


def test_repository_sort_is_deterministic(repository: EvidenceRepository) -> None:
    for index in range(4):
        _record(repository, "owner-a", "reasoning_event", index)
    rows = repository.list_page("owner-a", "reasoning_event", 10, 0)
    sort_keys = [(row["created_at"], row["id"]) for row in rows]
    assert sort_keys == sorted(sort_keys, reverse=True)
