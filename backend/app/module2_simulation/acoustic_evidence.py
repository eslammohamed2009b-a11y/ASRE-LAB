"""Server-owned persistence integrity and evidence for bounded 3D acoustics."""
from __future__ import annotations

import hashlib
import json
import math
import numpy as np

from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.acoustic_benchmark import (
    BENCHMARK_ID,
    BENCHMARK_VERSION,
    COMPLEX_L2_ERROR_LIMIT,
    FREQUENCY_HZ,
    MIN_OBSERVED_ORDER,
    REFINEMENT_TARGETS_MM,
    analytical_plane_wave_pressure,
    authoritative_speed_of_sound,
    benchmark_design,
    benchmark_physics_request,
    validate_benchmark_identity,
)
from app.module2_simulation.acoustic_fem_solvers import (
    MAX_K_H,
    MIN_RECIPROCAL_CONDITION_ESTIMATE,
    RESIDUAL_TOLERANCE,
    SOLVER_ID,
    SOLVER_VERSION,
)
from app.module2_simulation.field_results import load_field_artifact
from app.module2_simulation.physics_model import build_physics_model
from app.v2.evidence_models import BenchmarkCaseBinding, BenchmarkEvidence, EvidenceType
from app.v2.repository import EvidenceRepository


REQUIRED_ACOUSTIC_FIELDS = {
    "pressure_real_pa", "pressure_imag_pa", "pressure_amplitude_pa",
    "pressure_phase_rad", "sound_pressure_level_db", "sound_pressure_level_defined",
}


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _snapshot_property(snapshot: dict, name: str) -> float:
    item = next((value for value in snapshot.get("properties", []) if value.get("name") == name), None)
    if item is None or not math.isfinite(float(item["value"])):
        raise ValueError(f"Persisted acoustic material snapshot lacks {name}")
    return float(item["value"])


def load_acoustic_fields(*, repository, storage, user_id: str, simulation_id: str):
    """Reload and cross-check every persisted acoustic nodal field."""
    job = repository.get_simulation_job(simulation_id)
    result = repository.get_simulation_result(simulation_id)
    simulation_input = repository.get_simulation_input(simulation_id)
    if job is None or job.user_id != user_id or result is None or simulation_input is None:
        raise LookupError("Owned persisted acoustic result is unavailable")
    if result.solver_id != SOLVER_ID or result.solver_version != SOLVER_VERSION or result.status != "completed":
        raise ValueError("Persisted result is not a completed authoritative acoustic solve")
    metadata = result.validation_metadata
    if _hash(simulation_input.geometry) != metadata.get("input_fingerprint"):
        raise ValueError("Persisted acoustic input identity is invalid")
    node_count = int(simulation_input.geometry.get("mesh_geometry", {}).get("node_count", 0))
    if node_count <= 0:
        raise ValueError("Persisted acoustic mesh node count is unavailable")
    records = repository.list_field_results(simulation_id)
    by_name = {item.variable_name: item for item in records}
    if set(by_name) != REQUIRED_ACOUSTIC_FIELDS or len(records) != len(REQUIRED_ACOUSTIC_FIELDS):
        raise ValueError("Persisted acoustic field set is incomplete or ambiguous")
    expected_prefix = f"users/{user_id}/experiments/{job.experiment_id}/simulations/{simulation_id}/"
    arrays = {}
    for name, record in by_name.items():
        grid = record.grid_metadata
        if (
            record.user_id != user_id or not record.storage_object_key.startswith(expected_prefix)
            or record.array_shape != [node_count] or record.dimensions != 1
            or grid.get("solver_id") != SOLVER_ID or grid.get("solver_version") != SOLVER_VERSION
            or grid.get("mesh_hash") != metadata.get("mesh_hash")
            or grid.get("physics_model_id") != metadata.get("physics_model_id")
            or grid.get("physics_hash") != metadata.get("physics_model_hash")
            or grid.get("design_hash") != metadata.get("design_hash")
            or grid.get("geometry_fingerprint") != metadata.get("geometry_fingerprint")
            or grid.get("material_snapshot_hashes") != metadata.get("material_snapshot_hashes")
            or int(grid.get("node_count", 0)) != node_count
            or float(grid.get("frequency_hz", math.nan)) != float(metadata.get("frequency_hz", math.nan))
            or metadata.get("owner_id") != user_id or metadata.get("simulation_id") != simulation_id
        ):
            raise ValueError("Persisted acoustic field scientific identity is invalid")
        values = load_field_artifact(storage, record.storage_object_key, record.checksum_sha256)
        if values.shape != (node_count,):
            raise ValueError("Persisted acoustic field length does not match its authoritative mesh")
        arrays[name] = values
    real = arrays["pressure_real_pa"]
    imaginary = arrays["pressure_imag_pa"]
    amplitude = arrays["pressure_amplitude_pa"]
    phase = arrays["pressure_phase_rad"]
    defined = arrays["sound_pressure_level_defined"].astype(bool)
    if not np.allclose(amplitude, np.hypot(real, imaginary), rtol=1e-12, atol=1e-15):
        raise ValueError("Persisted acoustic amplitude contradicts complex pressure")
    if not np.allclose(phase, np.angle(real + 1j * imaginary), rtol=1e-12, atol=1e-12):
        raise ValueError("Persisted acoustic phase contradicts complex pressure")
    if not np.array_equal(defined, amplitude > 0):
        raise ValueError("Persisted SPL validity mask contradicts pressure amplitude")
    expected_spl = 20.0 * np.log10(amplitude[defined] / 20e-6)
    if not np.allclose(arrays["sound_pressure_level_db"][defined], expected_spl, rtol=1e-12, atol=1e-10):
        raise ValueError("Persisted SPL values contradict pressure amplitude")
    return arrays, by_name, result, simulation_input


