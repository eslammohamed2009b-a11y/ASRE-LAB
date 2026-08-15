from __future__ import annotations

import hashlib
import io
import json

import numpy as np
import pytest
from fastapi import HTTPException

from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.cad_fem_execution import FEMExecutionError, execute_cad_fem
from app.module2_simulation.geometry_physics_schemas import PhysicsModelRequest
from app.module2_simulation.meshing import generate_mesh
from app.module2_simulation.physics_model import build_physics_model
from app.module2_simulation.evidence_lifecycle import list_simulation_evidence
from app.module2_simulation.thermal_field_benchmark import persist_linear_prism_benchmark, persist_quadratic_prism_benchmark
from app.v2.refinement import create_refinement_evidence
from app.v2.scientific_router import BenchmarkRequest, RefinementRequest, execute_benchmark, execute_refinement
from app.v2.trust_v2 import derive_trust_record
from app.v2 import scientific_router
from app.module2_simulation import source_resolution
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
    assert [field.variable_name for field in repo.list_field_results(simulation)] == ["heat_flux", "temperature", "temperature_gradient"]
    assert {item["record_type"] for item in list_simulation_evidence(repo, simulation, owner)} == {
        "scientific_numerical_result", "scientific_field_result", "scientific_validity", "scientific_run_convergence"}
    assert repo.get_simulation_result(simulation).validation_metadata["mesh_hash"] == mesh.metadata.mesh_hash
    with pytest.raises(FEMExecutionError):
        execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment,
            design_id=None, mesh=mesh, model=model, solver_id="structural_linear_elasticity_3d_v1", idempotency_key="bad")
    changed = _model(mesh)
    changed = changed.model_copy(update={"boundary_conditions": [
        *changed.boundary_conditions[:-1], changed.boundary_conditions[-1].model_copy(update={"heat_flux_w_m2": 10})]})
    with pytest.raises(FEMExecutionError, match="scientific identity"):
        execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment,
            design_id=None, mesh=mesh, model=changed, solver_id="thermal_fem_3d_v1", idempotency_key="same-science")


def test_persisted_linear_thermal_field_benchmark_is_authoritative_and_idempotent(tmp_path):
    repo = LocalSQLiteRepository(tmp_path / "records.sqlite"); storage = LocalFileStorage(tmp_path / "private")
    owner = "fem-owner"; experiment = repo.create_experiment(owner, "FEM")
    mesh = generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec())
    simulation = execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment,
        design_id=None, mesh=mesh, model=_model(mesh), solver_id="thermal_fem_3d_v1", idempotency_key="field-benchmark")
    first = persist_linear_prism_benchmark(repository=repo, storage=storage, user_id=owner, simulation_id=simulation,
        mesh=mesh, cold_k=300, hot_k=400)
    second = persist_linear_prism_benchmark(repository=repo, storage=storage, user_id=owner, simulation_id=simulation,
        mesh=mesh, cold_k=300, hot_k=400)
    details = first["payload"]["benchmark_details"]
    assert first["id"] == second["id"] and first["status"] == "pass"
    assert details["node_count"] == len(mesh.nodes_m) and details["max_absolute_error_k"] < 1e-8
    assert details["normalized_l2_error"] < 1e-8 and details["mesh_hash"] == mesh.metadata.mesh_hash
    assert len(first["payload"]["source_ids"]) >= 2
    scientific=scientific_router.EvidenceRepository(repository=repo)
    unbound={**first["payload"],"case_binding":None,"warnings":["unbound-forgery"]}
    scientific.create_scientific_evidence(owner,unbound)
    assert derive_trust_record(owner,simulation,repository=repo)["payload"]["dimensions"]["benchmark"]["state"]=="PASS"
    tampered=json.loads(json.dumps(first["payload"])); tampered["case_binding"]["derived_parameters"]["length_m"]*=2
    tampered["warnings"]=["tampered-binding"]
    scientific.create_scientific_evidence(owner,tampered)
    assert derive_trust_record(owner,simulation,repository=repo)["payload"]["dimensions"]["benchmark"]["state"]=="PASS"


