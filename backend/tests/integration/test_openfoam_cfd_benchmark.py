from __future__ import annotations

from dataclasses import replace
import json
import os
import shutil
import subprocess

import numpy as np
import pytest

from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.geometry_physics_schemas import DomainKind
from app.module2_simulation.meshing import GeneratedMesh, generate_mesh
from app.module2_simulation.openfoam_benchmark import (
    BENCHMARK_ID,
    DUCT_LENGTH_M,
    DUCT_HEIGHT_M,
    DUCT_WIDTH_M,
    FINE_ERROR_LIMIT,
    FV_MESH_RESOLUTIONS,
    FIT_R2_MINIMUM,
    MESH_TARGETS_M,
    PURE_TET_CFD_VALIDATION_STATUS,
    FIT_WINDOW,
    BenchmarkGeometry,
    CFDBenchmarkError,
    analytical_pressure_gradient,
    assemble_certified_fv_refinement,
    assemble_benchmark_result,
    benchmark_design,
    benchmark_domain,
    benchmark_mesh_specification,
    benchmark_physics_request,
    evaluate_benchmark_level,
    evaluate_certified_fv_benchmark_level,
    rectangular_duct_series,
    validate_benchmark_identity,
    validate_benchmark_identity_from_level,
    volume_weighted_pressure_fit,
)
from app.module2_simulation.openfoam_case import (
    CFDSolutionV1, generate_laminar_fv_case, parse_cfd_fv_solution,
    parse_cfd_solution, prepare_laminar_case,
)
from app.module2_simulation.openfoam_fv_mesh import (
    certify_final_cfd_mesh, generate_certified_cfd_surface, generate_snappyhex_case,
)
from app.module2_simulation.physics_model import build_cfd_physics_model, build_physics_model

pytestmark = pytest.mark.integration


def test_rectangular_duct_analytical_engine_independent_limits_and_scaling():
    square = rectangular_duct_series(0.02, 0.02)
    assert square.correction == pytest.approx(0.421731044865, abs=2e-12)
    assert square.converged and square.terms <= 100_000
    wide = rectangular_duct_series(20.0, 0.02)
    assert wide.correction == pytest.approx(1.0, rel=7e-4)
    base, _ = analytical_pressure_gradient(0.02, 0.02, 1.81e-5, 4e-5)
    doubled_mu, _ = analytical_pressure_gradient(0.02, 0.02, 3.62e-5, 4e-5)
    doubled_q, _ = analytical_pressure_gradient(0.02, 0.02, 1.81e-5, 8e-5)
    assert doubled_mu == pytest.approx(2 * base) and doubled_q == pytest.approx(2 * base)
    for args in ((0, .02, 1e-5, 1e-4), (.01, .02, 1e-5, 1e-4), (.02, .02, 0, 1e-4), (.02, .02, 1e-5, 0)):
        with pytest.raises(CFDBenchmarkError): analytical_pressure_gradient(*args)


def test_certified_fv_refinement_contract_is_server_owned_and_predeclared():
    assert {name: (item.transverse_cell_size_m, item.streamwise_cell_size_m)
            for name, item in FV_MESH_RESOLUTIONS.items()} == {
        "coarse": (0.002, 0.010), "medium": (0.0015, 0.0075), "fine": (0.001, 0.005),
    }
    assert FINE_ERROR_LIMIT == 0.05 and FIT_WINDOW == (0.75, 0.95)
    assert PURE_TET_CFD_VALIDATION_STATUS == "FAILED_NOT_PRODUCTION_AUTHORITY"


@pytest.fixture(scope="module")
def coarse_science():
    compiled = compile_design(benchmark_design()); domain = benchmark_domain()
    mesh = generate_mesh(compiled, [domain], benchmark_mesh_specification("coarse"))
    model = build_physics_model(mesh, benchmark_physics_request())
    return mesh, model


