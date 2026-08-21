from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.geometry_physics_schemas import (
    AnalysisFamilyV1,
    DomainKind,
    FlowInletBC,
    MaterialSnapshot,
    PhysicsModelRequest,
)
from app.module2_simulation.meshing import generate_mesh
from app.module2_simulation.openfoam_case import (
    MASS_IMBALANCE_LIMIT,
    OpenFOAMCaseError,
    generate_laminar_case,
    mass_flow_diagnostics,
    parse_cfd_solution,
    parse_residuals,
    parse_volume_field,
    prepare_laminar_case,
    validate_cfd_scope,
)
from app.module2_simulation.openfoam_mesh import export_poly_mesh
from app.module2_simulation.physics_model import build_physics_model
from app.module2_simulation.solver_orchestrator import (
    BackendAvailability,
    BackendCapability,
    SolverOrchestrationError,
    create_execution_plan,
    dispatch,
    solve_openfoam_cfd_3d,
)
from tests.integration.test_geometry_physics_foundation import authoritative_box, domain, mesh_spec

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def cfd_science():
    fluid = domain("fluid")
    mesh = generate_mesh(compile_design(authoritative_box()), [fluid], mesh_spec())
    request = PhysicsModelRequest.model_validate({
        "analysis_family": "cfd",
        "domains": [fluid.model_dump(mode="json")],
        "material_assignments": [{"domain_id": fluid.domain_id, "material_name": "air"}],
        "boundary_conditions": [
            {"bc_type": "velocity_inlet", "bc_id": "inlet", "semantic_region": "low_end", "velocity_m_s": [0.1, 0, 0]},
            {"bc_type": "pressure_boundary", "bc_id": "outlet", "semantic_region": "high_end", "pressure_pa": 0},
            {"bc_type": "wall", "bc_id": "walls", "semantic_region": "walls", "no_slip": True},
        ],
        "numerical_settings": {"settings_type": "steady_flow", "tolerance": 1e-6, "maximum_iterations": 2000},
        "expected_outputs": ["velocity", "pressure", "mass_flow"],
    })
    return mesh, build_physics_model(mesh, request)


def test_scope_accepts_fluid_and_rejects_solid_unsupported_and_missing_material(cfd_science):
    mesh, model = cfd_science
    inlet, outlet, wall, density, viscosity = validate_cfd_scope(mesh, model)
    assert inlet.bc_type == "velocity_inlet" and outlet.bc_type == "pressure_boundary" and wall.no_slip
    assert density == pytest.approx(1.204) and viscosity == pytest.approx(1.81e-5)
    solid = model.domains[0].model_copy(update={"domain_kind": DomainKind.SOLID, "explicit_fluid_volume": False})
    with pytest.raises(OpenFOAMCaseError) as exc:
        validate_cfd_scope(mesh, model.model_copy(update={"domains": [solid]}))
    assert exc.value.code == "FLUID_DOMAIN_REQUIRED"
    with pytest.raises(OpenFOAMCaseError) as exc:
        validate_cfd_scope(mesh, model.model_copy(update={"analysis_family": AnalysisFamilyV1.THERMAL}))
    assert exc.value.code == "UNSUPPORTED_CFD_FAMILY"
    unsupported = FlowInletBC(bc_id="flow", semantic_region="low_end", volumetric_flow_m3_s=1e-4)
    with pytest.raises(OpenFOAMCaseError) as exc:
        validate_cfd_scope(mesh, model.model_copy(update={"boundary_conditions": [unsupported, *model.boundary_conditions[1:]]}))
    assert exc.value.code == "UNSUPPORTED_CFD_BOUNDARY"
    props = model.materials[0].properties
    for missing in ("density", "dynamic_viscosity"):
        material = MaterialSnapshot(material_name="air", properties=[item for item in props if item.name != missing], snapshot_hash="test")
        with pytest.raises(OpenFOAMCaseError) as exc:
            validate_cfd_scope(mesh, model.model_copy(update={"materials": [material]}))
        assert exc.value.code == "MATERIAL_PROPERTY_MISSING"


