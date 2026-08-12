"""Canonical scientific identities; never include queue/timestamp/wall-clock data."""
from __future__ import annotations
import hashlib, json, os
from typing import Any

def _hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

def input_fingerprint(*, solver_id: str, solver_version: str, request: dict, material_snapshot: dict,
                      design_id: str | None, application_version: str = "unknown") -> str:
    return _hash({"solver_id": solver_id, "solver_version": solver_version, "request": request,
                  "material_snapshot": material_snapshot, "design_id": design_id,
                  "application_version": application_version,
                  "code_revision": os.getenv("ASRE_CODE_REVISION", "unknown")})

def result_hash(*, solver_id: str, solver_version: str, input_fingerprint_value: str,
                converged: bool, iteration_count: int, metric: float | None, summary_metrics: dict,
                validation_metadata: dict, field_checksums: list[str] | None = None) -> str:
    return _hash({"solver_id": solver_id, "solver_version": solver_version,
                  "input_fingerprint": input_fingerprint_value, "converged": converged,
                  "iteration_count": iteration_count, "convergence_metric": metric,
                  "summary_metrics": summary_metrics, "validation_metadata": validation_metadata,
                  "field_checksums": sorted(field_checksums or [])})