def _synthetic_solution(mesh, model, poly, definition):
    points = np.asarray(mesh.nodes_m); centroids = np.asarray([points[list(cell)].mean(axis=0) for cell in mesh.tetrahedra])
    p_pa = 0.03 - 0.12875504580705194 * centroids[:, 0]
    return CFDSolutionV1.model_validate({
        "mesh_id": mesh.metadata.mesh_id, "mesh_hash": mesh.metadata.mesh_hash, "poly_mesh_hash": poly.poly_mesh_hash,
        "case_fingerprint": definition.case_fingerprint, "converged": True, "iterations": 20,
        "summary_metrics": {},
        "fields": {
            "U": {"name": "U", "field_class": "volVectorField", "dimensions": [0,1,-1,0,0,0,0], "unit": "m/s", "count": len(mesh.tetrahedra), "values": [[.1,0,0]]*len(mesh.tetrahedra)},
            "p": {"name": "p", "field_class": "volScalarField", "dimensions": [0,2,-2,0,0,0,0], "unit": "m2/s2 (kinematic pressure)", "count": len(mesh.tetrahedra), "values": (p_pa/definition.density_kg_m3).tolist()},
        },
        "flux": {"dimensions": [0,3,-1,0,0,0,0], "internal_face_count": poly.internal_face_count, "boundary_face_count": poly.boundary_face_count, "total_face_count": poly.internal_face_count+poly.boundary_face_count},
        "material": {"density_kg_m3": definition.density_kg_m3, "dynamic_viscosity_pa_s": definition.dynamic_viscosity_pa_s, "density_source": definition.density_source, "dynamic_viscosity_source": definition.dynamic_viscosity_source},
        "pressure_interpretation": {"density_kg_m3": definition.density_kg_m3, "density_source": definition.density_source},
        "diagnostics": {"final_u_residual": 1e-8, "final_p_residual": 1e-8, "volumetric_flow_in_m3_s": 4e-5, "volumetric_flow_out_m3_s": 4e-5, "mass_flow_in_kg_s": 4.816e-5, "mass_flow_out_kg_s": 4.816e-5, "normalized_mass_imbalance": 0},
        "limitations": [],
    })