def test_case_is_deterministic_sensitive_bounded_and_maps_patches(cfd_science, tmp_path):
    mesh, model = cfd_science
    poly_a = export_poly_mesh(mesh, tmp_path / "a", {"low_end": "inlet", "high_end": "outlet", "walls": "wall"})
    poly_b = export_poly_mesh(mesh, tmp_path / "b", {"low_end": "inlet", "high_end": "outlet", "walls": "wall"})
    first = generate_laminar_case(mesh, model, poly_a, tmp_path / "a")
    second = generate_laminar_case(mesh, model, poly_b, tmp_path / "b")
    assert first.case_fingerprint == second.case_fingerprint
    assert (first.inlet_patch, first.outlet_patch, first.wall_patch) == (
        "asre_inlet_b7501d2701a8", "asre_outlet_2d1779f725fa", "asre_wall_5fbffcac35e3")
    for relative in first.generated_files:
        assert (tmp_path / "a" / relative).read_bytes() == (tmp_path / "b" / relative).read_bytes()
    changed_bc = model.boundary_conditions[0].model_copy(update={"velocity_m_s": (0.2, 0.0, 0.0)})
    changed = model.model_copy(update={"boundary_conditions": [changed_bc, *model.boundary_conditions[1:]]})
    third = generate_laminar_case(mesh, changed, poly_a, tmp_path / "changed")
    assert third.case_fingerprint != first.case_fingerprint
    content = "".join((tmp_path / "a" / relative).read_text() for relative in first.generated_files)
    assert "simulationType laminar;" in content and "foamRun" not in content
    assert not any(token in content for token in ("#code", "dynamicCode", "codedFixedValue", "#include", "libs"))


def _field(path: Path, name: str, class_name: str, dimensions: str, values: str):
    path.write_text(
        f"FoamFile {{ format ascii; class {class_name}; object {name}; }}\n"
        f"dimensions {dimensions};\ninternalField {values};\n", encoding="utf-8")


def test_reviewed_field_parser_rejects_malformed_nonfinite_count_and_dimensions(tmp_path):
    u = tmp_path / "U"; p = tmp_path / "p"
    _field(u, "U", "volVectorField", "[0 1 -1 0 0 0 0]", "nonuniform List<vector> 2 ((1 0 0)(2 0 0))")
    _field(p, "p", "volScalarField", "[0 2 -2 0 0 0 0]", "uniform 2")
    assert parse_volume_field(u, "U", 2).location_type == "cell_centered"
    parsed_p = parse_volume_field(p, "p", 2)
    assert parsed_p.unit == "m2/s2 (kinematic pressure)" and parsed_p.values == [2.0, 2.0]
    _field(u, "U", "volScalarField", "[0 1 -1 0 0 0 0]", "uniform (1 0 0)")
    with pytest.raises(OpenFOAMCaseError, match="unexpected class"):
        parse_volume_field(u, "U", 2)
    _field(u, "U", "volVectorField", "[0 1 -1 0 0 0 0]", "nonuniform List<vector> 1 ((1 0 0))")
    with pytest.raises(OpenFOAMCaseError) as exc: parse_volume_field(u, "U", 2)
    assert exc.value.code == "FIELD_COUNT_MISMATCH"
    _field(u, "U", "volVectorField", "[0 1 -1 0 0 0 0]", "uniform (nan 0 0)")
    with pytest.raises(OpenFOAMCaseError): parse_volume_field(u, "U", 2)
    _field(p, "p", "volScalarField", "[1 -1 -2 0 0 0 0]", "uniform 0")
    with pytest.raises(OpenFOAMCaseError) as exc: parse_volume_field(p, "p", 2)
    assert exc.value.code == "UNEXPECTED_FIELD_DIMENSIONS"
    _field(p, "p", "volVectorField", "[0 2 -2 0 0 0 0]", "uniform 0")
    with pytest.raises(OpenFOAMCaseError) as exc: parse_volume_field(p, "p", 2)
    assert exc.value.code == "UNEXPECTED_FIELD"