def _field_evidence(repository, user_id: str, simulation_id: str) -> tuple[dict[str, dict], dict]:
    records = EvidenceRepository(repository=repository).list_scientific_for_simulation(user_id, simulation_id)
    numerical = [item for item in records if item["record_type"] == "scientific_numerical_result"]
    fields = {
        item["payload"].get("variable_name"): item for item in records
        if item["record_type"] == "scientific_field_result"
    }
    if len(numerical) != 1 or set(fields) != REQUIRED_ACOUSTIC_FIELDS:
        raise ValueError("Automatic acoustic execution evidence is incomplete")
    return fields, numerical[0]


def persist_acoustic_benchmark(*, repository, storage, user_id: str, simulation_id: str, mesh, model) -> dict:
    compiled = compile_design(benchmark_design())
    validate_benchmark_identity(compiled, mesh, model)
    arrays, field_records, result, simulation_input = load_acoustic_fields(
        repository=repository, storage=storage, user_id=user_id, simulation_id=simulation_id,
    )
    fingerprint = _hash(simulation_input.geometry)
    speed = authoritative_speed_of_sound(model)
    exact = analytical_plane_wave_pressure(np.asarray(mesh.nodes_m)[:, 0], speed)
    numerical = arrays["pressure_real_pa"] + 1j * arrays["pressure_imag_pa"]
    error = float(np.linalg.norm(numerical - exact) / max(np.linalg.norm(exact), 1e-30))
    field_evidence, numerical_evidence = _field_evidence(repository, user_id, simulation_id)
    checksums = {name: item.checksum_sha256 for name, item in field_records.items()}
    primary = field_records["pressure_real_pa"]
    unsigned = {
        "schema_version": "1.0", "benchmark_id": BENCHMARK_ID,
        "benchmark_version": BENCHMARK_VERSION, "authoritative": True,
        "eligibility_status": "eligible", "simulation_id": simulation_id,
        "solver_id": SOLVER_ID, "solver_version": SOLVER_VERSION,
        "input_fingerprint": fingerprint, "mesh_id": mesh.metadata.mesh_id,
        "mesh_hash": mesh.metadata.mesh_hash, "result_hash": result.reproducibility_hash,
        "field_evidence_id": field_evidence["pressure_real_pa"]["id"],
        "field_checksum_sha256": primary.checksum_sha256,
        "derived_parameters": {
            "frequency_hz": FREQUENCY_HZ, "speed_of_sound_m_s": speed,
            "material_snapshot_hash": model.materials[0].snapshot_hash,
            "physics_hash": model.physics_hash, "design_hash": model.design_hash,
            "geometry_fingerprint": model.geometry_fingerprint,
            "node_count": len(mesh.nodes_m), "field_set_hash": _hash(checksums),
        },
    }
    binding = BenchmarkCaseBinding(**unsigned, binding_hash=_hash(unsigned))
    conditioning = float(result.validation_metadata["reciprocal_condition_estimate"])
    passed = bool(
        error <= COMPLEX_L2_ERROR_LIMIT and result.residual <= RESIDUAL_TOLERANCE
        and result.validation_metadata["k_h_max"] <= MAX_K_H
        and conditioning >= MIN_RECIPROCAL_CONDITION_ESTIMATE
    )
    details = {
        "field_checksum_sha256": primary.checksum_sha256,
        "field_checksums": checksums, "node_count": len(mesh.nodes_m),
        "tetrahedron_count": len(mesh.tetrahedra), "mesh_id": mesh.metadata.mesh_id,
        "mesh_hash": mesh.metadata.mesh_hash, "physics_model_id": model.physics_model_id,
        "physics_hash": model.physics_hash, "material_snapshot_hash": model.materials[0].snapshot_hash,
        "speed_of_sound_m_s": speed, "frequency_hz": FREQUENCY_HZ,
        "h_max_m": result.summary_metrics["h_max_m"], "k_h_max": result.validation_metadata["k_h_max"],
        "normalized_algebraic_residual": result.residual,
        "reciprocal_condition_estimate": conditioning,
        "conditioning_criterion": f">= {MIN_RECIPROCAL_CONDITION_ESTIMATE}",
        "normalized_complex_l2_error": error,
    }
    job = repository.get_simulation_job(simulation_id)
    source_ids = [numerical_evidence["id"], *[field_evidence[name]["id"] for name in sorted(field_evidence)]]
    return EvidenceRepository(repository=repository).create_scientific_evidence(user_id, {
        "evidence_type": EvidenceType.BENCHMARK.value, "schema_version": "2.0",
        "experiment_id": job.experiment_id, "design_id": job.design_id,
        "simulation_id": simulation_id, "solver_id": SOLVER_ID, "solver_version": SOLVER_VERSION,
        "input_fingerprint": fingerprint, "result_hash": result.reproducibility_hash,
        "source_ids": source_ids, "status": "pass" if passed else "fail",
        "benchmark_id": BENCHMARK_ID, "metric_name": "normalized_complex_l2_error",
        "computed_value": error, "reference_value": 0.0, "absolute_error": error,
        "relative_error": error, "tolerance": COMPLEX_L2_ERROR_LIMIT, "passed": passed,
        "source_simulation_id": simulation_id, "benchmark_details": details,
        "case_binding": binding.model_dump(mode="json"),
        "limitations": ["Server-owned rectangular-duct plane-wave benchmark only."],
    })