def test_latest_valid_benchmark_per_case_controls_trust_and_failure_cites_failure(tmp_path):
    repo=LocalSQLiteRepository(tmp_path/"records.sqlite"); storage=LocalFileStorage(tmp_path/"private")
    owner="fem-owner"; experiment=repo.create_experiment(owner,"canonical-benchmark")
    mesh=generate_mesh(compile_design(authoritative_box()),[domain()],mesh_spec())
    simulation=execute_cad_fem(repository=repo,storage=storage,user_id=owner,experiment_id=experiment,design_id=None,
        mesh=mesh,model=_model(mesh),solver_id="thermal_fem_3d_v1",idempotency_key="canonical")
    original=persist_linear_prism_benchmark(repository=repo,storage=storage,user_id=owner,simulation_id=simulation,mesh=mesh)
    scientific=scientific_router.EvidenceRepository(repository=repo)
    failed={**original["payload"],"status":"fail","passed":False,"computed_value":.5,"relative_error":.5,"warnings":["obsolete"]}
    scientific.create_scientific_evidence(owner,failed)
    current=scientific.create_scientific_evidence(owner,{**original["payload"],"warnings":["canonical-current"]})
    trust=derive_trust_record(owner,simulation,repository=repo)["payload"]
    assert trust["dimensions"]["benchmark"]=={"state":"PASS","evidence_ids":[current["id"]]}
    current_fail=scientific.create_scientific_evidence(owner,{**failed,"warnings":["canonical-failure"]})
    trust=derive_trust_record(owner,simulation,repository=repo)["payload"]
    assert trust["dimensions"]["benchmark"]=={"state":"FAIL","evidence_ids":[current_fail["id"]]}


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
    assert all(field.unit == "kg^-1/2" and field.grid_metadata["normalization"] == "phi^T M phi = 1"
               and field.grid_metadata["quantity"] == "mass_normalized_mode_shape" for field in fields)
    convergence = [record for record in list_simulation_evidence(repo, simulation, owner) if record["record_type"] == "scientific_run_convergence"][0]
    assert convergence["payload"]["metric_type"] == "generalized_eigenpair_residual"


def test_thermal_fem_persisted_benchmark_refinement_and_trust_chain(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "records.sqlite"); storage = LocalFileStorage(tmp_path / "private")
    monkeypatch.setattr(scientific_router, "get_repository", lambda: repo)
    monkeypatch.setattr(source_resolution, "get_repository", lambda: repo)
    owner = "fem-owner"; experiment = repo.create_experiment(owner, "FEM")
    simulations, meshes = [], []
    for size, key in ((20, "coarse"), (10, "medium"), (7.5, "fine")):
        mesh = generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec(size))
        meshes.append(mesh)
        simulations.append(execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment,
            design_id=None, mesh=mesh, model=_model(mesh), solver_id="thermal_fem_3d_v1", idempotency_key=key))
    monkeypatch.setattr(scientific_router, "get_storage", lambda: storage)
    monkeypatch.setattr(scientific_router, "_load_mesh", lambda mesh_id, user_id: meshes[-1])
    benchmark = execute_benchmark("thermal_fem_3d_v1", BenchmarkRequest(
        benchmark_case_id="thermal_fem_linear_prism",
        inputs={"temperature_at_min_k": 300, "temperature_at_max_k": 400},
        source_simulation_id=simulations[-1]), {"id": owner})
    assert benchmark["passed"] is True
    assert execute_benchmark("thermal_fem_3d_v1", BenchmarkRequest(
        benchmark_case_id="thermal_fem_linear_prism",
        inputs={"temperature_at_min_k": 300, "temperature_at_max_k": 400},
        source_simulation_id=simulations[-1]), {"id": owner})["evidence_id"] == benchmark["evidence_id"]
    with pytest.raises(HTTPException, match="contradicts persisted"):
        execute_benchmark("thermal_fem_3d_v1", BenchmarkRequest(
            benchmark_case_id="thermal_fem_linear_prism",
            inputs={"temperature_at_min_k": 299, "temperature_at_max_k": 401},
            source_simulation_id=simulations[-1]), {"id": owner})
    refinement = create_refinement_evidence(owner, simulations, "temperature_k",
        "geometry.fem_refinement.mesh.specification.target_size.value", repository=repo)
    assert refinement["payload"]["passed"] is True
    trust = derive_trust_record(owner, simulations[-1], repository=repo)
    assert trust["payload"]["overall_trust"] == "MODERATE"  # bounded solver validation remains a warning
    with pytest.raises(ValueError):
        create_refinement_evidence(owner, [simulations[0], simulations[0], simulations[2]], "temperature_k",
            "geometry.fem_refinement.mesh.specification.target_size.value", repository=repo)