def test_residual_and_mass_diagnostics_are_real_values_not_defaults():
    log = """Time = 1
smoothSolver:  Solving for Ux, Initial residual = 0.1, Final residual = 1e-8, No Iterations 2
smoothSolver:  Solving for Uy, Initial residual = 0.01, Final residual = 1e-9, No Iterations 1
GAMG:  Solving for p, Initial residual = 0.02, Final residual = 2e-9, No Iterations 3
SIMPLE solution converged in 17 iterations
"""
    assert parse_residuals(log) == (17, 0.1, 0.02, True)
    mass_in, mass_out, imbalance = mass_flow_diagnostics(1.0, 0.9995, 2.0)
    assert (mass_in, mass_out) == (2.0, 1.999) and imbalance == pytest.approx(5e-4)
    with pytest.raises(OpenFOAMCaseError): parse_residuals("End")


def test_cfd_orchestration_is_backend_bounded_and_stale_plan_fails(cfd_science, monkeypatch):
    mesh, model = cfd_science
    backend = BackendCapability("openfoam-foundation-14", BackendAvailability.AVAILABLE, "20260724",
        "test reviewed runtime", "dedicated CFD image")
    with pytest.raises(SolverOrchestrationError) as exc:
        create_execution_plan("cfd_openfoam_laminar_internal_3d_v1", mesh, model, backend=backend)
    assert exc.value.code == "UNKNOWN_SOLVER"
    from app.module2_simulation.solver_orchestrator import FIXED_CAD_CFD_ADAPTERS
    assert set(FIXED_CAD_CFD_ADAPTERS) == {"cfd_openfoam_laminar_internal_3d_v1"}


@pytest.mark.skipif(os.getenv("ASRE_RUN_OPENFOAM_REAL") != "1", reason="explicit real OpenFOAM CFD gate")
def test_real_openfoam_asre_channel_solve(cfd_science, tmp_path):
    mesh, model = cfd_science
    poly, definition = prepare_laminar_case(mesh, model, tmp_path)
    if shutil.which("foamRun"):
        # In the dedicated worker image, exercise the complete authorized-workspace adapter.
        solution = solve_openfoam_cfd_3d(mesh, model)
    else:
        # The host test controls the same fixed command in the reviewed image;
        # adapter command construction/isolation has separate focused coverage.
        command = ". /opt/openfoam14/etc/bashrc && foamRun -solver incompressibleFluid -case /case"
        completed = subprocess.run(["docker", "run", "--rm", "-v", f"{tmp_path.resolve()}:/case", "asre-openfoam14:20260724", "bash", "-lc", command], shell=False, capture_output=True, text=True, timeout=300)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        solution = parse_cfd_solution(mesh, model, poly, definition, tmp_path, completed.stdout + completed.stderr)
    assert solution.converged
    assert solution.diagnostics.normalized_mass_imbalance <= MASS_IMBALANCE_LIMIT
    assert solution.fields["U"].count == len(mesh.tetrahedra) and solution.fields["p"].count == len(mesh.tetrahedra)
    assert solution.flux.total_face_count == poly.internal_face_count + poly.boundary_face_count
    assert solution.pressure_interpretation.raw_unit == "m2/s2"
    assert solution.summary_metrics["physical_pressure_drop_pa"] == pytest.approx(
        definition.density_kg_m3 * solution.summary_metrics["pressure_drop_raw_m2_s2"])
    evidence = {"mesh_id": mesh.metadata.mesh_id, "mesh_hash": mesh.metadata.mesh_hash, "poly_mesh_hash": poly.poly_mesh_hash,
        "cells": poly.cell_count, "boundary_faces": poly.boundary_face_count, "density": definition.density_kg_m3,
        "dynamic_viscosity": definition.dynamic_viscosity_pa_s, "inlet_velocity": definition.inlet_velocity_m_s,
        "iterations": solution.iterations, "converged": solution.converged,
        "diagnostics": solution.diagnostics.model_dump(mode="json"), "summary_metrics": solution.summary_metrics,
        "U_count": solution.fields["U"].count, "p_count": solution.fields["p"].count,
        "p_dimensions": solution.fields["p"].dimensions, "phi_faces": solution.flux.total_face_count}
    print("ASRE_REAL_CFD=" + json.dumps(evidence, sort_keys=True))
