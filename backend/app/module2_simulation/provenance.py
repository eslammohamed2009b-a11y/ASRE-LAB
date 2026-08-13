"""Canonical scientific identities; never include queue/timestamp/wall-clock data."""
from __future__ import annotations
import hashlib, json
from typing import Any

_OPERATIONAL_KEYS = {
    "created_at", "updated_at", "started_at", "finished_at", "timestamp",
    "elapsed_time_seconds", "queue_latency", "retry_count", "worker_id",
    "request_id", "signed_url", "download_url", "storage_object_key",
    "temporary_path", "temp_path",
    "experiment_id", "design_id", "simulation_id", "source_simulation_id",
}

def _hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

def scientific_metadata(value: Any) -> Any:
    """Remove operational location/timing data from a scientific identity."""
    if isinstance(value, dict):
        return {
            key: scientific_metadata(item)
            for key, item in sorted(value.items())
            if key not in _OPERATIONAL_KEYS
            and not key.endswith("_url")
            and not key.endswith("_path")
        }
    if isinstance(value, list):
        return [scientific_metadata(item) for item in value]
    return value

def input_fingerprint(*, solver_id: str, solver_version: str, request: dict, material_snapshot: dict,
                      design_id: str | None, application_version: str = "unknown") -> str:
    # Identifiers and deployment metadata are deliberately accepted for API
    # compatibility but excluded: solver version + normalized inputs define
    # scientific identity, not random database IDs or deployment state.
    return _hash({"solver_id": solver_id, "solver_version": solver_version,
                  "request": scientific_metadata(request),
                  "material_snapshot": scientific_metadata(material_snapshot)})

def result_hash(*, solver_id: str, solver_version: str, input_fingerprint_value: str,
                converged: bool, iteration_count: int, metric: float | None, summary_metrics: dict,
                validation_metadata: dict, field_checksums: list[str] | None = None,
                numerical_method: str = "", tolerance: float | None = None) -> str:
    return _hash({"solver_id": solver_id, "solver_version": solver_version,
                  "input_fingerprint": input_fingerprint_value, "converged": converged,
                  "iteration_count": iteration_count, "convergence_metric": metric,
                  "convergence_tolerance": tolerance,
                  "numerical_method": numerical_method,
                  "summary_metrics": summary_metrics,
                  "validation_metadata": scientific_metadata(validation_metadata),
                  "field_checksums": sorted(field_checksums or [])})