def test_quadratic_field_benchmark_refinement_and_trust(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "records.sqlite"); storage = LocalFileStorage(tmp_path / "private")
    monkeypatch.setattr(source_resolution, "get_repository", lambda: repo)
    monkeypatch.setattr(scientific_router, "get_repository", lambda: repo)
    owner = "fem-owner"; experiment = repo.create_experiment(owner, "quadratic")
    simulations, benchmarks = [], []
    for size in (20, 10, 7.5):
        mesh = generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec(size))
        payload = {"analysis_family": "thermal", "domains": [domain().model_dump(mode="json")],
            "material_assignments": [{"domain_id": "solid_domain", "material_name": "steel"}],
            "boundary_conditions": [{"bc_type": "temperature", "bc_id": "left", "semantic_region": "low_end", "temperature_k": 300},
                {"bc_type": "temperature", "bc_id": "right", "semantic_region": "high_end", "temperature_k": 300},
                {"bc_type": "heat_flux", "bc_id": "walls", "semantic_region": "walls", "heat_flux_w_m2": 0},
                {"bc_type": "volumetric_heat_source", "bc_id": "source", "domain_id": "solid_domain", "heat_source_w_m3": 1_000_000}],
            "numerical_settings": {"settings_type": "steady_thermal"}, "expected_outputs": ["temperature", "heat_flux"]}
        simulation = execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment, design_id=None,
            mesh=mesh, model=build_physics_model(mesh, PhysicsModelRequest.model_validate(payload)), solver_id="thermal_fem_3d_v1", idempotency_key=f"quadratic-{size}")
        simulations.append(simulation)
        benchmarks.append(persist_quadratic_prism_benchmark(repository=repo, storage=storage, user_id=owner, simulation_id=simulation,
            mesh=mesh, temperature_k=300, source_w_m3=1_000_000, conductivity_w_m_k=45))
    with pytest.raises(ValueError, match="source_w_m3"):
        persist_quadratic_prism_benchmark(repository=repo,storage=storage,user_id=owner,simulation_id=simulations[-1],
            mesh=mesh,source_w_m3=999_999)
    with pytest.raises(ValueError, match="conductivity_w_m_k"):
        persist_quadratic_prism_benchmark(repository=repo,storage=storage,user_id=owner,simulation_id=simulations[-1],
            mesh=mesh,conductivity_w_m_k=44)
    errors = [item["payload"]["computed_value"] for item in benchmarks]
    assert errors[0] > errors[1] > errors[2] and errors[2] <= 5e-4
    refinement = create_refinement_evidence(owner, simulations, "normalized_l2_error",
        "geometry.fem_refinement.mesh.specification.target_size.value", threshold=5e-4,
        benchmark_id="thermal_fem_uniform_generation_prism", repository=repo)
    assert refinement["payload"]["passed"] is True
    routed=execute_refinement("thermal_fem_3d_v1",RefinementRequest(simulation_ids=simulations,
        selected_metric="normalized_l2_error",refinement_parameter="geometry.fem_refinement.mesh.specification.target_size.value",
        threshold=5e-4,metric_source="benchmark_evidence",benchmark_id="thermal_fem_uniform_generation_prism"),{"id":owner})
    assert routed["id"]==refinement["id"] and routed["payload"]["metric_source"]=="benchmark_evidence"
    trust = derive_trust_record(owner, simulations[-1], repository=repo)
    assert trust["payload"]["dimensions"]["benchmark"]["state"] == "PASS"
    assert refinement["id"] in trust["payload"]["dimensions"]["refinement"]["evidence_ids"]


