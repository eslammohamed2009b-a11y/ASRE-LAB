"""
Durable persistence and ownership abstraction (Module 1 design files).

Replaces the previous `app.module1_design.ownership_store` in-process dict.
That store worked but was explicitly disclosed as non-durable: it lost all
ownership records on every process restart and could not be shared across
multiple backend replicas (each worker/process had its own private dict).

This module defines a small repository interface (`PersistenceRepository`)
with two real implementations:

- `SupabaseRepository`: production adapter, backed by the `experiments` and
  `design_files` tables (see database/schema.sql). Used whenever Supabase
  credentials are configured (`SUPABASE_URL` + `SUPABASE_KEY`).
- `LocalSQLiteRepository`: deterministic adapter used for local development
  and for the automated test suite when Supabase credentials are not
  available. It is backed by a real SQLite file on disk (not an in-memory
  dict), so it is genuinely durable across process restarts and can be
  shared by multiple repository instances/processes pointed at the same
  database file - the same properties the Supabase adapter has in
  production, without requiring live cloud credentials to test them.

Ownership enforcement happens in this layer's `get_design_file` result
(the caller compares `owner_id` to the requesting user), not by relying on
Supabase Row Level Security alone - `persistence_service` uses a single
shared client without per-request auth binding, so RLS cannot be assumed
to be the enforcing control here.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DesignFileRecord:
    design_id: str
    owner_id: str
    experiment_id: str | None
    file_format: str
    storage_path: str
    file_size_bytes: int | None
    checksum: str | None
    created_at: str


class PersistenceRepository(ABC):
    """Abstract persistence + ownership boundary used by Module 1 routes."""

    @abstractmethod
    def create_experiment(self, owner_id: str, title: str, description: str | None = None) -> str:
        """Create an experiment row and return its id."""

    @abstractmethod
    def record_design_file(
        self,
        design_id: str,
        owner_id: str,
        experiment_id: str | None,
        file_format: str,
        storage_path: str,
        file_size_bytes: int | None,
        checksum: str | None,
    ) -> None:
        """Persist an owned design file record."""

    @abstractmethod
    def get_design_file(self, design_id: str) -> DesignFileRecord | None:
        """Look up a design file record by id, or None if it does not exist."""


class SupabaseRepository(PersistenceRepository):
    """Production adapter backed by Supabase Postgres."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_experiment(self, owner_id: str, title: str, description: str | None = None) -> str:
        payload = {
            "owner_id": owner_id,
            "title": title,
            "description": description,
            "status": "running",
            "created_at": self._ts(),
            "updated_at": self._ts(),
        }
        data = self._client.table("experiments").insert(payload).execute().data
        if not data:
            raise RuntimeError("Supabase did not return an inserted experiment row")
        return data[0]["id"]

    def record_design_file(
        self,
        design_id: str,
        owner_id: str,
        experiment_id: str | None,
        file_format: str,
        storage_path: str,
        file_size_bytes: int | None,
        checksum: str | None,
    ) -> None:
        payload = {
            "id": design_id,
            "user_id": owner_id,
            "experiment_id": experiment_id,
            "design_model_id": None,
            "file_format": file_format,
            "storage_path": storage_path,
            "file_size_bytes": file_size_bytes,
            "checksum": checksum,
            "created_at": self._ts(),
        }
        self._client.table("design_files").insert(payload).execute()

    def get_design_file(self, design_id: str) -> DesignFileRecord | None:
        data = self._client.table("design_files").select("*").eq("id", design_id).execute().data
        if not data:
            return None
        row = data[0]
        return DesignFileRecord(
            design_id=row["id"],
            owner_id=row["user_id"],
            experiment_id=row.get("experiment_id"),
            file_format=row["file_format"],
            storage_path=row["storage_path"],
            file_size_bytes=row.get("file_size_bytes"),
            checksum=row.get("checksum"),
            created_at=row["created_at"],
        )


class LocalSQLiteRepository(PersistenceRepository):
    """
    Deterministic local/test adapter, backed by a real SQLite file.

    Genuinely durable across process restarts (data lives on disk, not in
    a Python dict) and shareable across multiple `LocalSQLiteRepository`
    instances pointed at the same `db_path` - which is how this class
    stands in for "multiple API instances share persisted ownership"
    without needing live cloud infrastructure.
    """

    _init_lock = threading.Lock()

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        with self._init_lock:
            self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS design_files (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    experiment_id TEXT,
                    design_model_id TEXT,
                    file_format TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    file_size_bytes INTEGER,
                    checksum TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_experiment(self, owner_id: str, title: str, description: str | None = None) -> str:
        experiment_id = str(uuid.uuid4())
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO experiments (id, owner_id, title, description, status, created_at) "
                "VALUES (?, ?, ?, ?, 'running', ?)",
                (experiment_id, owner_id, title, description, self._ts()),
            )
            conn.commit()
        finally:
            conn.close()
        return experiment_id

    def record_design_file(
        self,
        design_id: str,
        owner_id: str,
        experiment_id: str | None,
        file_format: str,
        storage_path: str,
        file_size_bytes: int | None,
        checksum: str | None,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO design_files "
                "(id, user_id, experiment_id, design_model_id, file_format, storage_path, "
                "file_size_bytes, checksum, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    design_id,
                    owner_id,
                    experiment_id,
                    file_format,
                    storage_path,
                    file_size_bytes,
                    checksum,
                    self._ts(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_design_file(self, design_id: str) -> DesignFileRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM design_files WHERE id = ?", (design_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return DesignFileRecord(
            design_id=row["id"],
            owner_id=row["user_id"],
            experiment_id=row["experiment_id"],
            file_format=row["file_format"],
            storage_path=row["storage_path"],
            file_size_bytes=row["file_size_bytes"],
            checksum=row["checksum"],
            created_at=row["created_at"],
        )


def default_local_db_path() -> str:
    override = os.environ.get("LOCAL_PERSISTENCE_DB_PATH")
    if override:
        return override
    return str(Path(tempfile.gettempdir()) / "asre_lab_local_persistence.db")


def get_repository() -> PersistenceRepository:
    """
    Resolve the active persistence adapter for this process.

    No global singleton is cached here on purpose: constructing either
    adapter is cheap (the Supabase client is already a module-level
    singleton in `persistence_service`; SQLite connections are opened
    per-call), and re-resolving on every call lets tests select an
    isolated `LOCAL_PERSISTENCE_DB_PATH` per test without leaking state
    between them.
    """
    from app.core.persistence import persistence_service

    if persistence_service.enabled:
        return SupabaseRepository(persistence_service.client)
    return LocalSQLiteRepository(default_local_db_path())