def test_server_owned_identity_pressure_fit_and_tamper_rejection(coarse_science, tmp_path):
    mesh, model = coarse_science; poly, definition = prepare_laminar_case(mesh, model, tmp_path / "valid")
    geometry = validate_benchmark_identity(mesh, model, definition)
    assert (geometry.length_m, geometry.width_m, geometry.height_m) == pytest.approx((.5,.02,.02))
    assert geometry.hydraulic_diameter_m == pytest.approx(.02)
    assert geometry.fit_start_from_inlet_m == pytest.approx(.375)
    assert geometry.fit_start_from_inlet_m / geometry.hydraulic_diameter_m == pytest.approx(18.75)
    assert geometry.fit_start_from_inlet_m >= 2 * geometry.entrance_length_screen_m
    solution = _synthetic_solution(mesh, model, poly, definition)
    level, _, _, reynolds = evaluate_benchmark_level("coarse", mesh, model, poly, definition, solution)
    assert level.pressure_fit.r_squared > 0.999999 and level.normalized_pressure_gradient_error < 1e-10 and reynolds < 2000

    solid = model.domains[0].model_copy(update={"domain_kind": DomainKind.SOLID, "explicit_fluid_volume": False})
    with pytest.raises(Exception): validate_benchmark_identity(mesh, model.model_copy(update={"domains": [solid]}))
    scaled = GeneratedMesh(metadata=mesh.metadata, nodes_m=tuple((x*.5,y,z) for x,y,z in mesh.nodes_m), tetrahedra=mesh.tetrahedra, boundary_facets=mesh.boundary_facets)
    with pytest.raises(CFDBenchmarkError) as exc: validate_benchmark_identity(scaled, model)
    assert exc.value.code == "BENCHMARK_GEOMETRY_MISMATCH"
    narrow = GeneratedMesh(metadata=mesh.metadata, nodes_m=tuple((x,y*.5,z) for x,y,z in mesh.nodes_m), tetrahedra=mesh.tetrahedra, boundary_facets=mesh.boundary_facets)
    with pytest.raises(CFDBenchmarkError) as exc: validate_benchmark_identity(narrow, model)
    assert exc.value.code == "BENCHMARK_GEOMETRY_MISMATCH"
    wrong_wall = model.boundary_conditions[2].model_copy(update={"semantic_region": "low_end"})
    with pytest.raises(CFDBenchmarkError): validate_benchmark_identity(mesh, model.model_copy(update={"boundary_conditions": [*model.boundary_conditions[:2], wrong_wall]}))
    changed_inlet = model.boundary_conditions[0].model_copy(update={"velocity_m_s": (.2,0,0)})
    with pytest.raises(CFDBenchmarkError): validate_benchmark_identity(mesh, model.model_copy(update={"boundary_conditions": [changed_inlet,*model.boundary_conditions[1:]]}))
    changed_property = model.materials[0].properties[0].model_copy(update={"value": 1.3})
    changed_material = model.materials[0].model_copy(update={"properties": [changed_property,*model.materials[0].properties[1:]]})
    with pytest.raises(CFDBenchmarkError): validate_benchmark_identity(mesh, model.model_copy(update={"materials": [changed_material]}))
    wrong_backend = solution.model_copy(update={"backend_id": "other"})
    with pytest.raises(CFDBenchmarkError) as exc: evaluate_benchmark_level("coarse", mesh, model, poly, definition, wrong_backend)
    assert exc.value.code == "BENCHMARK_SOLVER_MISMATCH"
    wrong_source = solution.model_copy(update={"mesh_id": "another-simulation-mesh"})
    with pytest.raises(CFDBenchmarkError) as exc: evaluate_benchmark_level("coarse", mesh, model, poly, definition, wrong_source)
    assert exc.value.code == "BENCHMARK_SOURCE_MISMATCH"
    wrong_mesh_metadata = mesh.metadata.model_copy(deep=True)
    wrong_mesh_metadata.specification.target_size.value = 6.0
    wrong_mesh = GeneratedMesh(metadata=wrong_mesh_metadata, nodes_m=mesh.nodes_m, tetrahedra=mesh.tetrahedra, boundary_facets=mesh.boundary_facets)
    with pytest.raises(CFDBenchmarkError) as exc: evaluate_benchmark_level("coarse", wrong_mesh, model, poly, definition, solution)
    assert exc.value.code == "BENCHMARK_MESH_SEQUENCE_MISMATCH"
    with pytest.raises(CFDBenchmarkError) as exc: validate_benchmark_identity_from_level(level, definition, 10_000.0)
    assert exc.value.code == "BENCHMARK_CONFIGURATION_INVALID"
    altered_fit = level.pressure_fit.model_dump(mode="json"); altered_fit["normalized_window"] = [0.60, 0.90]
    with pytest.raises(Exception): type(level.pressure_fit).model_validate(altered_fit)
    assert FIT_WINDOW == (0.75, 0.95)
    assert DUCT_LENGTH_M == 0.50
    with pytest.raises(TypeError): benchmark_design(length_m=0.20)
    with pytest.raises(TypeError): benchmark_physics_request(volumetric_flow_m3_s=1e-4)
    bad_mass = solution.model_copy(deep=True); bad_mass.diagnostics.normalized_mass_imbalance = 0.01
    with pytest.raises(CFDBenchmarkError): evaluate_benchmark_level("coarse", mesh, model, poly, definition, bad_mass)
    nonconverged = solution.model_copy(update={"converged": False})
    with pytest.raises(CFDBenchmarkError): evaluate_benchmark_level("coarse", mesh, model, poly, definition, nonconverged)