def persist_acoustic_benchmark_if_eligible(**kwargs) -> dict | None:
    mesh, model = kwargs["mesh"], kwargs["model"]
    compiled = compile_design(benchmark_design())
    expected = build_physics_model(mesh, benchmark_physics_request())
    if (
        model.design_hash != compiled.design_hash
        or model.geometry_fingerprint != compiled.geometry_fingerprint
        or model.physics_hash != expected.physics_hash
    ):
        return None
    return persist_acoustic_benchmark(**kwargs)


def validate_persisted_acoustic_binding(model: BenchmarkEvidence, repository, user_id: str) -> bool:
    binding = model.case_binding
    if binding is None or binding.benchmark_id != BENCHMARK_ID or model.benchmark_id != BENCHMARK_ID:
        return False
    if _hash(binding.model_dump(mode="json", exclude={"binding_hash"})) != binding.binding_hash:
        return False
    job = repository.get_simulation_job(binding.simulation_id)
    result = repository.get_simulation_result(binding.simulation_id)
    simulation_input = repository.get_simulation_input(binding.simulation_id)
    if job is None or job.user_id != user_id or result is None or simulation_input is None:
        return False
    if (
        _hash(simulation_input.geometry) != binding.input_fingerprint
        or result.reproducibility_hash != binding.result_hash
        or result.solver_id != SOLVER_ID or result.solver_version != SOLVER_VERSION
        or result.validation_metadata.get("mesh_id") != binding.mesh_id
        or result.validation_metadata.get("mesh_hash") != binding.mesh_hash
        or model.input_fingerprint != binding.input_fingerprint or model.result_hash != binding.result_hash
    ):
        return False
    snapshots = simulation_input.geometry.get("material_snapshots", {})
    assignments = simulation_input.geometry.get("material_assignments", [])
    if len(assignments) != 1 or assignments[0].get("material_name") not in snapshots:
        return False
    snapshot = snapshots[assignments[0]["material_name"]]
    derived = binding.derived_parameters
    if (
        snapshot.get("snapshot_hash") != derived.get("material_snapshot_hash")
        or not math.isclose(_snapshot_property(snapshot, "speed_of_sound"), float(derived.get("speed_of_sound_m_s", math.nan)))
        or float(simulation_input.numerical_settings.get("frequency_hz", math.nan)) != FREQUENCY_HZ
        or simulation_input.geometry.get("physics_model_hash") != derived.get("physics_hash")
        or simulation_input.geometry.get("design_hash") != derived.get("design_hash")
        or simulation_input.geometry.get("geometry_fingerprint") != derived.get("geometry_fingerprint")
    ):
        return False
    fields = repository.list_field_results(binding.simulation_id)
    checksums = {item.variable_name: item.checksum_sha256 for item in fields if item.user_id == user_id}
    if set(checksums) != REQUIRED_ACOUSTIC_FIELDS or _hash(checksums) != derived.get("field_set_hash"):
        return False
    dependencies = [EvidenceRepository(repository=repository).get(item, user_id) for item in model.source_ids]
    if not any(item and item["record_type"] == "scientific_numerical_result" for item in dependencies):
        return False
    evidence_checksums = {
        item["payload"].get("variable_name"): item["payload"].get("checksum_sha256")
        for item in dependencies if item and item["record_type"] == "scientific_field_result"
    }
    return evidence_checksums == checksums