def test_thermal_binding_rejects_wrong_mesh_nonbenchmark_bc_missing_field_checksum_and_owner(tmp_path):
    owner="fem-owner"; repo=LocalSQLiteRepository(tmp_path/"records.sqlite"); storage=LocalFileStorage(tmp_path/"private")
    experiment=repo.create_experiment(owner,"binding-attacks")
    mesh=generate_mesh(compile_design(authoritative_box()),[domain()],mesh_spec())
    simulation=execute_cad_fem(repository=repo,storage=storage,user_id=owner,experiment_id=experiment,design_id=None,
        mesh=mesh,model=_model(mesh),solver_id="thermal_fem_3d_v1",idempotency_key="bound")
    wrong_mesh=generate_mesh(compile_design(authoritative_box()),[domain()],mesh_spec(7.5))
    with pytest.raises(ValueError,match="mesh identity"):
        persist_linear_prism_benchmark(repository=repo,storage=storage,user_id=owner,simulation_id=simulation,mesh=wrong_mesh)
    with pytest.raises(LookupError):
        persist_linear_prism_benchmark(repository=repo,storage=storage,user_id="other-owner",simulation_id=simulation,mesh=mesh)

    nonbenchmark_model=_model(mesh)
    nonbenchmark_model=nonbenchmark_model.model_copy(update={"boundary_conditions":[
        *nonbenchmark_model.boundary_conditions[:-1],
        nonbenchmark_model.boundary_conditions[-1].model_copy(update={"heat_flux_w_m2":5.0})]})
    nonbenchmark=execute_cad_fem(repository=repo,storage=storage,user_id=owner,experiment_id=experiment,design_id=None,
        mesh=mesh,model=nonbenchmark_model,solver_id="thermal_fem_3d_v1",idempotency_key="nonbenchmark")
    with pytest.raises(ValueError,match="BC eligibility"):
        persist_linear_prism_benchmark(repository=repo,storage=storage,user_id=owner,simulation_id=nonbenchmark,mesh=mesh)

    field=next(x for x in repo.list_field_results(simulation) if x.variable_name=="temperature")
    with repo._connect() as connection:
        connection.execute("update simulation_field_results set checksum_sha256=? where id=?",("0"*64,field.id))
        connection.commit()
    with pytest.raises(ValueError,match="evidence"):
        persist_linear_prism_benchmark(repository=repo,storage=storage,user_id=owner,simulation_id=simulation,mesh=mesh)
    with repo._connect() as connection:
        connection.execute("delete from simulation_field_results where id=?",(field.id,)); connection.commit()
    with pytest.raises(ValueError,match="temperature field"):
        persist_linear_prism_benchmark(repository=repo,storage=storage,user_id=owner,simulation_id=simulation,mesh=mesh)


def test_correct_scalar_mean_with_corrupted_spatial_field_creates_fail_evidence(tmp_path):
    owner="fem-owner"; repo=LocalSQLiteRepository(tmp_path/"records.sqlite"); storage=LocalFileStorage(tmp_path/"private")
    experiment=repo.create_experiment(owner,"spatial-corruption"); mesh=generate_mesh(compile_design(authoritative_box()),[domain()],mesh_spec())
    simulation=execute_cad_fem(repository=repo,storage=storage,user_id=owner,experiment_id=experiment,design_id=None,
        mesh=mesh,model=_model(mesh),solver_id="thermal_fem_3d_v1",idempotency_key="corrupt-field")
    field=next(x for x in repo.list_field_results(simulation) if x.variable_name=="temperature")
    values=np.asarray([300+100*(point[0]-min(x[0] for x in mesh.nodes_m))/(max(x[0] for x in mesh.nodes_m)-min(x[0] for x in mesh.nodes_m)) for point in mesh.nodes_m])
    values[0]+=10; values[-1]-=10
    buffer=io.BytesIO(); np.savez_compressed(buffer,field=values); data=buffer.getvalue(); checksum=hashlib.sha256(data).hexdigest()
    temporary=tmp_path/"corrupt.npz"; temporary.write_bytes(data); storage.save_file(field.storage_object_key,temporary)
    evidence_repo=scientific_router.EvidenceRepository(repository=repo)
    field_evidence=next(x for x in evidence_repo.list_scientific_for_simulation(owner,simulation)
        if x["record_type"]=="scientific_field_result" and x["payload"]["variable_name"]=="temperature")
    payload={**field_evidence["payload"],"checksum_sha256":checksum}
    with repo._connect() as connection:
        connection.execute("update simulation_field_results set checksum_sha256=?,minimum=?,maximum=?,mean=? where id=?",
            (checksum,float(values.min()),float(values.max()),float(values.mean()),field.id))
        connection.execute("update engineering_evidence_records set payload=? where id=?",(json.dumps(payload),field_evidence["id"]))
        connection.commit()
    result=repo.get_simulation_result(simulation)
    assert result.summary_metrics["temperature_k"]==pytest.approx(350.0)
    benchmark=persist_linear_prism_benchmark(repository=repo,storage=storage,user_id=owner,simulation_id=simulation,mesh=mesh)
    assert benchmark["payload"]["passed"] is False and benchmark["payload"]["computed_value"]>0


