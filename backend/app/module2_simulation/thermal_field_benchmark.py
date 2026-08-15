"""Authoritative persisted-field benchmarks for CAD TET4 thermal results."""
from __future__ import annotations

import numpy as np

from app.module2_simulation.field_results import load_field_artifact
from app.v2.evidence_models import EvidenceType
from app.v2.repository import EvidenceRepository

QUADRATURE_RULE_ID = "duffy_gauss_legendre_4x4x4_v1"
QUADRATURE_DEGREE = 4


def tetra_quadrature_degree4():
    """Positive 4x4x4 Duffy Gauss-Legendre rule, exact through degree four."""
    roots, weights = np.polynomial.legendre.leggauss(4)
    roots = (roots + 1.0) / 2.0; weights = weights / 2.0
    for u, wu in zip(roots, weights):
        for v, wv in zip(roots, weights):
            for w, ww in zip(roots, weights):
                yield np.array((u, (1-u)*v, (1-u)*(1-v)*w)), wu * wv * ww * (1-u)**2 * (1-v)


def linear_prism_field_error(*, repository, storage, user_id: str, simulation_id: str, mesh,
                             cold_k: float, hot_k: float) -> dict:
    """Resolve only private persisted truth; caller-supplied scalar values are ignored."""
    job = repository.get_simulation_job(simulation_id)
    result = repository.get_simulation_result(simulation_id)
    if job is None or job.user_id != user_id or result is None or result.solver_id != "thermal_fem_3d_v1":
        raise LookupError("Thermal FEM simulation not found")
    if result.validation_metadata.get("mesh_hash") != mesh.metadata.mesh_hash:
        raise ValueError("Persisted result mesh identity does not match authoritative mesh")
    field = next((item for item in repository.list_field_results(simulation_id) if item.variable_name == "temperature"), None)
    if field is None or field.user_id != user_id:
        raise ValueError("Persisted nodal temperature field is unavailable")
    values = load_field_artifact(storage, field.storage_object_key, field.checksum_sha256)
    x = np.asarray(mesh.nodes_m, dtype=float)[:, 0]
    reference = cold_k + (hot_k - cold_k) * (x - x.min()) / (x.max() - x.min())
    error = values - reference
    return {"field_checksum_sha256": field.checksum_sha256, "mesh_hash": mesh.metadata.mesh_hash,
        "node_count": int(values.size), "max_absolute_error_k": float(np.abs(error).max()),
        "normalized_l2_error": float(np.linalg.norm(error) / max(np.linalg.norm(reference), 1e-15)),
        "formula": "linear_prism_temperature_v1", "formula_version": "1"}


def quadratic_prism_field_error(*, repository, storage, user_id: str, simulation_id: str, mesh,
                                temperature_k: float, source_w_m3: float, conductivity_w_m_k: float) -> dict:
    base = linear_prism_field_error(repository=repository, storage=storage, user_id=user_id,
        simulation_id=simulation_id, mesh=mesh, cold_k=temperature_k, hot_k=temperature_k)
    field = next(item for item in repository.list_field_results(simulation_id) if item.variable_name == "temperature")
    values = load_field_artifact(storage, field.storage_object_key, field.checksum_sha256)
    nodes = np.asarray(mesh.nodes_m, dtype=float); x = nodes[:, 0]; length = x.max() - x.min(); local = x - x.min()
    reference = temperature_k + source_w_m3 / (2 * conductivity_w_m_k) * local * (length - local)
    error = values - reference
    error_squared = reference_squared = 0.0
    for tetrahedron in mesh.tetrahedra:
        coordinates = nodes[list(tetrahedron)]; nodal = values[list(tetrahedron)]
        volume = abs(float(np.linalg.det(np.stack((coordinates[1]-coordinates[0], coordinates[2]-coordinates[0], coordinates[3]-coordinates[0]))) / 6.0))
        for barycentric, weight in tetra_quadrature_degree4():
            shape = np.array((1.0 - barycentric.sum(), *barycentric))
            point = shape @ coordinates; local_x = point[0] - x.min()
            exact = temperature_k + source_w_m3 / (2 * conductivity_w_m_k) * local_x * (length - local_x)
            difference = float(shape @ nodal - exact)
            error_squared += volume * weight * difference * difference
            reference_squared += volume * weight * exact * exact
    return {**base, "max_nodal_absolute_error_k": float(np.abs(error).max()),
        "absolute_integrated_l2_error_k_sqrt_m3": float(np.sqrt(error_squared)),
        "normalized_l2_error": float(np.sqrt(error_squared / max(reference_squared, 1e-30))),
        "quadrature_rule": QUADRATURE_RULE_ID, "quadrature_degree": QUADRATURE_DEGREE,
        "element_count": len(mesh.tetrahedra), "formula": "quadratic_prism_temperature_v1", "formula_version": "1"}


