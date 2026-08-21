"""Server-only scientific evidence derived from persisted solver truth."""
from __future__ import annotations

from app.v2.repository import EvidenceRepository


def _convergence_semantics(result) -> tuple[str, str, str, bool | None]:
    metadata = result.validation_metadata
    declared = metadata.get("convergence_metric")
    if result.solver_id == "cfd_openfoam_laminar_internal_3d_v1":
        conditions = metadata.get("convergence_conditions", {})
        required = {
            "simple_converged", "final_u_residual", "u_tolerance", "final_p_residual",
            "p_tolerance", "normalized_mass_imbalance", "mass_imbalance_limit", "finite_reviewed_fields",
        }
        passed = bool(required <= set(conditions) and conditions["simple_converged"]
                      and conditions["finite_reviewed_fields"]
                      and conditions["final_u_residual"] <= conditions["u_tolerance"]
                      and conditions["final_p_residual"] <= conditions["p_tolerance"]
                      and conditions["normalized_mass_imbalance"] <= conditions["mass_imbalance_limit"])
        return (
            "cfd_combined_residual_mass_conservation",
            "OpenFOAM SIMPLE converged; final U and p residuals <= requested tolerance; "
            "normalized mass imbalance <= 1e-3; reviewed U, p, and phi fields finite",
            "completed" if passed else "not_converged", passed,
        )
    if declared == "maximum_iteration_update":
        return (
            "maximum_iteration_update", "maximum iteration update <= requested tolerance",
            "completed" if result.converged else "not_converged", result.converged,
        )
    if result.solver_id == "pyramid_thermal_conduction_v1":
        return (
            "maximum_iteration_update", "maximum iteration update <= requested tolerance",
            "completed" if result.converged else "not_converged", result.converged,
        )
    if (
        result.solver_id == "modal_eigen_1d_v1"
        and result.residual is None
        and result.tolerance is None
    ):
        return "direct_solver", "iterative convergence is not applicable", "not_applicable", None
    metric_types = {
        "thermal_conduction_v1": "algebraic_residual",
        "thermal_fem_3d_v1": "algebraic_residual",
        "structural_linear_1d_v1": "algebraic_residual",
        "structural_linear_elasticity_3d_v1": "algebraic_residual",
        "modal_fem_3d_v1": "generalized_eigenpair_residual",
        "acoustic_duct_1d_v1": "algebraic_system_residual",
        "electrostatic_rectangular_2d_v1": "algebraic_residual",
        "cfd_laminar_channel_2d_v1": "bounded_mass_and_momentum_residual",
    }
    metric_type = declared or metric_types.get(result.solver_id)
    if metric_type is None or result.tolerance is None:
        return "unavailable", "convergence information was not persisted", "not_run", None
    return (
        metric_type, f"{metric_type} <= requested tolerance",
        "completed" if result.converged else "not_converged", result.converged,
    )