def test_structural_and_modal_client_fitting_cannot_create_authoritative_benchmark(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "records.sqlite"); storage = LocalFileStorage(tmp_path / "private")
    monkeypatch.setattr(scientific_router, "get_repository", lambda: repo)
    monkeypatch.setattr(source_resolution, "get_repository", lambda: repo)
    owner = "fem-owner"; experiment = repo.create_experiment(owner, "FEM")
    mesh = generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec())
    structural = execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment, design_id=None,
        mesh=mesh, model=_model(mesh, "structural"), solver_id="structural_linear_elasticity_3d_v1", idempotency_key="structural-benchmark")
    structural_result = repo.get_simulation_result(structural)
    fitted_area = 100*.04/(200e9*structural_result.summary_metrics["displacement_m"])
    with pytest.raises(HTTPException, match="no server-bound"):
        execute_benchmark("structural_linear_elasticity_3d_v1", BenchmarkRequest(
            benchmark_case_id="structural_fem_axial_prism",
            inputs={"load_n":100,"length_m":.04,"youngs_modulus_pa":200e9,"area_m2":fitted_area},
            computed_result=structural_result.summary_metrics["displacement_m"],source_simulation_id=structural),{"id":owner})

    modal_request = PhysicsModelRequest.model_validate({"analysis_family": "modal", "domains": [domain().model_dump(mode="json")],
        "material_assignments": [{"domain_id": "solid_domain", "material_name": "steel"}],
        "boundary_conditions": [{"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"}],
        "numerical_settings": {"settings_type": "modal_eigen", "requested_modes": 3}, "expected_outputs": ["eigenfrequency", "mode_shape"]})
    modal = execute_cad_fem(repository=repo, storage=storage, user_id=owner, experiment_id=experiment, design_id=None,
        mesh=mesh, model=build_physics_model(mesh, modal_request), solver_id="modal_fem_3d_v1", idempotency_key="modal-benchmark")
    modal_result = repo.get_simulation_result(modal)
    fitted_inertia = ((modal_result.summary_metrics["frequency_hz"]*2*3.141592653589793/1.875104068711961**2)**2
        *7850*.0004*.04**4/200e9)
    with pytest.raises(HTTPException, match="no server-bound"):
        execute_benchmark("modal_fem_3d_v1", BenchmarkRequest(
            benchmark_case_id="modal_fem_cantilever",
            inputs={"youngs_modulus_pa":200e9,"inertia_m4":fitted_inertia,"density_kg_m3":7850,"area_m2":.0004,"length_m":.04},
            computed_result=modal_result.summary_metrics["frequency_hz"],source_simulation_id=modal),{"id":owner})


def test_zero_load_structural_result_omits_finite_factor_of_safety(tmp_path):
    repo=LocalSQLiteRepository(tmp_path/"records.sqlite"); storage=LocalFileStorage(tmp_path/"private")
    owner="fem-owner"; experiment=repo.create_experiment(owner,"zero-load")
    mesh=generate_mesh(compile_design(authoritative_box()),[domain()],mesh_spec())
    model=_model(mesh,"structural")
    model=model.model_copy(update={"boundary_conditions":[model.boundary_conditions[0],model.boundary_conditions[1].model_copy(update={"force_n":(0.0,0.0,0.0)})]})
    simulation=execute_cad_fem(repository=repo,storage=storage,user_id=owner,experiment_id=experiment,design_id=None,
        mesh=mesh,model=model,solver_id="structural_linear_elasticity_3d_v1",idempotency_key="zero-load")
    result=repo.get_simulation_result(simulation)
    assert "factor_of_safety" not in result.summary_metrics
    assert any("finite factor of safety is not applicable" in warning.lower() for warning in result.warnings)
