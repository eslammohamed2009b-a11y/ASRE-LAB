from __future__ import annotations

import pytest

from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.cad_fem_execution import FEMExecutionError, execute_cad_fem
from app.module2_simulation.geometry_physics_schemas import PhysicsModelRequest
from app.module2_simulation.meshing import generate_mesh
from app.module2_simulation.physics_model import build_physics_model
from app.module2_simulation.evidence_lifecycle import list_simulation_evidence
from tests.integration.test_geometry_physics_foundation import authoritative_box, domain, mesh_spec

pytestmark = pytest.mark.integration


def _model(mesh, family: str = "thermal"):
    payload = {
        "analysis_family": family, "domains": [domain().model_dump(mode="json")],
        "material_assignments": [{"domain_id": "solid_domain", "material_name": "steel"}],
        "boundary_conditions": ([
            {"bc_type": "temperature", "bc_id": "left", "semantic_region": "low_end", "temperature_k": 300},
            {"bc_type": "temperature", "bc_id": "right", "semantic_region": "high_end", "temperature_k": 400},
            {"bc_type": "heat_flux", "bc_id": "insulated", "semantic_region": "walls", "heat_flux_w_m2": 0},
        ] if family == "thermal" else [
            {"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"},
            {"bc_type": "force", "bc_id": "load", "semantic_region": "high_end", "force_n": [100, 0, 0]},
        ]),
        "numerical_settings": {"settings_type": "steady_thermal"} if family == "thermal" else {"settings_type": "linear_static"},
        "expected_outputs": ["temperature"] if family == "thermal" else ["displacement", "stress"],
    }
    return build_physics_model(mesh, PhysicsModelRequest.model_validate(payload))


def test_authoritative_thermal_execution_persists_fields_evidence_and_is_idempotent(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "records.sqlite"); storage = LocalFileStorage(tmp_path / "private")
    owner = "fem-owner"; experiment = repo.create_experiment(owner, "FEM")
    mesh = generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec())
    model = _model(mesh)
    simulation = execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment,
        design_id=None, mesh=mesh, model=model, solver_id="thermal_fem_3d_v1", idempotency_key="same-science")
    assert execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment,
        design_id=None, mesh=mesh, model=model, solver_id="thermal_fem_3d_v1", idempotency_key="same-science") == simulation
    assert [field.variable_name for field in repo.list_field_results(simulation)] == ["temperature", "temperature_gradient"]
    assert {item["record_type"] for item in list_simulation_evidence(repo, simulation, owner)} == {
        "scientific_numerical_result", "scientific_field_result", "scientific_validity", "scientific_run_convergence"}
    assert repo.get_simulation_result(simulation).validation_metadata["mesh_hash"] == mesh.metadata.mesh_hash
    with pytest.raises(FEMExecutionError):
        execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment,
            design_id=None, mesh=mesh, model=model, solver_id="structural_linear_elasticity_3d_v1", idempotency_key="bad")


def test_structural_fields_are_explicit_and_owner_records_remain_private(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "records.sqlite"); storage = LocalFileStorage(tmp_path / "private")
    owner = "fem-owner"; experiment = repo.create_experiment(owner, "FEM")
    mesh = generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec())
    simulation = execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment, design_id=None,
        mesh=mesh, model=_model(mesh, "structural"), solver_id="structural_linear_elasticity_3d_v1", idempotency_key="structural")
    fields = {field.variable_name: field for field in repo.list_field_results(simulation)}
    assert set(fields) == {"displacement", "strain", "stress", "von_mises_stress"}
    assert fields["stress"].grid_metadata["component_order"] == "xx,yy,zz,xy,yz,zx"
    with pytest.raises(LookupError):
        list_simulation_evidence(repo, simulation, "other-owner")


def test_modal_execution_persists_one_mass_normalized_field_per_mode(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "records.sqlite"); storage = LocalFileStorage(tmp_path / "private")
    owner = "fem-owner"; experiment = repo.create_experiment(owner, "FEM")
    mesh = generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec())
    request = PhysicsModelRequest.model_validate({"analysis_family": "modal", "domains": [domain().model_dump(mode="json")],
        "material_assignments": [{"domain_id": "solid_domain", "material_name": "steel"}],
        "boundary_conditions": [{"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"}],
        "numerical_settings": {"settings_type": "modal_eigen", "requested_modes": 3}, "expected_outputs": ["eigenfrequency", "mode_shape"]})
    simulation = execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment, design_id=None,
        mesh=mesh, model=build_physics_model(mesh, request), solver_id="modal_fem_3d_v1", idempotency_key="modal")
    fields = repo.list_field_results(simulation)
    assert [field.variable_name for field in fields] == ["mode_shape_001", "mode_shape_002", "mode_shape_003"]
    assert all(field.grid_metadata["normalization"] == "consistent-mass normalization (phi^T M phi = 1)" for field in fields)
    convergence = [record for record in list_simulation_evidence(repo, simulation, owner) if record["record_type"] == "scientific_run_convergence"][0]
    assert convergence["payload"]["metric_type"] == "generalized_eigenpair_residual"
