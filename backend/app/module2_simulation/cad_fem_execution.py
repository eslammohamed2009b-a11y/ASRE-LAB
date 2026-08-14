"""Phase-3B adapter from authoritative CAD physics records to Phase-1 results."""
from __future__ import annotations

import hashlib
import json

import numpy as np

from app.core.repository import SimulationResultRecord
from app.module2_simulation.cad_fem_solvers import CAD_FEM_SOLVERS, FEMError
from app.module2_simulation.evidence_lifecycle import persist_automatic_evidence
from app.module2_simulation.field_results import persist_field_result
from app.module2_simulation.geometry_physics_schemas import PhysicsModelV1
from app.module2_simulation.meshing import GeneratedMesh
from app.module2_simulation.solver_registry import SOLVER_REGISTRY
from app.module2_simulation.schemas import ImplementationStatus


class FEMExecutionError(ValueError):
    pass


_FAMILY_BY_SOLVER = {
    "thermal_fem_3d_v1": "thermal", "structural_linear_elasticity_3d_v1": "structural",
    "modal_fem_3d_v1": "modal",
}


def _stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _validate_solver(solver_id: str, model: PhysicsModelV1, mesh: GeneratedMesh) -> None:
    entry = SOLVER_REGISTRY.get(solver_id)
    if solver_id not in CAD_FEM_SOLVERS or entry is None or entry.implementation_status != ImplementationStatus.REAL:
        raise FEMExecutionError("Requested solver is not an implemented authoritative CAD FEM solver")
    if _FAMILY_BY_SOLVER[solver_id] != model.analysis_family.value:
        raise FEMExecutionError("Requested solver is incompatible with this PhysicsModel analysis family")
    if not entry.consumes_authoritative_cad or entry.required_mesh_dimension != mesh.metadata.dimension:
        raise FEMExecutionError("Solver capability does not accept this authoritative mesh")
    if "tetra4" not in entry.accepted_element_types or "tetra4" not in mesh.metadata.element_types:
        raise FEMExecutionError("Solver and mesh element types are incompatible")
    supported = set(entry.supported_boundary_conditions)
    unsupported = [bc.bc_type for bc in model.boundary_conditions if bc.bc_type not in supported]
    if unsupported:
        raise FEMExecutionError("PhysicsModel contains unsupported boundary conditions")
    if model.mesh_hash != mesh.metadata.mesh_hash or model.design_hash != mesh.metadata.design_hash:
        raise FEMExecutionError("PhysicsModel scientific identities do not match the authoritative mesh")


def _axes(values: np.ndarray, location: str) -> list[dict]:
    names = ["node_id" if location == "nodal" else "element_id", "component", "mode_index"]
    units = ["index", "index", "index"]
    return [{"name": names[index], "unit": units[index], "values": list(range(size))} for index, size in enumerate(values.shape)]


def _fields(solution, mesh: GeneratedMesh):
    for name, (unit, values) in solution.fields.items():
        array = np.asarray(values, dtype=float)
        if name == "mode_shapes":
            frequencies = solution.fields["natural_frequencies"][1]
            eigenvalues = solution.fields["eigenvalues"][1]
            for index, mode in enumerate(array):
                yield f"mode_shape_{index + 1:03d}", unit, mode, "nodal", {
                    "location_type": "nodal", "mode_index": index + 1,
                    "natural_frequency_hz": float(frequencies[index]), "eigenvalue": float(eigenvalues[index]),
                    "normalization": solution.diagnostics["normalization"], "mesh_hash": mesh.metadata.mesh_hash,
                }
        elif name not in {"natural_frequencies", "eigenvalues"}:
            location = "nodal" if name in {"temperature", "displacement"} else "elemental"
            yield name, unit, array, location, {"location_type": location, "mesh_hash": mesh.metadata.mesh_hash,
                "component_order": "xx,yy,zz,xy,yz,zx" if name in {"strain", "stress"} else None}