def test_pressure_fit_rejects_malformed_nonfinite_insufficient_and_nonlinear(coarse_science):
    mesh, model = coarse_science; geometry = validate_benchmark_identity(mesh, model)
    with pytest.raises(CFDBenchmarkError) as exc: volume_weighted_pressure_fit(mesh, [1.0], geometry)
    assert exc.value.code == "MALFORMED_PRESSURE_FIELD"
    nonfinite = np.ones(len(mesh.tetrahedra)); nonfinite[0] = np.nan
    with pytest.raises(CFDBenchmarkError): volume_weighted_pressure_fit(mesh, nonfinite, geometry)
    insufficient = replace(geometry, x_max_m=geometry.x_max_m + 100, length_m=geometry.length_m + 100)
    with pytest.raises(CFDBenchmarkError) as exc: volume_weighted_pressure_fit(mesh, np.arange(len(mesh.tetrahedra)), insufficient)
    assert exc.value.code == "INSUFFICIENT_PRESSURE_FIT"
    rng = np.random.default_rng(42)
    with pytest.raises(CFDBenchmarkError) as exc: volume_weighted_pressure_fit(mesh, rng.normal(size=len(mesh.tetrahedra)), geometry)
    assert exc.value.code == "NONLINEAR_PRESSURE_FIT"


@pytest.mark.skipif(os.getenv("ASRE_RUN_OPENFOAM_REFINEMENT") != "1" or shutil.which("docker") is None,
                    reason="explicit three-real-solve certified-FV CFD gate")
def test_real_openfoam_square_duct_poiseuille_refinement(tmp_path):
    compiled = compile_design(benchmark_design()); domain = benchmark_domain()
    surface = generate_certified_cfd_surface(compiled, domain, {"low_end": "inlet", "high_end": "outlet", "walls": "wall"})
    levels=[]; models=[]; definitions=[]
    for level in ("coarse", "medium", "fine"):
        case = tmp_path / level
        generated = generate_snappyhex_case(case, surface, FV_MESH_RESOLUTIONS[level])
        logs = []
        for command in (("blockMesh", "-case", "/case"), ("snappyHexMesh", "-overwrite", "-case", "/case"), ("checkMesh", "-case", "/case")):
            completed = subprocess.run(["docker", "run", "--rm", "-v", f"{case.resolve()}:/case",
                "asre-openfoam14:20260724", "bash", "-lc", ". /opt/openfoam14/etc/bashrc && " + " ".join(command)],
                shell=False, capture_output=True, text=True, timeout=1200)
            assert completed.returncode == 0, completed.stdout + completed.stderr
            logs.append(completed.stdout + completed.stderr)
        mesh = certify_final_cfd_mesh(compiled, domain, generated, case, logs[-1])
        model = build_cfd_physics_model(mesh, benchmark_physics_request())
        definition = generate_laminar_fv_case(mesh, model, case)
        completed = subprocess.run(["docker", "run", "--rm", "-v", f"{case.resolve()}:/case",
            "asre-openfoam14:20260724", "bash", "-lc",
            ". /opt/openfoam14/etc/bashrc && foamRun -solver incompressibleFluid -case /case"],
            shell=False, capture_output=True, text=True, timeout=1800)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        solution = parse_cfd_fv_solution(mesh, model, definition, case, completed.stdout + completed.stderr)
        result = evaluate_certified_fv_benchmark_level(level, case, mesh, model, definition, solution)
        levels.append(result); models.append(model); definitions.append(definition)
    benchmark = assemble_certified_fv_refinement(levels, models, definitions)
    summary = {"benchmark_id": BENCHMARK_ID, "G_analytical_pa_m": levels[0].analytical_gradient_pa_m,
        "fit_window": FIT_WINDOW, "fine_threshold": FINE_ERROR_LIMIT, "passed": benchmark.passed,
        "monotonic": benchmark.monotonic_error_reduction, "observed_order": benchmark.observed_order,
        "levels": {item.level: item.model_dump(mode="json") for item in levels}}
    print("ASRE_REAL_CERTIFIED_FV_REFINEMENT=" + json.dumps(summary, sort_keys=True))
    assert benchmark.passed and levels[2].normalized_pressure_gradient_error < levels[0].normalized_pressure_gradient_error
