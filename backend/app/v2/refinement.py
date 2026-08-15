"""Authoritative coarse/medium/fine refinement from real persisted simulations."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from app.module2_simulation.source_resolution import resolve_simulation_source
from app.core.repository import get_repository
from app.v2.evidence_integrity import records_by_type
from app.v2.evidence_models import EvidenceType
from app.v2.repository import EvidenceRepository


def _remove_path(value: dict, dotted: str):
    result = json.loads(json.dumps(value))
    parts = dotted.split(".")
    current = result
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise ValueError("Refinement parameter does not resolve in persisted inputs")
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        raise ValueError("Refinement parameter does not resolve in persisted inputs")
    refinement_value = current.pop(parts[-1])
    if isinstance(refinement_value, bool) or not isinstance(refinement_value, (int, float)):
        raise ValueError("Refinement parameter must be numerical")
    return result, float(refinement_value)


def _fem_refinement_comparable(value: dict, dotted: str) -> tuple[dict, float] | None:
    """Normalize only the authoritative mesh identity in a CAD-FEM study.

    The surrounding immutable PhysicsModel is mesh-bound by design; its hash
    therefore cannot be compared verbatim across valid refinement levels.
    """
    context = value.get("geometry", {}).get("fem_refinement")
    if not isinstance(context, dict) or not dotted.startswith("geometry.fem_refinement.mesh.specification."):
        return None
    comparable, refinement = _remove_path(value, dotted)
    mesh = comparable["geometry"]["fem_refinement"].pop("mesh", None)
    if not isinstance(mesh, dict) or not mesh.get("mesh_id") or not mesh.get("mesh_hash"):
        raise ValueError("FEM refinement source lacks authoritative mesh provenance")
    geometry = comparable["geometry"]
    for key in ("mesh_id", "mesh_hash", "physics_model_hash"):
        geometry.pop(key, None)
    mesh_geometry = geometry.get("mesh_geometry")
    if isinstance(mesh_geometry, dict):
        for key in ("element_volume_m3", "node_count", "tetrahedron_count", "boundary_facet_count", "fallback_provenance"):
            mesh_geometry.pop(key, None)
    return comparable, refinement


def create_refinement_evidence(
    user_id: str, simulation_ids: list[str], selected_metric: str,
    refinement_parameter: str, threshold: float = .02, *, benchmark_id: str | None = None, repository=None,
) -> dict:
    repository = repository or get_repository()
    if len(simulation_ids) != 3 or len(set(simulation_ids)) != 3:
        raise ValueError("Exactly three distinct coarse, medium, and fine simulations are required")
    if not 0 < threshold <= 1:
        raise ValueError("Refinement threshold must be in (0, 1]")
    sources = [resolve_simulation_source(item, user_id, require_completed_result=True,
        required_summary_metric=None if benchmark_id else selected_metric, repository=repository) for item in simulation_ids]
    if len({item.solver_id for item in sources}) != 1:
        raise ValueError("Refinement sources must use the same solver")
    if len({item.solver_version for item in sources}) != 1:
        raise ValueError("Refinement sources must use the same solver version")
    if len({item.experiment_id for item in sources}) != 1 or len({item.design_id for item in sources}) != 1:
        raise ValueError("Refinement sources must belong to the same experiment and design")

    scientific = EvidenceRepository(repository=repository)
    physical_hashes, refinements, numerical_ids, mesh_hashes, values, benchmark_ids = [], [], [], [], [], []
    for source in sources:
        simulation_input = repository.get_simulation_input(source.simulation_id)
        if simulation_input is None:
            raise ValueError("Refinement source input is unavailable")
        input_data = asdict(simulation_input)
        input_data.pop("simulation_id", None)
        input_data.pop("created_at", None)
        normalized = _fem_refinement_comparable(input_data, refinement_parameter)
        if normalized is not None:
            mesh_hashes.append(input_data["geometry"]["fem_refinement"]["mesh"]["mesh_hash"])
        comparable, refinement_value = normalized or _remove_path(input_data, refinement_parameter)
        physical_hashes.append(hashlib.sha256(json.dumps(
            comparable, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()).hexdigest())
        refinements.append(refinement_value)
        candidates = records_by_type(scientific, user_id, source).get(EvidenceType.NUMERICAL_RESULT, [])
        if not candidates:
            raise ValueError("Refinement source lacks authoritative numerical evidence")
        numerical_ids.append(sorted(candidates, key=lambda x: (x[0].get("created_at", ""), x[0]["id"]))[-1][0]["id"])
        if benchmark_id:
            benchmarks = [item for item in records_by_type(scientific, user_id, source).get(EvidenceType.BENCHMARK, [])
                          if item[1].benchmark_id == benchmark_id and item[1].metric_name == selected_metric and item[1].status.value == "pass"]
            if not benchmarks:
                raise ValueError("Refinement source lacks a passed authoritative benchmark for the requested metric")
            benchmark = sorted(benchmarks, key=lambda x: (x[0].get("created_at", ""), x[0]["id"]))[-1]
            values.append(float(benchmark[1].computed_value)); benchmark_ids.append(benchmark[0]["id"])
    if len(set(physical_hashes)) != 1:
        raise ValueError("Refinement sources do not represent the same physical setup")
    if mesh_hashes and len(set(mesh_hashes)) != 3:
        raise ValueError("FEM refinement sources must use three distinct authoritative meshes")
    ordered = refinements[0] > refinements[1] > refinements[2] if mesh_hashes else refinements[0] < refinements[1] < refinements[2]
    if not ordered:
        raise ValueError("Refinement parameter must progress from coarse to medium to fine")

    if not benchmark_id:
        values = [float(item.result.summary_metrics[selected_metric]) for item in sources]
    changes = [
        0.0 if abs(values[index] - values[index - 1]) <= 1e-12 * max(abs(values[index]), abs(values[index - 1]), 1.0)
        else abs(values[index] - values[index - 1]) / max(abs(values[index]), 1e-15)
        for index in (1, 2)
    ]
    passed = (values[0] > values[1] > values[2] and values[2] <= threshold) if benchmark_id else changes[1] <= threshold and changes[1] <= changes[0]
    fingerprint = sources[-1].result.validation_metadata.get("input_fingerprint")
    payload = {
        "evidence_type": "refinement_convergence", "schema_version": "2.0",
        "experiment_id": sources[-1].experiment_id, "design_id": sources[-1].design_id,
        "simulation_id": sources[-1].simulation_id, "solver_id": sources[-1].solver_id,
        "solver_version": sources[-1].solver_version, "input_fingerprint": fingerprint,
        "result_hash": sources[-1].result.reproducibility_hash,
        "source_ids": numerical_ids + benchmark_ids, "status": "completed" if passed else "not_converged",
        "selected_metric": selected_metric, "refinement_parameter": refinement_parameter,
        "metric_source": "benchmark_evidence" if benchmark_id else "simulation_summary", "benchmark_id": benchmark_id,
        "comparison_hash": physical_hashes[0], "convergence_threshold": threshold,
        "coarse_to_medium_change": changes[0], "medium_to_fine_change": changes[1],
        "passed": passed,
        "levels": [{
            "level": name, "simulation_id": source.simulation_id,
            "value": value, "refinement_value": refinement_value,
            "input_fingerprint": source.result.validation_metadata.get("input_fingerprint"),
            "solver_id": source.solver_id, "solver_version": source.solver_version,
            "configuration": {refinement_parameter: refinement_value},
        } for name, source, value, refinement_value in zip(
            ("coarse", "medium", "fine"), sources, values, refinements,
        )],
        "warnings": [] if passed else ["Authoritative refinement criterion was not satisfied."],
        "limitations": ["The conclusion applies only to the selected metric and declared refinement dimension."],
    }
    return scientific.create_scientific_evidence(user_id, payload)