def execute_cad_fem(*, repository, storage, user_id: str, experiment_id: str, design_id: str | None,
                    mesh: GeneratedMesh, model: PhysicsModelV1, solver_id: str, idempotency_key: str) -> str:
    """Run once per stable scientific identity and repair missing fields/evidence on retry."""
    _validate_solver(solver_id, model, mesh)
    existing = repository.get_simulation_job_by_idempotency_key(user_id, idempotency_key)
    if existing is not None:
        if existing.solver_id != solver_id or existing.experiment_id != experiment_id:
            raise FEMExecutionError("Idempotency key belongs to a different scientific request")
        if repository.get_simulation_result(existing.id) is not None:
            if not repository.list_field_results(existing.id):
                raise FEMExecutionError("A legacy partial FEM result cannot be repaired without its field solution")
            persist_automatic_evidence(repository, existing.id)
        return existing.id
    simulation_id = repository.create_simulation_job(user_id, solver_id, experiment_id, design_id, idempotency_key)
    repository.update_simulation_job(simulation_id, status="running", progress_percent=5)
    material_snapshots = {item.material_name: item.model_dump(mode="json") for item in model.materials}
    evidence_material_properties = {
        f"{item.material_name}.{property_.name}": property_.value
        for item in model.materials for property_ in item.properties
    }
    input_payload = {"physics_model_hash": model.physics_hash, "design_hash": model.design_hash,
        "geometry_fingerprint": model.geometry_fingerprint, "mesh_hash": model.mesh_hash,
        "mesh_id": model.mesh_id, "material_snapshots": material_snapshots,
        "boundary_conditions": [item.model_dump(mode="json") for item in model.boundary_conditions],
        "numerical_settings": model.numerical_settings.model_dump(mode="json")}
    input_payload["fem_refinement"] = {
        "design_hash": model.design_hash, "geometry_fingerprint": model.geometry_fingerprint,
        "analysis_family": model.analysis_family.value, "materials": material_snapshots,
        "boundary_conditions": input_payload["boundary_conditions"],
        "numerical_settings": input_payload["numerical_settings"],
        "mesh": {"mesh_id": model.mesh_id, "mesh_hash": model.mesh_hash,
                 "specification": mesh.metadata.specification.model_dump(mode="json")},
    }
    repository.record_simulation_input(simulation_id, "PhysicsModelV1", material_snapshots, {"length": "m"}, {},
        input_payload["boundary_conditions"], input_payload["numerical_settings"], input_payload)
    try:
        solution = getattr(__import__("app.module2_simulation.cad_fem_solvers", fromlist=[CAD_FEM_SOLVERS[solver_id]]), CAD_FEM_SOLVERS[solver_id])(mesh, model)
        residual_key = "maximum_eigenpair_residual" if solver_id == "modal_fem_3d_v1" else "algebraic_residual"
        residual = float(solution.diagnostics[residual_key])
        tolerance = float(getattr(model.numerical_settings, "tolerance", 1e-8))
        result_hash = _stable_hash({"input": input_payload, "solver_id": solver_id, "summary": solution.summary,
            "residual": residual, "fields": {name: hashlib.sha256(np.asarray(value[1], dtype='<f8').tobytes()).hexdigest() for name, value in solution.fields.items()}})
        metadata = {"input_fingerprint": _stable_hash(input_payload), "material_properties_used": evidence_material_properties,
            "validation_status": SOLVER_REGISTRY[solver_id].validation_status.value, "physics_model_hash": model.physics_hash,
            "mesh_hash": model.mesh_hash, "design_hash": model.design_hash, "geometry_fingerprint": model.geometry_fingerprint,
            "convergence_metric": "generalized_eigenpair_residual" if solver_id == "modal_fem_3d_v1" else "algebraic_residual"}
        repository.record_simulation_result(SimulationResultRecord(simulation_id=simulation_id, solver_id=solver_id,
            solver_version=SOLVER_REGISTRY[solver_id].version, governing_equations=SOLVER_REGISTRY[solver_id].governing_equations,
            warnings=list(solution.warnings), converged=residual <= tolerance, residual=residual, iteration_count=1,
            tolerance=tolerance, summary_metrics=solution.summary, numerical_method=str(solution.diagnostics["solver_method"]),
            validation_metadata=metadata, elapsed_time_seconds=float(solution.diagnostics["solve_time_seconds"]),
            reproducibility_hash=result_hash, source_design_id=design_id, status="completed" if residual <= tolerance else "failed"))
        for name, unit, values, location, metadata in _fields(solution, mesh):
            persist_field_result(repository=repository, storage=storage, user_id=user_id, experiment_id=experiment_id,
                simulation_id=simulation_id, variable_name=name, unit=unit, axes=_axes(values, location), values=values,
                solver_id=solver_id, solver_version=SOLVER_REGISTRY[solver_id].version, grid_metadata=metadata)
        repository.update_simulation_job(simulation_id, status="completed" if residual <= tolerance else "failed", progress_percent=100)
        if residual <= tolerance:
            persist_automatic_evidence(repository, simulation_id)
        return simulation_id
    except (FEMError, FEMExecutionError, ValueError) as exc:
        repository.update_simulation_job(simulation_id, status="failed", progress_percent=100,
            error_code=getattr(exc, "code", "FEM_EXECUTION_FAILED"), safe_error_message=str(exc))
        raise