def persist_linear_prism_benchmark(*, repository, storage, user_id: str, simulation_id: str, mesh,
                                   cold_k: float, hot_k: float, tolerance: float = 1e-8) -> dict:
    details = linear_prism_field_error(repository=repository, storage=storage, user_id=user_id,
        simulation_id=simulation_id, mesh=mesh, cold_k=cold_k, hot_k=hot_k)
    job = repository.get_simulation_job(simulation_id); result = repository.get_simulation_result(simulation_id)
    evidence = EvidenceRepository(repository=repository)
    source_ids = [record["id"] for record in evidence.list_scientific_for_simulation(user_id, simulation_id)
                  if record["record_type"] in {"scientific_numerical_result", "scientific_field_result"}]
    passed = details["normalized_l2_error"] <= tolerance
    return evidence.create_scientific_evidence(user_id, {"evidence_type": EvidenceType.BENCHMARK.value,
        "experiment_id": job.experiment_id, "design_id": job.design_id, "simulation_id": simulation_id,
        "solver_id": result.solver_id, "solver_version": result.solver_version,
        "input_fingerprint": result.validation_metadata["input_fingerprint"], "result_hash": result.reproducibility_hash,
        "source_ids": source_ids, "status": "pass" if passed else "fail", "benchmark_id": "thermal_fem_linear_prism_field",
        "metric_name": "normalized_l2_error", "computed_value": details["normalized_l2_error"], "reference_value": 0.0,
        "absolute_error": details["max_absolute_error_k"], "relative_error": details["normalized_l2_error"],
        "tolerance": tolerance, "passed": passed, "source_simulation_id": simulation_id,
        "benchmark_details": details, "limitations": ["Linear-prism analytical field only."]})


def persist_quadratic_prism_benchmark(*, repository, storage, user_id: str, simulation_id: str, mesh,
                                      temperature_k: float, source_w_m3: float, conductivity_w_m_k: float,
                                      tolerance: float = 1e-2) -> dict:
    details = quadratic_prism_field_error(repository=repository, storage=storage, user_id=user_id,
        simulation_id=simulation_id, mesh=mesh, temperature_k=temperature_k, source_w_m3=source_w_m3,
        conductivity_w_m_k=conductivity_w_m_k)
    job = repository.get_simulation_job(simulation_id); result = repository.get_simulation_result(simulation_id)
    evidence = EvidenceRepository(repository=repository)
    source_ids = [record["id"] for record in evidence.list_scientific_for_simulation(user_id, simulation_id)
                  if record["record_type"] in {"scientific_numerical_result", "scientific_field_result"}]
    passed = details["normalized_l2_error"] <= tolerance
    return evidence.create_scientific_evidence(user_id, {"evidence_type": "benchmark", "experiment_id": job.experiment_id,
        "design_id": job.design_id, "simulation_id": simulation_id, "solver_id": result.solver_id,
        "solver_version": result.solver_version, "input_fingerprint": result.validation_metadata["input_fingerprint"],
        "result_hash": result.reproducibility_hash, "source_ids": source_ids, "status": "pass" if passed else "fail",
        "benchmark_id": "thermal_fem_uniform_generation_prism", "metric_name": "normalized_l2_error",
        "computed_value": details["normalized_l2_error"], "reference_value": 0.0,
        "absolute_error": details["absolute_integrated_l2_error_k_sqrt_m3"], "relative_error": details["normalized_l2_error"],
        "tolerance": tolerance, "passed": passed, "source_simulation_id": simulation_id,
        "benchmark_details": details, "limitations": ["Uniform-source rectangular-prism analytical field only."]})