def persist_automatic_evidence(repository, simulation_id: str) -> list[dict]:
    """Create idempotent evidence exclusively from already-persisted records."""
    job = repository.get_simulation_job(simulation_id)
    result = repository.get_simulation_result(simulation_id)
    simulation_input = repository.get_simulation_input(simulation_id)
    if job is None or result is None or simulation_input is None:
        raise ValueError("Persisted job, input, and result are required for automatic evidence")
    fingerprint = result.validation_metadata.get("input_fingerprint")
    material_snapshot = result.validation_metadata.get("material_properties_used")
    if not fingerprint or not result.reproducibility_hash or not isinstance(material_snapshot, dict):
        raise ValueError("Final result provenance is incomplete")
    validation_status = result.validation_metadata.get("validation_status")
    if validation_status is None:
        raise ValueError("Solver validation metadata is unavailable")

    evidence = EvidenceRepository(repository=repository)
    common = {
        "schema_version": "2.0", "experiment_id": job.experiment_id,
        "design_id": job.design_id, "simulation_id": simulation_id,
        "solver_id": result.solver_id, "solver_version": result.solver_version,
        "input_fingerprint": fingerprint, "result_hash": result.reproducibility_hash,
    }
    numerical = evidence.create_scientific_evidence(job.user_id, {
        **common, "evidence_type": "numerical_result",
        "status": "completed" if result.status == "completed" else "fail",
        "summary_metrics": result.summary_metrics,
        "material_snapshot": material_snapshot,
        "numerical_method": result.numerical_method,
        "convergence": {
            "converged": result.converged, "iterations": result.iteration_count,
            "metric": result.residual, "tolerance": result.tolerance,
        },
        "warnings": result.warnings,
    })
    records = [numerical]

    for field in repository.list_field_results(simulation_id):
        records.append(evidence.create_scientific_evidence(job.user_id, {
            **common, "evidence_type": "field_result",
            "status": "completed" if result.status == "completed" else "fail",
            "source_ids": [numerical["id"]], "variable_name": field.variable_name,
            "unit": field.unit, "array_shape": field.array_shape,
            "checksum_sha256": field.checksum_sha256,
            "format": field.format, "format_version": field.format_version,
            "location_type": field.grid_metadata.get("location_type"),
            "mesh_hash": field.grid_metadata.get("mesh_hash"),
            "quantity": field.grid_metadata.get("quantity"),
            "field_solver_id": field.grid_metadata.get("solver_id"),
            "field_solver_version": field.grid_metadata.get("solver_version"),
        }))

    rules = [{
        "rule_id": "solver_domain_validation", "status": "pass", "severity": "info",
        "technical_reason": "The persisted request passed the real solver geometry, boundary, and material validators.",
    }]
    if validation_status == "partially_validated":
        rules.append({
            "rule_id": "solver_validation_scope", "status": "warning", "severity": "warning",
            "technical_reason": "The solver registry marks this bounded implementation as partially validated.",
        })
    elif validation_status == "unvalidated":
        rules.append({
            "rule_id": "solver_validation_scope", "status": "fail", "severity": "error",
            "technical_reason": "The solver registry does not establish a validated scientific domain.",
        })
    rules.extend({
        "rule_id": f"solver_warning_{index + 1}", "status": "warning", "severity": "warning",
        "technical_reason": warning,
    } for index, warning in enumerate(result.warnings))
    validity_status = (
        "invalid" if validation_status == "unvalidated"
        else "valid_with_warnings"
        if result.warnings or validation_status == "partially_validated"
        else "valid"
    )
    evaluated_inputs = {
        "material_name": simulation_input.material_name,
        "material_properties": simulation_input.material_properties,
        "units": simulation_input.units,
        "initial_conditions": simulation_input.initial_conditions,
        "boundary_conditions": simulation_input.boundary_conditions,
        "numerical_settings": simulation_input.numerical_settings,
        "geometry": simulation_input.geometry,
    }
    records.append(evidence.create_scientific_evidence(job.user_id, {
        **common, "evidence_type": "validity", "status": validity_status,
        "evaluated_inputs": evaluated_inputs, "rules": rules,
        "warnings": result.warnings,
    }))

    metric_type, criterion, convergence_status, passed = _convergence_semantics(result)
    records.append(evidence.create_scientific_evidence(job.user_id, {
        **common, "evidence_type": "run_convergence", "status": convergence_status,
        "source_ids": [numerical["id"]], "metric_type": metric_type,
        "metric_value": result.residual, "tolerance": result.tolerance,
        "iterations": result.iteration_count, "criterion": criterion, "passed": passed,
    }))
    return records


def list_simulation_evidence(repository, simulation_id: str, user_id: str) -> list[dict]:
    job = repository.get_simulation_job(simulation_id)
    if job is None or job.user_id != user_id:
        raise LookupError("Simulation not found")
    records = EvidenceRepository(repository=repository).list_scientific_for_simulation(
        user_id, simulation_id
    )
    return [{
        "id": record["id"], "record_type": record["record_type"],
        "status": record["status"], "schema_version": record["schema_version"],
        "experiment_id": record.get("experiment_id"),
        "simulation_id": record.get("simulation_id"), "payload": record["payload"],
        "created_at": record["created_at"],
    } for record in records]