def persist_acoustic_refinement_evidence(*, repository, user_id: str, simulation_ids: list[str]) -> dict:
    if len(simulation_ids) != 3 or len(set(simulation_ids)) != 3:
        raise ValueError("Exactly three distinct acoustic simulations are required")
    evidence = EvidenceRepository(repository=repository)
    rows = []
    common_science = []
    source_ids: list[str] = []
    for level, simulation_id in zip(("coarse", "medium", "fine"), simulation_ids):
        job = repository.get_simulation_job(simulation_id)
        result = repository.get_simulation_result(simulation_id)
        simulation_input = repository.get_simulation_input(simulation_id)
        if job is None or job.user_id != user_id:
            raise LookupError("Acoustic refinement simulation not found")
        if result is None or result.solver_id != SOLVER_ID or result.status != "completed" or simulation_input is None:
            raise ValueError("Acoustic refinement requires completed authoritative results")
        target = float(simulation_input.geometry["fem_refinement"]["mesh"]["specification"]["target_size"]["value"])
        if not math.isclose(target, REFINEMENT_TARGETS_MM[level], rel_tol=0, abs_tol=1e-12):
            raise ValueError("Acoustic refinement target sizes must be the fixed 20/15/10 mm sequence")
        records = evidence.list_scientific_for_simulation(user_id, simulation_id)
        benchmarks = []
        for record in records:
            if record["record_type"] != "scientific_benchmark":
                continue
            candidate = BenchmarkEvidence.model_validate(record["payload"])
            if validate_persisted_acoustic_binding(candidate, repository, user_id):
                benchmarks.append((record, candidate))
        if len(benchmarks) != 1:
            raise ValueError("Each refinement level requires one valid server-owned acoustic benchmark")
        benchmark_record, benchmark = benchmarks[0]
        grouped = [item for item in records if item["record_type"] in {
            "scientific_numerical_result", "scientific_run_convergence",
        }]
        if len([item for item in grouped if item["record_type"] == "scientific_numerical_result"]) != 1:
            raise ValueError("Acoustic refinement numerical evidence is incomplete")
        source_ids.extend([item["id"] for item in grouped] + [benchmark_record["id"]])
        details = benchmark.benchmark_details
        rows.append({
            "level": level, "simulation_id": simulation_id, "target_size_mm": target,
            "mesh_id": details["mesh_id"], "mesh_hash": details["mesh_hash"],
            "physics_hash": details["physics_hash"], "nodes": details["node_count"],
            "tetrahedra": details["tetrahedron_count"], "h_max": details["h_max_m"],
            "k_h_max": details["k_h_max"], "residual": details["normalized_algebraic_residual"],
            "reciprocal_condition_estimate": details["reciprocal_condition_estimate"],
            "complex_l2_error": benchmark.computed_value,
            "max_amplitude": result.summary_metrics["max_pressure_amplitude_pa"],
            "mean_amplitude": result.summary_metrics["mean_pressure_amplitude_pa"],
            "max_spl_db": result.summary_metrics["max_spl_db"],
            "input_fingerprint": result.validation_metadata["input_fingerprint"],
            "result_hash": result.reproducibility_hash,
        })
        common_science.append({
            "experiment_id": job.experiment_id, "design_id": job.design_id,
            "design_hash": simulation_input.geometry["design_hash"],
            "geometry_fingerprint": simulation_input.geometry["geometry_fingerprint"],
            "materials": simulation_input.geometry["material_snapshots"],
            "boundary_conditions": simulation_input.boundary_conditions,
            "numerical_settings": simulation_input.numerical_settings,
            "solver_id": result.solver_id, "solver_version": result.solver_version,
        })
    if any(item != common_science[0] for item in common_science[1:]):
        raise ValueError("Only the authoritative acoustic mesh may change across refinement levels")
    errors = np.asarray([item["complex_l2_error"] for item in rows], dtype=float)
    lengths = np.asarray([item["h_max"] for item in rows], dtype=float)
    monotonic = bool(errors[0] > errors[1] > errors[2])
    observed_order = float(np.polyfit(np.log(lengths), np.log(errors), 1)[0]) if monotonic else None
    every_gate = all(
        item["k_h_max"] <= MAX_K_H and item["residual"] <= RESIDUAL_TOLERANCE
        and item["reciprocal_condition_estimate"] >= MIN_RECIPROCAL_CONDITION_ESTIMATE
        for item in rows
    )
    passed = bool(
        every_gate and monotonic and observed_order is not None and observed_order > MIN_OBSERVED_ORDER
        and errors[2] <= COMPLEX_L2_ERROR_LIMIT and errors[2] < errors[0]
    )
    comparison_hash = _hash(common_science[0])
    payload = {
        "evidence_type": EvidenceType.REFINEMENT_CONVERGENCE.value, "schema_version": "2.0",
        "experiment_id": common_science[0]["experiment_id"], "design_id": common_science[0]["design_id"],
        "simulation_id": simulation_ids[-1], "solver_id": SOLVER_ID, "solver_version": SOLVER_VERSION,
        "input_fingerprint": rows[-1]["input_fingerprint"], "result_hash": rows[-1]["result_hash"],
        "source_ids": sorted(set(source_ids)), "status": "completed" if passed else "not_converged",
        "selected_metric": "normalized_complex_l2_error",
        "refinement_parameter": "geometry.fem_refinement.mesh.specification.target_size.value",
        "comparison_hash": comparison_hash, "convergence_threshold": COMPLEX_L2_ERROR_LIMIT,
        "coarse_to_medium_change": abs(errors[1] - errors[0]) / errors[1],
        "medium_to_fine_change": abs(errors[2] - errors[1]) / errors[2],
        "passed": passed, "metric_source": "benchmark_evidence", "benchmark_id": BENCHMARK_ID,
        "levels": [{
            "level": item["level"], "simulation_id": item["simulation_id"],
            "value": item["complex_l2_error"], "refinement_value": item["target_size_mm"],
            "input_fingerprint": item["input_fingerprint"], "solver_id": SOLVER_ID,
            "solver_version": SOLVER_VERSION,
            "configuration": {key: value for key, value in item.items() if key not in {"level", "simulation_id", "input_fingerprint"}},
        } for item in rows],
        "refinement_details": {
            "benchmark_id": BENCHMARK_ID, "benchmark_version": BENCHMARK_VERSION,
            "design_hash": common_science[0]["design_hash"],
            "geometry_fingerprint": common_science[0]["geometry_fingerprint"],
            "material_snapshot_identity": common_science[0]["materials"],
            "frequency_hz": FREQUENCY_HZ, "boundary_semantics": common_science[0]["boundary_conditions"],
            "levels": rows, "refinement_monotonic": monotonic, "observed_order": observed_order,
            "minimum_observed_order": MIN_OBSERVED_ORDER,
            "coarse_to_fine_improvement_ratio": float(errors[0] / errors[2]),
            "thresholds": {"fine_l2_error": COMPLEX_L2_ERROR_LIMIT, "k_h_max": MAX_K_H,
                           "residual": RESIDUAL_TOLERANCE,
                           "minimum_reciprocal_condition_estimate": MIN_RECIPROCAL_CONDITION_ESTIMATE},
        },
        "warnings": [] if passed else ["Authoritative acoustic refinement criterion was not satisfied."],
        "limitations": ["Convergence applies only to the server-owned rectangular-duct plane-wave benchmark."],
    }
    return evidence.create_scientific_evidence(user_id, payload)
