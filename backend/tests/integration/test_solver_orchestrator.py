from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.cad_fem_solvers import (
    solve_modal_fem_3d,
    solve_structural_fem_3d,
    solve_thermal_fem_3d,
)
from app.module2_simulation.geometry_physics_schemas import PhysicsModelRequest
from app.module2_simulation.meshing import GeneratedMesh, generate_mesh
from app.module2_simulation.physics_model import build_physics_model
from app.module2_simulation.solver_orchestrator import (
    BackendAvailability,
    BackendCapability,
    ExecutionResourceLimits,
    OpenFOAMAdapterFoundation,
    OpenFOAMExecutionConfig,
    PreflightStatus,
    SolverOrchestrationError,
    create_execution_plan,
    dispatch,
)
from tests.integration.test_geometry_physics_foundation import authoritative_box, domain, mesh_spec


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def mesh():
    return generate_mesh(compile_design(authoritative_box()), [domain()], mesh_spec())


def model(mesh, family: str):
    payload = {
        "analysis_family": family,
        "domains": [domain().model_dump(mode="json")],
        "material_assignments": [{"domain_id": "solid_domain", "material_name": "steel"}],
        "boundary_conditions": {
            "thermal": [
                {"bc_type": "temperature", "bc_id": "left", "semantic_region": "low_end", "temperature_k": 300},
                {"bc_type": "temperature", "bc_id": "right", "semantic_region": "high_end", "temperature_k": 400},
                {"bc_type": "heat_flux", "bc_id": "walls", "semantic_region": "walls", "heat_flux_w_m2": 0},
            ],
            "structural": [
                {"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"},
                {"bc_type": "force", "bc_id": "load", "semantic_region": "high_end", "force_n": [100, 0, 0]},
            ],
            "modal": [{"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"}],
        }[family],
        "numerical_settings": {"thermal": {"settings_type": "steady_thermal"}, "structural": {"settings_type": "linear_static"}, "modal": {"settings_type": "modal_eigen", "requested_modes": 3}}[family],
        "expected_outputs": {"thermal": ["temperature"], "structural": ["displacement", "stress"], "modal": ["eigenfrequency", "mode_shape"]}[family],
    }
    return build_physics_model(mesh, PhysicsModelRequest.model_validate(payload))


@pytest.mark.parametrize(("solver_id", "family", "direct"), [
    ("thermal_fem_3d_v1", "thermal", solve_thermal_fem_3d),
    ("structural_linear_elasticity_3d_v1", "structural", solve_structural_fem_3d),
    ("modal_fem_3d_v1", "modal", solve_modal_fem_3d),
])
def test_fixed_fem_adapter_has_direct_solver_parity(mesh, solver_id, family, direct):
    physics = model(mesh, family)
    direct_result = direct(mesh, physics)
    plan = create_execution_plan(solver_id, mesh, physics)
    orchestrated = dispatch(plan, mesh, physics)
    assert plan.preflight_status == PreflightStatus.PASS
    assert orchestrated.solver_id == direct_result.solver_id == solver_id
    assert orchestrated.summary == direct_result.summary
    assert {key: value for key, value in orchestrated.diagnostics.items() if key != "solve_time_seconds"} == {
        key: value for key, value in direct_result.diagnostics.items() if key != "solve_time_seconds"
    }
    assert set(orchestrated.fields) == set(direct_result.fields)
    for key in direct_result.fields:
        assert (orchestrated.fields[key][1] == direct_result.fields[key][1]).all()


def test_unknown_solver_and_no_silent_fallback(mesh):
    with pytest.raises(SolverOrchestrationError, match="no fixed authoritative adapter") as exc:
        create_execution_plan("invented_solver", mesh, model(mesh, "thermal"))
    assert exc.value.code == "UNKNOWN_SOLVER"


def test_wrong_family_unsupported_bc_and_mesh_reject_before_dispatch(mesh):
    thermal = model(mesh, "thermal")
    assert create_execution_plan("structural_linear_elasticity_3d_v1", mesh, thermal).diagnostics == ("SOLVER_FAMILY_MISMATCH", "UNSUPPORTED_BOUNDARY_CONDITION")
    unsupported = thermal.model_copy(update={"boundary_conditions": [*thermal.boundary_conditions, model(mesh, "structural").boundary_conditions[0]]})
    assert "UNSUPPORTED_BOUNDARY_CONDITION" in create_execution_plan("thermal_fem_3d_v1", mesh, unsupported).diagnostics
    bad_mesh = replace(mesh, metadata=mesh.metadata.model_copy(update={"dimension": 2}))
    assert "UNSUPPORTED_MESH_DIMENSION" in create_execution_plan("thermal_fem_3d_v1", bad_mesh, thermal).diagnostics


