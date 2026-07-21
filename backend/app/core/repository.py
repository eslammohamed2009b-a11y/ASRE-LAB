"""
Durable persistence and ownership abstraction (Module 1 production schema).

Backing tables (see `database/migrations/001_initial_schema.sql`,
`002_design_files.sql`, `003_job_tracking.sql` - the single authoritative
schema; the old root-level `database/schema.sql` /
`database/supabase_schema.sql` full-schema files are deprecated):

- `experiments`: one row per logical unit of work (a single ad-hoc
  generation or a batch job groups its designs under one experiment).
- `design_models`: one row per generated geometry variant (real CadQuery
  parameters/units captured as JSON for reproducibility).
- `design_files`: durable ownership + storage location (provider + object
  key, never a raw filesystem path) for every exported STL/STEP file.
- `generation_jobs`: async batch generation job tracking (status,
  progress, idempotency).

Two real implementations of `PersistenceRepository`:

- `SupabaseRepository`: production adapter. Used whenever Supabase
  credentials are configured (`SUPABASE_URL` + `SUPABASE_KEY`).
- `LocalSQLiteRepository`: deterministic adapter for local development and
  the automated test suite, backed by a real on-disk SQLite file (not an
  in-memory dict) - durable across process restarts and shareable across
  multiple repository instances pointed at the same database file.

Ownership enforcement happens in this layer's read methods (the caller
compares `user_id` to the requesting user), not by relying on Supabase Row
Level Security alone - `persistence_service` uses a single shared client
without per-request auth binding, so RLS is defense-in-depth here, not the
sole enforcing control.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ExperimentRecord:
    id: str
    user_id: str
    name: str
    status: str
    input_specification: dict = field(default_factory=dict)
    application_version: str = "unknown"
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class DesignModelRecord:
    id: str
    experiment_id: str
    user_id: str
    geometry_family: str
    parameters: dict = field(default_factory=dict)
    units: dict = field(default_factory=dict)
    variation_index: int = 0
    generation_status: str = "pending"
    cadquery_version: str | None = None
    application_version: str = "unknown"
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class DesignFileRecord:
    id: str
    owner_id: str
    experiment_id: str | None
    design_model_id: str | None
    file_format: str
    storage_provider: str
    object_key: str
    file_size_bytes: int | None
    checksum_sha256: str | None
    media_type: str
    created_at: str


@dataclass(frozen=True)
class GenerationJobRecord:
    id: str
    experiment_id: str
    user_id: str
    job_type: str
    status: str
    requested_count: int
    completed_count: int = 0
    failed_count: int = 0
    progress_percent: int = 0
    error_code: str | None = None
    safe_error_message: str | None = None
    idempotency_key: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str = ""


@dataclass(frozen=True)
class SimulationJobRecord:
    """Module 2 — async simulation job tracking (mirrors `GenerationJobRecord`'s
    ownership/status/idempotency model for Module 1 batch jobs)."""

    id: str
    experiment_id: str | None
    design_id: str | None
    user_id: str
    solver_id: str
    status: str
    idempotency_key: str | None = None
    error_code: str | None = None
    safe_error_message: str | None = None
    progress_percent: int = 0
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str = ""


@dataclass(frozen=True)
class SimulationInputRecord:
    """Immutable snapshot of exactly what was requested for a simulation
    job - never updated after creation, so it remains trustworthy evidence
    of the inputs a persisted result was actually computed from."""

    simulation_id: str
    material_name: str
    material_properties: dict = field(default_factory=dict)
    units: dict = field(default_factory=dict)
    initial_conditions: dict = field(default_factory=dict)
    boundary_conditions: dict = field(default_factory=dict)
    numerical_settings: dict = field(default_factory=dict)
    created_at: str = ""


@dataclass(frozen=True)
class SimulationResultRecord:
    """Immutable persisted result contract for a completed (or failed)
    simulation job - see Phase C2/C8 of the Module 2 architecture."""

    simulation_id: str
    solver_id: str
    solver_version: str
    governing_equations: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    converged: bool = False
    residual: float | None = None
    iteration_count: int = 0
    tolerance: float | None = None
    summary_metrics: dict = field(default_factory=dict)
    field_values: list = field(default_factory=list)
    hotspot_node_ids: list = field(default_factory=list)
    result_object_keys: list = field(default_factory=list)
    application_version: str = "unknown"
    created_at: str = ""


@dataclass(frozen=True)
class FieldResultRecord:
    id: str
    simulation_id: str
    user_id: str
    variable_name: str
    unit: str
    format: str
    format_version: str
    dimensions: int
    axes: list = field(default_factory=list)
    array_shape: list = field(default_factory=list)
    grid_metadata: dict = field(default_factory=dict)
    storage_object_key: str = ""
    checksum_sha256: str = ""
    byte_size: int = 0
    minimum: float = 0.0
    maximum: float = 0.0
    mean: float = 0.0
    preview: list = field(default_factory=list)
    reproducibility_hash: str = ""
    created_at: str = ""


class PersistenceRepository(ABC):
    """Abstract persistence + ownership boundary used by Module 1 routes."""

    # -- experiments ---------------------------------------------------
    @abstractmethod
    def create_experiment(
        self, user_id: str, name: str, input_specification: dict | None = None
    ) -> str:
        ...

    @abstractmethod
    def get_experiment(self, experiment_id: str) -> ExperimentRecord | None:
        ...

    # -- design_models ---------------------------------------------------
    @abstractmethod
    def create_design_model(
        self,
        experiment_id: str,
        user_id: str,
        geometry_family: str,
        parameters: dict,
        units: dict,
        variation_index: int,
        generation_status: str = "pending",
        cadquery_version: str | None = None,
    ) -> str:
        ...

    @abstractmethod
    def update_design_model_status(self, design_model_id: str, generation_status: str) -> None:
        ...

    @abstractmethod
    def list_design_models_for_experiment(self, experiment_id: str) -> list[DesignModelRecord]:
        ...

    # -- design_files ---------------------------------------------------
    @abstractmethod
    def record_design_file(
        self,
        design_id: str,
        owner_id: str,
        experiment_id: str | None,
        file_format: str,
        storage_provider: str,
        object_key: str,
        file_size_bytes: int | None,
        checksum_sha256: str | None,
        media_type: str = "application/octet-stream",
        design_model_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def get_design_file(self, design_id: str) -> DesignFileRecord | None:
        ...

    @abstractmethod
    def list_design_files_for_experiment(self, experiment_id: str) -> list[DesignFileRecord]:
        ...

    # -- generation_jobs -------------------------------------------------
    @abstractmethod
    def create_job(
        self,
        experiment_id: str,
        user_id: str,
        job_type: str,
        requested_count: int,
        idempotency_key: str | None = None,
    ) -> str:
        ...

    @abstractmethod
    def get_job_by_idempotency_key(self, user_id: str, idempotency_key: str) -> GenerationJobRecord | None:
        ...

    @abstractmethod
    def get_job(self, job_id: str) -> GenerationJobRecord | None:
        ...

    @abstractmethod
    def count_active_jobs_for_user(self, user_id: str) -> int:
        ...

    @abstractmethod
    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        completed_count: int | None = None,
        failed_count: int | None = None,
        progress_percent: int | None = None,
        error_code: str | None = None,
        safe_error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        ...

    # -- simulation_jobs (Module 2) --------------------------------------
    @abstractmethod
    def create_simulation_job(
        self,
        user_id: str,
        solver_id: str,
        experiment_id: str | None = None,
        design_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        ...

    @abstractmethod
    def get_simulation_job(self, simulation_id: str) -> SimulationJobRecord | None:
        ...

    @abstractmethod
    def get_simulation_job_by_idempotency_key(
        self, user_id: str, idempotency_key: str
    ) -> SimulationJobRecord | None:
        ...

    @abstractmethod
    def count_active_simulation_jobs_for_user(self, user_id: str) -> int:
        ...

    @abstractmethod
    def update_simulation_job(
        self,
        simulation_id: str,
        *,
        status: str | None = None,
        progress_percent: int | None = None,
        error_code: str | None = None,
        safe_error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        ...

    # -- simulation_inputs (Module 2) -------------------------------------
    @abstractmethod
    def record_simulation_input(
        self,
        simulation_id: str,
        material_name: str,
        material_properties: dict,
        units: dict,
        initial_conditions: dict,
        boundary_conditions: dict,
        numerical_settings: dict,
    ) -> None:
        ...

    @abstractmethod
    def get_simulation_input(self, simulation_id: str) -> SimulationInputRecord | None:
        ...

    # -- simulation_results (Module 2) ------------------------------------
    @abstractmethod
    def record_simulation_result(self, result: SimulationResultRecord) -> None:
        ...

    @abstractmethod
    def get_simulation_result(self, simulation_id: str) -> SimulationResultRecord | None:
        ...

    @abstractmethod
    def record_field_result(self, result: FieldResultRecord) -> None:
        ...

    @abstractmethod
    def get_field_result(self, field_result_id: str) -> FieldResultRecord | None:
        ...

    @abstractmethod
    def list_field_results(self, simulation_id: str) -> list[FieldResultRecord]:
        ...


class SupabaseRepository(PersistenceRepository):
    """Production adapter backed by Supabase Postgres."""

    def __init__(self, client: Any) -> None:
        self._client = client

    # -- experiments ---------------------------------------------------
    def create_experiment(
        self, user_id: str, name: str, input_specification: dict | None = None
    ) -> str:
        from app.core.config import settings

        payload = {
            "user_id": user_id,
            "name": name,
            "status": "running",
            "input_specification": input_specification or {},
            "application_version": settings.APPLICATION_VERSION,
            "created_at": _ts(),
            "updated_at": _ts(),
        }
        data = self._client.table("experiments").insert(payload).execute().data
        if not data:
            raise RuntimeError("Supabase did not return an inserted experiment row")
        return data[0]["id"]

    def get_experiment(self, experiment_id: str) -> ExperimentRecord | None:
        data = self._client.table("experiments").select("*").eq("id", experiment_id).execute().data
        if not data:
            return None
        row = data[0]
        return ExperimentRecord(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            status=row["status"],
            input_specification=row.get("input_specification") or {},
            application_version=row.get("application_version", "unknown"),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
        )

    # -- design_models ---------------------------------------------------
    def create_design_model(
        self,
        experiment_id: str,
        user_id: str,
        geometry_family: str,
        parameters: dict,
        units: dict,
        variation_index: int,
        generation_status: str = "pending",
        cadquery_version: str | None = None,
    ) -> str:
        from app.core.config import settings

        payload = {
            "experiment_id": experiment_id,
            "user_id": user_id,
            "geometry_family": geometry_family,
            "parameters": parameters,
            "units": units,
            "variation_index": variation_index,
            "generation_status": generation_status,
            "cadquery_version": cadquery_version,
            "application_version": settings.APPLICATION_VERSION,
            "created_at": _ts(),
            "updated_at": _ts(),
        }
        data = self._client.table("design_models").insert(payload).execute().data
        if not data:
            raise RuntimeError("Supabase did not return an inserted design_model row")
        return data[0]["id"]

    def update_design_model_status(self, design_model_id: str, generation_status: str) -> None:
        self._client.table("design_models").update(
            {"generation_status": generation_status, "updated_at": _ts()}
        ).eq("id", design_model_id).execute()

    def list_design_models_for_experiment(self, experiment_id: str) -> list[DesignModelRecord]:
        data = (
            self._client.table("design_models")
            .select("*")
            .eq("experiment_id", experiment_id)
            .order("variation_index")
            .execute()
            .data
        )
        return [
            DesignModelRecord(
                id=row["id"],
                experiment_id=row["experiment_id"],
                user_id=row["user_id"],
                geometry_family=row["geometry_family"],
                parameters=row.get("parameters") or {},
                units=row.get("units") or {},
                variation_index=row.get("variation_index", 0),
                generation_status=row.get("generation_status", "pending"),
                cadquery_version=row.get("cadquery_version"),
                application_version=row.get("application_version", "unknown"),
                created_at=row.get("created_at", ""),
                updated_at=row.get("updated_at", ""),
            )
            for row in (data or [])
        ]

    # -- design_files ---------------------------------------------------
    def record_design_file(
        self,
        design_id: str,
        owner_id: str,
        experiment_id: str | None,
        file_format: str,
        storage_provider: str,
        object_key: str,
        file_size_bytes: int | None,
        checksum_sha256: str | None,
        media_type: str = "application/octet-stream",
        design_model_id: str | None = None,
    ) -> None:
        payload = {
            "id": design_id,
            "user_id": owner_id,
            "experiment_id": experiment_id,
            "design_model_id": design_model_id,
            "file_format": file_format,
            "storage_provider": storage_provider,
            "object_key": object_key,
            "file_size_bytes": file_size_bytes,
            "checksum_sha256": checksum_sha256,
            "media_type": media_type,
            "created_at": _ts(),
        }
        self._client.table("design_files").insert(payload).execute()

    def get_design_file(self, design_id: str) -> DesignFileRecord | None:
        data = self._client.table("design_files").select("*").eq("id", design_id).execute().data
        if not data:
            return None
        row = data[0]
        return DesignFileRecord(
            id=row["id"],
            owner_id=row["user_id"],
            experiment_id=row.get("experiment_id"),
            design_model_id=row.get("design_model_id"),
            file_format=row["file_format"],
            storage_provider=row.get("storage_provider", "local"),
            object_key=row.get("object_key", ""),
            file_size_bytes=row.get("file_size_bytes"),
            checksum_sha256=row.get("checksum_sha256"),
            media_type=row.get("media_type", "application/octet-stream"),
            created_at=row["created_at"],
        )

    def list_design_files_for_experiment(self, experiment_id: str) -> list[DesignFileRecord]:
        data = (
            self._client.table("design_files")
            .select("*")
            .eq("experiment_id", experiment_id)
            .execute()
            .data
        )
        return [
            DesignFileRecord(
                id=row["id"],
                owner_id=row["user_id"],
                experiment_id=row.get("experiment_id"),
                design_model_id=row.get("design_model_id"),
                file_format=row["file_format"],
                storage_provider=row.get("storage_provider", "local"),
                object_key=row.get("object_key", ""),
                file_size_bytes=row.get("file_size_bytes"),
                checksum_sha256=row.get("checksum_sha256"),
                media_type=row.get("media_type", "application/octet-stream"),
                created_at=row["created_at"],
            )
            for row in (data or [])
        ]

    # -- generation_jobs -------------------------------------------------
    def create_job(
        self,
        experiment_id: str,
        user_id: str,
        job_type: str,
        requested_count: int,
        idempotency_key: str | None = None,
    ) -> str:
        payload = {
            "experiment_id": experiment_id,
            "user_id": user_id,
            "job_type": job_type,
            "status": "queued",
            "requested_count": requested_count,
            "completed_count": 0,
            "failed_count": 0,
            "progress_percent": 0,
            "idempotency_key": idempotency_key,
            "created_at": _ts(),
            "updated_at": _ts(),
        }
        data = self._client.table("generation_jobs").insert(payload).execute().data
        if not data:
            raise RuntimeError("Supabase did not return an inserted generation_jobs row")
        return data[0]["id"]

    def get_job_by_idempotency_key(self, user_id: str, idempotency_key: str) -> GenerationJobRecord | None:
        data = (
            self._client.table("generation_jobs")
            .select("*")
            .eq("user_id", user_id)
            .eq("idempotency_key", idempotency_key)
            .execute()
            .data
        )
        if not data:
            return None
        return self._row_to_job(data[0])

    def get_job(self, job_id: str) -> GenerationJobRecord | None:
        data = self._client.table("generation_jobs").select("*").eq("id", job_id).execute().data
        if not data:
            return None
        return self._row_to_job(data[0])

    def count_active_jobs_for_user(self, user_id: str) -> int:
        data = (
            self._client.table("generation_jobs")
            .select("id")
            .eq("user_id", user_id)
            .in_("status", ["queued", "running"])
            .execute()
            .data
        )
        return len(data or [])

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        completed_count: int | None = None,
        failed_count: int | None = None,
        progress_percent: int | None = None,
        error_code: str | None = None,
        safe_error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        updates: dict[str, Any] = {"updated_at": _ts()}
        for key, value in (
            ("status", status),
            ("completed_count", completed_count),
            ("failed_count", failed_count),
            ("progress_percent", progress_percent),
            ("error_code", error_code),
            ("safe_error_message", safe_error_message),
            ("started_at", started_at),
            ("finished_at", finished_at),
        ):
            if value is not None:
                updates[key] = value
        self._client.table("generation_jobs").update(updates).eq("id", job_id).execute()

    @staticmethod
    def _row_to_job(row: dict) -> GenerationJobRecord:
        return GenerationJobRecord(
            id=row["id"],
            experiment_id=row["experiment_id"],
            user_id=row["user_id"],
            job_type=row.get("job_type", "design_batch"),
            status=row.get("status", "queued"),
            requested_count=row.get("requested_count", 0),
            completed_count=row.get("completed_count", 0),
            failed_count=row.get("failed_count", 0),
            progress_percent=row.get("progress_percent", 0),
            error_code=row.get("error_code"),
            safe_error_message=row.get("safe_error_message"),
            idempotency_key=row.get("idempotency_key"),
            created_at=row.get("created_at", ""),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            updated_at=row.get("updated_at", ""),
        )

    # -- simulation_jobs (Module 2) --------------------------------------
    def create_simulation_job(
        self,
        user_id: str,
        solver_id: str,
        experiment_id: str | None = None,
        design_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        payload = {
            "user_id": user_id,
            "solver_id": solver_id,
            "experiment_id": experiment_id,
            "design_id": design_id,
            "status": "queued",
            "progress_percent": 0,
            "idempotency_key": idempotency_key,
            "created_at": _ts(),
            "updated_at": _ts(),
        }
        data = self._client.table("simulation_jobs").insert(payload).execute().data
        if not data:
            raise RuntimeError("Supabase did not return an inserted simulation_jobs row")
        return data[0]["id"]

    def get_simulation_job(self, simulation_id: str) -> SimulationJobRecord | None:
        data = self._client.table("simulation_jobs").select("*").eq("id", simulation_id).execute().data
        return self._row_to_simulation_job(data[0]) if data else None

    def get_simulation_job_by_idempotency_key(
        self, user_id: str, idempotency_key: str
    ) -> SimulationJobRecord | None:
        data = (
            self._client.table("simulation_jobs")
            .select("*")
            .eq("user_id", user_id)
            .eq("idempotency_key", idempotency_key)
            .execute()
            .data
        )
        return self._row_to_simulation_job(data[0]) if data else None

    def count_active_simulation_jobs_for_user(self, user_id: str) -> int:
        data = (
            self._client.table("simulation_jobs")
            .select("id")
            .eq("user_id", user_id)
            .in_("status", ["queued", "running"])
            .execute()
            .data
        )
        return len(data or [])

    def update_simulation_job(
        self,
        simulation_id: str,
        *,
        status: str | None = None,
        progress_percent: int | None = None,
        error_code: str | None = None,
        safe_error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        updates: dict[str, Any] = {"updated_at": _ts()}
        for key, value in (
            ("status", status),
            ("progress_percent", progress_percent),
            ("error_code", error_code),
            ("safe_error_message", safe_error_message),
            ("started_at", started_at),
            ("finished_at", finished_at),
        ):
            if value is not None:
                updates[key] = value
        self._client.table("simulation_jobs").update(updates).eq("id", simulation_id).execute()

    @staticmethod
    def _row_to_simulation_job(row: dict) -> SimulationJobRecord:
        return SimulationJobRecord(
            id=row["id"],
            experiment_id=row.get("experiment_id"),
            design_id=row.get("design_id"),
            user_id=row["user_id"],
            solver_id=row["solver_id"],
            status=row.get("status", "queued"),
            idempotency_key=row.get("idempotency_key"),
            error_code=row.get("error_code"),
            safe_error_message=row.get("safe_error_message"),
            progress_percent=row.get("progress_percent", 0),
            created_at=row.get("created_at", ""),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            updated_at=row.get("updated_at", ""),
        )

    # -- simulation_inputs (Module 2) -------------------------------------
    def record_simulation_input(
        self,
        simulation_id: str,
        material_name: str,
        material_properties: dict,
        units: dict,
        initial_conditions: dict,
        boundary_conditions: dict,
        numerical_settings: dict,
    ) -> None:
        payload = {
            "simulation_id": simulation_id,
            "material_name": material_name,
            "material_properties": material_properties,
            "units": units,
            "initial_conditions": initial_conditions,
            "boundary_conditions": boundary_conditions,
            "numerical_settings": numerical_settings,
            "created_at": _ts(),
        }
        self._client.table("simulation_inputs").insert(payload).execute()

    def get_simulation_input(self, simulation_id: str) -> SimulationInputRecord | None:
        data = (
            self._client.table("simulation_inputs")
            .select("*")
            .eq("simulation_id", simulation_id)
            .execute()
            .data
        )
        if not data:
            return None
        row = data[0]
        return SimulationInputRecord(
            simulation_id=row["simulation_id"],
            material_name=row.get("material_name", ""),
            material_properties=row.get("material_properties") or {},
            units=row.get("units") or {},
            initial_conditions=row.get("initial_conditions") or {},
            boundary_conditions=row.get("boundary_conditions") or {},
            numerical_settings=row.get("numerical_settings") or {},
            created_at=row.get("created_at", ""),
        )

    # -- simulation_results (Module 2) ------------------------------------
    def record_simulation_result(self, result: SimulationResultRecord) -> None:
        payload = {
            "simulation_id": result.simulation_id,
            "solver_id": result.solver_id,
            "solver_version": result.solver_version,
            "governing_equations": result.governing_equations,
            "assumptions": result.assumptions,
            "warnings": result.warnings,
            "converged": result.converged,
            "residual": result.residual,
            "iteration_count": result.iteration_count,
            "tolerance": result.tolerance,
            "summary_metrics": result.summary_metrics,
            "field_values": result.field_values,
            "hotspot_node_ids": result.hotspot_node_ids,
            "result_object_keys": result.result_object_keys,
            "application_version": result.application_version,
            "created_at": _ts(),
        }
        self._client.table("simulation_results").insert(payload).execute()

    def get_simulation_result(self, simulation_id: str) -> SimulationResultRecord | None:
        data = (
            self._client.table("simulation_results")
            .select("*")
            .eq("simulation_id", simulation_id)
            .execute()
            .data
        )
        if not data:
            return None
        row = data[0]
        return SimulationResultRecord(
            simulation_id=row["simulation_id"],
            solver_id=row["solver_id"],
            solver_version=row.get("solver_version", "unknown"),
            governing_equations=row.get("governing_equations") or [],
            assumptions=row.get("assumptions") or [],
            warnings=row.get("warnings") or [],
            converged=bool(row.get("converged", False)),
            residual=row.get("residual"),
            iteration_count=row.get("iteration_count", 0),
            tolerance=row.get("tolerance"),
            summary_metrics=row.get("summary_metrics") or {},
            field_values=row.get("field_values") or [],
            hotspot_node_ids=row.get("hotspot_node_ids") or [],
            result_object_keys=row.get("result_object_keys") or [],
            application_version=row.get("application_version", "unknown"),
            created_at=row.get("created_at", ""),
        )

    def record_field_result(self, result: FieldResultRecord) -> None:
        payload = {
            "id": result.id, "simulation_id": result.simulation_id, "user_id": result.user_id,
            "variable_name": result.variable_name, "unit": result.unit, "format": result.format,
            "format_version": result.format_version, "dimensions": result.dimensions,
            "axes": result.axes, "array_shape": result.array_shape, "grid_metadata": result.grid_metadata,
            "storage_object_key": result.storage_object_key, "checksum_sha256": result.checksum_sha256,
            "byte_size": result.byte_size, "minimum": result.minimum, "maximum": result.maximum,
            "mean": result.mean, "preview": result.preview,
            "reproducibility_hash": result.reproducibility_hash, "created_at": result.created_at or _ts(),
        }
        self._client.table("simulation_field_results").insert(payload).execute()

    @staticmethod
    def _field_result_from_row(row: dict) -> FieldResultRecord:
        return FieldResultRecord(
            id=row["id"], simulation_id=row["simulation_id"], user_id=row["user_id"],
            variable_name=row["variable_name"], unit=row["unit"], format=row["format"],
            format_version=row["format_version"], dimensions=row["dimensions"], axes=row.get("axes") or [],
            array_shape=row.get("array_shape") or [], grid_metadata=row.get("grid_metadata") or {},
            storage_object_key=row["storage_object_key"], checksum_sha256=row["checksum_sha256"],
            byte_size=row["byte_size"], minimum=row["minimum"], maximum=row["maximum"], mean=row["mean"],
            preview=row.get("preview") or [], reproducibility_hash=row["reproducibility_hash"],
            created_at=row.get("created_at", ""),
        )

    def get_field_result(self, field_result_id: str) -> FieldResultRecord | None:
        data = self._client.table("simulation_field_results").select("*").eq("id", field_result_id).execute().data
        return self._field_result_from_row(data[0]) if data else None

    def list_field_results(self, simulation_id: str) -> list[FieldResultRecord]:
        data = self._client.table("simulation_field_results").select("*").eq("simulation_id", simulation_id).execute().data
        return [self._field_result_from_row(row) for row in data]


class LocalSQLiteRepository(PersistenceRepository):
    """
    Deterministic local/test adapter, backed by a real SQLite file.

    Genuinely durable across process restarts (data lives on disk, not in
    a Python dict) and shareable across multiple `LocalSQLiteRepository`
    instances pointed at the same `db_path`.
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
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    input_specification TEXT NOT NULL DEFAULT '{}',
                    application_version TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS design_models (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    geometry_family TEXT NOT NULL,
                    parameters TEXT NOT NULL DEFAULT '{}',
                    units TEXT NOT NULL DEFAULT '{}',
                    variation_index INTEGER NOT NULL DEFAULT 0,
                    generation_status TEXT NOT NULL DEFAULT 'pending',
                    cadquery_version TEXT,
                    application_version TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                    storage_provider TEXT NOT NULL DEFAULT 'local',
                    object_key TEXT NOT NULL,
                    file_size_bytes INTEGER,
                    checksum_sha256 TEXT,
                    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    job_type TEXT NOT NULL DEFAULT 'design_batch',
                    status TEXT NOT NULL DEFAULT 'queued',
                    requested_count INTEGER NOT NULL,
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    progress_percent INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    safe_error_message TEXT,
                    idempotency_key TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_user_idempotency "
                "ON generation_jobs(user_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_jobs (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT,
                    design_id TEXT,
                    user_id TEXT NOT NULL,
                    solver_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress_percent INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT,
                    error_code TEXT,
                    safe_error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_sim_jobs_user_idempotency "
                "ON simulation_jobs(user_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_inputs (
                    simulation_id TEXT PRIMARY KEY,
                    material_name TEXT NOT NULL,
                    material_properties TEXT NOT NULL DEFAULT '{}',
                    units TEXT NOT NULL DEFAULT '{}',
                    initial_conditions TEXT NOT NULL DEFAULT '{}',
                    boundary_conditions TEXT NOT NULL DEFAULT '{}',
                    numerical_settings TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_results (
                    simulation_id TEXT PRIMARY KEY,
                    solver_id TEXT NOT NULL,
                    solver_version TEXT NOT NULL,
                    governing_equations TEXT NOT NULL DEFAULT '[]',
                    assumptions TEXT NOT NULL DEFAULT '[]',
                    warnings TEXT NOT NULL DEFAULT '[]',
                    converged INTEGER NOT NULL DEFAULT 0,
                    residual REAL,
                    iteration_count INTEGER NOT NULL DEFAULT 0,
                    tolerance REAL,
                    summary_metrics TEXT NOT NULL DEFAULT '{}',
                    field_values TEXT NOT NULL DEFAULT '[]',
                    hotspot_node_ids TEXT NOT NULL DEFAULT '[]',
                    result_object_keys TEXT NOT NULL DEFAULT '[]',
                    application_version TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_field_results (
                    id TEXT PRIMARY KEY,
                    simulation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    variable_name TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    format TEXT NOT NULL,
                    format_version TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    axes TEXT NOT NULL DEFAULT '[]',
                    array_shape TEXT NOT NULL DEFAULT '[]',
                    grid_metadata TEXT NOT NULL DEFAULT '{}',
                    storage_object_key TEXT NOT NULL UNIQUE,
                    checksum_sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    minimum REAL NOT NULL,
                    maximum REAL NOT NULL,
                    mean REAL NOT NULL,
                    preview TEXT NOT NULL DEFAULT '[]',
                    reproducibility_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(simulation_id) REFERENCES simulation_jobs(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_field_results_simulation ON simulation_field_results(simulation_id)")
            conn.commit()
        finally:
            conn.close()

    # -- experiments ---------------------------------------------------
    def create_experiment(
        self, user_id: str, name: str, input_specification: dict | None = None
    ) -> str:
        from app.core.config import settings

        experiment_id = str(uuid.uuid4())
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO experiments (id, user_id, name, status, input_specification, "
                "application_version, created_at, updated_at) VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
                (
                    experiment_id,
                    user_id,
                    name,
                    json.dumps(input_specification or {}),
                    settings.APPLICATION_VERSION,
                    _ts(),
                    _ts(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return experiment_id

    def get_experiment(self, experiment_id: str) -> ExperimentRecord | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return ExperimentRecord(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            status=row["status"],
            input_specification=json.loads(row["input_specification"]),
            application_version=row["application_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -- design_models ---------------------------------------------------
    def create_design_model(
        self,
        experiment_id: str,
        user_id: str,
        geometry_family: str,
        parameters: dict,
        units: dict,
        variation_index: int,
        generation_status: str = "pending",
        cadquery_version: str | None = None,
    ) -> str:
        from app.core.config import settings

        design_model_id = str(uuid.uuid4())
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO design_models (id, experiment_id, user_id, geometry_family, parameters, "
                "units, variation_index, generation_status, cadquery_version, application_version, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    design_model_id,
                    experiment_id,
                    user_id,
                    geometry_family,
                    json.dumps(parameters),
                    json.dumps(units),
                    variation_index,
                    generation_status,
                    cadquery_version,
                    settings.APPLICATION_VERSION,
                    _ts(),
                    _ts(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return design_model_id

    def update_design_model_status(self, design_model_id: str, generation_status: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE design_models SET generation_status = ?, updated_at = ? WHERE id = ?",
                (generation_status, _ts(), design_model_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_design_models_for_experiment(self, experiment_id: str) -> list[DesignModelRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM design_models WHERE experiment_id = ? ORDER BY variation_index",
                (experiment_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            DesignModelRecord(
                id=row["id"],
                experiment_id=row["experiment_id"],
                user_id=row["user_id"],
                geometry_family=row["geometry_family"],
                parameters=json.loads(row["parameters"]),
                units=json.loads(row["units"]),
                variation_index=row["variation_index"],
                generation_status=row["generation_status"],
                cadquery_version=row["cadquery_version"],
                application_version=row["application_version"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    # -- design_files ---------------------------------------------------
    def record_design_file(
        self,
        design_id: str,
        owner_id: str,
        experiment_id: str | None,
        file_format: str,
        storage_provider: str,
        object_key: str,
        file_size_bytes: int | None,
        checksum_sha256: str | None,
        media_type: str = "application/octet-stream",
        design_model_id: str | None = None,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO design_files "
                "(id, user_id, experiment_id, design_model_id, file_format, storage_provider, "
                "object_key, file_size_bytes, checksum_sha256, media_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    design_id,
                    owner_id,
                    experiment_id,
                    design_model_id,
                    file_format,
                    storage_provider,
                    object_key,
                    file_size_bytes,
                    checksum_sha256,
                    media_type,
                    _ts(),
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
        return self._row_to_file(row)

    def list_design_files_for_experiment(self, experiment_id: str) -> list[DesignFileRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM design_files WHERE experiment_id = ?", (experiment_id,)
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_file(row) for row in rows]

    @staticmethod
    def _row_to_file(row: sqlite3.Row) -> DesignFileRecord:
        return DesignFileRecord(
            id=row["id"],
            owner_id=row["user_id"],
            experiment_id=row["experiment_id"],
            design_model_id=row["design_model_id"],
            file_format=row["file_format"],
            storage_provider=row["storage_provider"],
            object_key=row["object_key"],
            file_size_bytes=row["file_size_bytes"],
            checksum_sha256=row["checksum_sha256"],
            media_type=row["media_type"],
            created_at=row["created_at"],
        )

    # -- generation_jobs -------------------------------------------------
    def create_job(
        self,
        experiment_id: str,
        user_id: str,
        job_type: str,
        requested_count: int,
        idempotency_key: str | None = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO generation_jobs (id, experiment_id, user_id, job_type, status, "
                "requested_count, completed_count, failed_count, progress_percent, idempotency_key, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, 0, 0, 0, ?, ?, ?)",
                (job_id, experiment_id, user_id, job_type, requested_count, idempotency_key, _ts(), _ts()),
            )
            conn.commit()
        finally:
            conn.close()
        return job_id

    def get_job_by_idempotency_key(self, user_id: str, idempotency_key: str) -> GenerationJobRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM generation_jobs WHERE user_id = ? AND idempotency_key = ?",
                (user_id, idempotency_key),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_job(row) if row is not None else None

    def get_job(self, job_id: str) -> GenerationJobRecord | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            conn.close()
        return self._row_to_job(row) if row is not None else None

    def count_active_jobs_for_user(self, user_id: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM generation_jobs "
                "WHERE user_id = ? AND status IN ('queued', 'running')",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(row["count"])

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        completed_count: int | None = None,
        failed_count: int | None = None,
        progress_percent: int | None = None,
        error_code: str | None = None,
        safe_error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        fields_to_set: list[str] = ["updated_at = ?"]
        values: list[Any] = [_ts()]
        for column, value in (
            ("status", status),
            ("completed_count", completed_count),
            ("failed_count", failed_count),
            ("progress_percent", progress_percent),
            ("error_code", error_code),
            ("safe_error_message", safe_error_message),
            ("started_at", started_at),
            ("finished_at", finished_at),
        ):
            if value is not None:
                fields_to_set.append(f"{column} = ?")
                values.append(value)
        values.append(job_id)
        conn = self._connect()
        try:
            conn.execute(
                f"UPDATE generation_jobs SET {', '.join(fields_to_set)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> GenerationJobRecord:
        return GenerationJobRecord(
            id=row["id"],
            experiment_id=row["experiment_id"],
            user_id=row["user_id"],
            job_type=row["job_type"],
            status=row["status"],
            requested_count=row["requested_count"],
            completed_count=row["completed_count"],
            failed_count=row["failed_count"],
            progress_percent=row["progress_percent"],
            error_code=row["error_code"],
            safe_error_message=row["safe_error_message"],
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            updated_at=row["updated_at"],
        )

    # -- simulation_jobs (Module 2) --------------------------------------
    def create_simulation_job(
        self,
        user_id: str,
        solver_id: str,
        experiment_id: str | None = None,
        design_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        simulation_id = str(uuid.uuid4())
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO simulation_jobs (id, experiment_id, design_id, user_id, solver_id, "
                "status, progress_percent, idempotency_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)",
                (simulation_id, experiment_id, design_id, user_id, solver_id, idempotency_key, _ts(), _ts()),
            )
            conn.commit()
        finally:
            conn.close()
        return simulation_id

    def get_simulation_job(self, simulation_id: str) -> SimulationJobRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM simulation_jobs WHERE id = ?", (simulation_id,)
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_simulation_job(row) if row is not None else None

    def get_simulation_job_by_idempotency_key(
        self, user_id: str, idempotency_key: str
    ) -> SimulationJobRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM simulation_jobs WHERE user_id = ? AND idempotency_key = ?",
                (user_id, idempotency_key),
            ).fetchone()
        finally:
            conn.close()
        return self._row_to_simulation_job(row) if row is not None else None

    def count_active_simulation_jobs_for_user(self, user_id: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM simulation_jobs "
                "WHERE user_id = ? AND status IN ('queued', 'running')",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(row["count"])

    def update_simulation_job(
        self,
        simulation_id: str,
        *,
        status: str | None = None,
        progress_percent: int | None = None,
        error_code: str | None = None,
        safe_error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        fields_to_set: list[str] = ["updated_at = ?"]
        values: list[Any] = [_ts()]
        for column, value in (
            ("status", status),
            ("progress_percent", progress_percent),
            ("error_code", error_code),
            ("safe_error_message", safe_error_message),
            ("started_at", started_at),
            ("finished_at", finished_at),
        ):
            if value is not None:
                fields_to_set.append(f"{column} = ?")
                values.append(value)
        values.append(simulation_id)
        conn = self._connect()
        try:
            conn.execute(
                f"UPDATE simulation_jobs SET {', '.join(fields_to_set)} WHERE id = ?",
                values,
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_simulation_job(row: sqlite3.Row) -> SimulationJobRecord:
        return SimulationJobRecord(
            id=row["id"],
            experiment_id=row["experiment_id"],
            design_id=row["design_id"],
            user_id=row["user_id"],
            solver_id=row["solver_id"],
            status=row["status"],
            idempotency_key=row["idempotency_key"],
            error_code=row["error_code"],
            safe_error_message=row["safe_error_message"],
            progress_percent=row["progress_percent"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            updated_at=row["updated_at"],
        )

    # -- simulation_inputs (Module 2) -------------------------------------
    def record_simulation_input(
        self,
        simulation_id: str,
        material_name: str,
        material_properties: dict,
        units: dict,
        initial_conditions: dict,
        boundary_conditions: dict,
        numerical_settings: dict,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO simulation_inputs (simulation_id, material_name, material_properties, "
                "units, initial_conditions, boundary_conditions, numerical_settings, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    simulation_id,
                    material_name,
                    json.dumps(material_properties),
                    json.dumps(units),
                    json.dumps(initial_conditions),
                    json.dumps(boundary_conditions),
                    json.dumps(numerical_settings),
                    _ts(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_simulation_input(self, simulation_id: str) -> SimulationInputRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM simulation_inputs WHERE simulation_id = ?", (simulation_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return SimulationInputRecord(
            simulation_id=row["simulation_id"],
            material_name=row["material_name"],
            material_properties=json.loads(row["material_properties"]),
            units=json.loads(row["units"]),
            initial_conditions=json.loads(row["initial_conditions"]),
            boundary_conditions=json.loads(row["boundary_conditions"]),
            numerical_settings=json.loads(row["numerical_settings"]),
            created_at=row["created_at"],
        )

    # -- simulation_results (Module 2) ------------------------------------
    def record_simulation_result(self, result: SimulationResultRecord) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO simulation_results (simulation_id, solver_id, solver_version, "
                "governing_equations, assumptions, warnings, converged, residual, iteration_count, "
                "tolerance, summary_metrics, field_values, hotspot_node_ids, result_object_keys, "
                "application_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.simulation_id,
                    result.solver_id,
                    result.solver_version,
                    json.dumps(result.governing_equations),
                    json.dumps(result.assumptions),
                    json.dumps(result.warnings),
                    int(result.converged),
                    result.residual,
                    result.iteration_count,
                    result.tolerance,
                    json.dumps(result.summary_metrics),
                    json.dumps(result.field_values),
                    json.dumps(result.hotspot_node_ids),
                    json.dumps(result.result_object_keys),
                    result.application_version,
                    _ts(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_simulation_result(self, simulation_id: str) -> SimulationResultRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM simulation_results WHERE simulation_id = ?", (simulation_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return SimulationResultRecord(
            simulation_id=row["simulation_id"],
            solver_id=row["solver_id"],
            solver_version=row["solver_version"],
            governing_equations=json.loads(row["governing_equations"]),
            assumptions=json.loads(row["assumptions"]),
            warnings=json.loads(row["warnings"]),
            converged=bool(row["converged"]),
            residual=row["residual"],
            iteration_count=row["iteration_count"],
            tolerance=row["tolerance"],
            summary_metrics=json.loads(row["summary_metrics"]),
            field_values=json.loads(row["field_values"]),
            hotspot_node_ids=json.loads(row["hotspot_node_ids"]),
            result_object_keys=json.loads(row["result_object_keys"]),
            application_version=row["application_version"],
            created_at=row["created_at"],
        )

    def record_field_result(self, result: FieldResultRecord) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO simulation_field_results (id, simulation_id, user_id, variable_name, unit, "
                "format, format_version, dimensions, axes, array_shape, grid_metadata, storage_object_key, "
                "checksum_sha256, byte_size, minimum, maximum, mean, preview, reproducibility_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result.id, result.simulation_id, result.user_id, result.variable_name, result.unit,
                 result.format, result.format_version, result.dimensions, json.dumps(result.axes),
                 json.dumps(result.array_shape), json.dumps(result.grid_metadata), result.storage_object_key,
                 result.checksum_sha256, result.byte_size, result.minimum, result.maximum, result.mean,
                 json.dumps(result.preview), result.reproducibility_hash, result.created_at or _ts()),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _field_result_from_sqlite(row: sqlite3.Row) -> FieldResultRecord:
        return FieldResultRecord(
            id=row["id"], simulation_id=row["simulation_id"], user_id=row["user_id"],
            variable_name=row["variable_name"], unit=row["unit"], format=row["format"],
            format_version=row["format_version"], dimensions=row["dimensions"], axes=json.loads(row["axes"]),
            array_shape=json.loads(row["array_shape"]), grid_metadata=json.loads(row["grid_metadata"]),
            storage_object_key=row["storage_object_key"], checksum_sha256=row["checksum_sha256"],
            byte_size=row["byte_size"], minimum=row["minimum"], maximum=row["maximum"], mean=row["mean"],
            preview=json.loads(row["preview"]), reproducibility_hash=row["reproducibility_hash"],
            created_at=row["created_at"],
        )

    def get_field_result(self, field_result_id: str) -> FieldResultRecord | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM simulation_field_results WHERE id = ?", (field_result_id,)).fetchone()
        finally:
            conn.close()
        return self._field_result_from_sqlite(row) if row else None

    def list_field_results(self, simulation_id: str) -> list[FieldResultRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM simulation_field_results WHERE simulation_id = ? ORDER BY created_at, id",
                (simulation_id,),
            ).fetchall()
        finally:
            conn.close()
        return [self._field_result_from_sqlite(row) for row in rows]


def default_local_db_path() -> str:
    override = os.environ.get("LOCAL_PERSISTENCE_DB_PATH")
    if override:
        return override
    # Versioned filename: the schema below (experiments/design_models/
    # design_files/generation_jobs columns) changed in the "Module 1
    # production completion" pass. `CREATE TABLE IF NOT EXISTS` never
    # migrates an existing file with the old column set, so bumping this
    # name forces a fresh, correctly-shaped database instead of crashing
    # against a stale one left over from before this change.
    return str(Path(tempfile.gettempdir()) / "asre_lab_local_persistence_v3.db")


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