def test_resource_bound_backend_unavailable_and_fingerprint_identity(mesh):
    thermal = model(mesh, "thermal")
    assert "RESOURCE_LIMIT" in create_execution_plan("thermal_fem_3d_v1", mesh, thermal, limits=ExecutionResourceLimits(maximum_nodes=1)).diagnostics
    unavailable = BackendCapability("python-scipy", BackendAvailability.UNAVAILABLE, None, "test", "test", "unavailable")
    failed = create_execution_plan("thermal_fem_3d_v1", mesh, thermal, backend=unavailable)
    assert failed.diagnostics == ("SOLVER_BACKEND_UNAVAILABLE",)
    with pytest.raises(SolverOrchestrationError) as exc:
        dispatch(failed, mesh, thermal)
    assert exc.value.code == "SOLVER_BACKEND_UNAVAILABLE"
    assert create_execution_plan("thermal_fem_3d_v1", mesh, thermal).request_fingerprint == create_execution_plan("thermal_fem_3d_v1", mesh, thermal).request_fingerprint
    changed = thermal.model_copy(update={"boundary_conditions": [*thermal.boundary_conditions[:-1], thermal.boundary_conditions[-1].model_copy(update={"heat_flux_w_m2": 1.0})]})
    assert create_execution_plan("thermal_fem_3d_v1", mesh, thermal).request_fingerprint != create_execution_plan("thermal_fem_3d_v1", mesh, changed).request_fingerprint


def test_dispatch_recomputes_complete_fingerprint_for_stale_plan(mesh, monkeypatch):
    original = model(mesh, "thermal")
    plan = create_execution_plan("thermal_fem_3d_v1", mesh, original)
    mutated = original.model_copy(update={"boundary_conditions": [
        *original.boundary_conditions[:-1], original.boundary_conditions[-1].model_copy(update={"heat_flux_w_m2": 7.0}),
    ]})
    assert mutated.physics_hash == original.physics_hash
    called = []
    module = __import__("app.module2_simulation.solver_orchestrator", fromlist=["FIXED_SOLVER_ADAPTERS"])
    adapter = module.FIXED_SOLVER_ADAPTERS["thermal_fem_3d_v1"]
    monkeypatch.setitem(module.FIXED_SOLVER_ADAPTERS, "thermal_fem_3d_v1", replace(adapter, callable=lambda *_: called.append(True)))
    with pytest.raises(SolverOrchestrationError) as exc:
        dispatch(plan, mesh, mutated)
    assert exc.value.code == "PLAN_INPUT_MISMATCH"
    assert not called


def test_backend_identity_mismatch_is_not_an_executable_plan(mesh):
    injected = BackendCapability("openfoam", BackendAvailability.AVAILABLE, "fake", "test", "test")
    plan = create_execution_plan("thermal_fem_3d_v1", mesh, model(mesh, "thermal"), backend=injected)
    assert plan.backend_id == "openfoam" and plan.backend_version == "fake"
    assert plan.preflight_status == PreflightStatus.FAIL
    assert plan.diagnostics == ("SOLVER_BACKEND_MISMATCH",)


def test_openfoam_foundation_uses_fixed_safe_command_and_isolation(monkeypatch, tmp_path):
    adapter = OpenFOAMAdapterFoundation(OpenFOAMExecutionConfig(timeout_seconds=7))
    calls = []
    monkeypatch.setattr("app.module2_simulation.solver_orchestrator.shutil.which", lambda executable: "/fixed/simpleFoam")
    monkeypatch.setattr("app.module2_simulation.solver_orchestrator.subprocess.run", lambda args, **kwargs: calls.append((args, kwargs)) or CompletedProcess(args, 17, "", "failure"))
    arbitrary = tmp_path / "case;not-a-command"; arbitrary.mkdir()
    with pytest.raises(SolverOrchestrationError) as exc:
        adapter.run_fixed_case(arbitrary)
    assert exc.value.code == "INVALID_EXTERNAL_WORKDIR"
    with adapter.case_workspace() as case:
        with pytest.raises(SolverOrchestrationError) as exc:
            adapter.run_fixed_case(case)
    assert exc.value.code == "SOLVER_EXTERNAL_FAILED"
    assert calls[0][0] == ["/fixed/simpleFoam", "-case", str(case)]
    assert calls[0][1]["shell"] is False and calls[0][1]["timeout"] == 7
    with adapter.case_workspace() as first, adapter.case_workspace() as second:
        assert Path(first).is_dir() and Path(second).is_dir() and first != second


def test_openfoam_missing_executable_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("app.module2_simulation.solver_orchestrator.shutil.which", lambda executable: None)
    adapter = OpenFOAMAdapterFoundation()
    with adapter.case_workspace() as workspace:
        with pytest.raises(SolverOrchestrationError) as exc:
            adapter.run_fixed_case(workspace)
    assert exc.value.code == "SOLVER_BACKEND_UNAVAILABLE"


def test_openfoam_timeout_is_typed(monkeypatch, tmp_path):
    monkeypatch.setattr("app.module2_simulation.solver_orchestrator.shutil.which", lambda executable: "/fixed/simpleFoam")
    monkeypatch.setattr("app.module2_simulation.solver_orchestrator.subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutExpired(args[0], kwargs["timeout"])))
    adapter = OpenFOAMAdapterFoundation()
    with adapter.case_workspace() as workspace:
        with pytest.raises(SolverOrchestrationError) as exc:
            adapter.run_fixed_case(workspace)
    assert exc.value.code == "SOLVER_TIMEOUT"
