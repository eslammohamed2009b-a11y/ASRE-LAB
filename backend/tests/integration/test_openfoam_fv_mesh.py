from __future__ import annotations

import os
import re
import shutil
import subprocess
from subprocess import CompletedProcess

import pytest
from pydantic import ValidationError

from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.geometry_physics_schemas import PhysicsDomain
from app.module2_simulation.meshing import generate_mesh
from app.module2_simulation.openfoam_benchmark import (
    benchmark_design,
    benchmark_domain,
    benchmark_mesh_specification,
    benchmark_physics_request,
    evaluate_certified_fv_accuracy_gate,
)
from app.module2_simulation.openfoam_case import generate_laminar_fv_case, parse_cfd_fv_solution
from app.module2_simulation.openfoam_fv_mesh import (
    CFDGeneratedMeshV1,
    CFDMeshError,
    CFDMeshResolutionV1,
    SurfaceCertificationV1,
    _require_closed_manifold,
    _safe_patch,
    certify_final_cfd_mesh,
    generate_certified_cfd_surface,
    generate_snappyhex_case,
    parse_check_mesh,
    run_snappyhex_mesher,
)
from app.module2_simulation.physics_model import build_cfd_physics_model, build_physics_model
from app.module2_simulation.solver_registry import SOLVER_REGISTRY


pytestmark = pytest.mark.integration
CATEGORIES = {"low_end": "inlet", "high_end": "outlet", "walls": "wall"}


@pytest.fixture(scope="module")
def compiled():
    return compile_design(benchmark_design())


@pytest.fixture(scope="module")
def surface(compiled):
    return generate_certified_cfd_surface(compiled, benchmark_domain(), CATEGORIES)


def test_surface_is_deterministic_brep_derived_and_certified(compiled, surface):
    repeated = generate_certified_cfd_surface(compiled, benchmark_domain(), CATEGORIES)
    assert repeated.certification.source_surface_hash == surface.certification.source_surface_hash
    assert repeated.stl_ascii == surface.stl_ascii
    assert surface.certification.manifold and surface.certification.triangle_count == 12
    assert surface.certification.relative_volume_error <= 1e-3
    assert surface.certification.relative_area_error <= 5e-3
    assert surface.certification.maximum_cad_deviation_m <= 5e-5


def test_changed_cad_changes_surface_identity():
    payload = benchmark_design().model_dump(mode="json")
    payload["sketches"][0]["entities"][0]["width"] = {"value": 400, "unit": "mm"}
    changed = type(benchmark_design()).model_validate(payload)
    original_surface = generate_certified_cfd_surface(compile_design(benchmark_design()), benchmark_domain(), CATEGORIES)
    changed_surface = generate_certified_cfd_surface(compile_design(changed), benchmark_domain(), CATEGORIES)
    assert changed_surface.certification.source_surface_hash != original_surface.certification.source_surface_hash


def test_semantic_patch_mapping_is_exact_safe_and_injection_proof(surface):
    assert {item.category for item in surface.semantic_patches} == {"inlet", "outlet", "wall"}
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{1,63}", item.final_patch) for item in surface.semantic_patches)
    malicious = _safe_patch("wall; #codeStream { system(owned); }", "wall")
    assert re.fullmatch(r"[a-z][a-z0-9_]{1,63}", malicious) and "code" not in malicious and ";" not in malicious


def test_non_fluid_and_incomplete_semantic_scope_fail_closed(compiled):
    solid = PhysicsDomain(domain_id="solid", source_body_id="duct_fluid", domain_kind="solid")
    with pytest.raises(CFDMeshError) as exc: generate_certified_cfd_surface(compiled, solid, CATEGORIES)
    assert exc.value.code == "FLUID_DOMAIN_REQUIRED"
    with pytest.raises(CFDMeshError) as exc: generate_certified_cfd_surface(compiled, benchmark_domain(), {"low_end": "inlet", "walls": "wall"})
    assert exc.value.code == "SEMANTIC_SCOPE_MISMATCH"


def test_non_manifold_and_invalid_certification_are_rejected(surface):
    with pytest.raises(CFDMeshError) as exc: _require_closed_manifold({((0, 0, 0), (1, 0, 0)): 1})
    assert exc.value.code == "NON_MANIFOLD_SURFACE"
    payload = surface.certification.model_dump(mode="json"); payload["relative_volume_error"] = 0.01
    with pytest.raises(ValidationError): SurfaceCertificationV1.model_validate(payload)
    payload = surface.certification.model_dump(mode="json"); payload["maximum_cad_deviation_m"] = 1e-3
    with pytest.raises(ValidationError): SurfaceCertificationV1.model_validate(payload)


def test_inside_point_is_server_generated_and_proven_inside(compiled, surface):
    solid = compiled.bodies["duct_fluid"].val()
    import cadquery as cq
    assert solid.isInside(cq.Vector(*(value * 1e3 for value in surface.inside_point_m)), 1e-7)


def test_mesher_configuration_is_deterministic_bounded_and_contains_no_dynamic_code(surface, tmp_path):
    first = generate_snappyhex_case(tmp_path / "first", surface)
    second = generate_snappyhex_case(tmp_path / "second", surface)
    assert first.configuration_hash == second.configuration_hash
    assert first.background_counts == second.background_counts == (105, 25, 25)
    contents = "\n".join((tmp_path / "first" / item).read_text(encoding="utf-8") for item in first.generated_files)
    assert not any(token in contents for token in ("#code", "#include", "dynamicCode", "codedFixedValue", "libs"))
    assert "locationInMesh" in contents and "snappyHexMesh" in contents


def test_mesher_resource_cap_fails_before_execution(surface, tmp_path):
    resolution = CFDMeshResolutionV1(maximum_cells=1_000)
    with pytest.raises(CFDMeshError) as exc: generate_snappyhex_case(tmp_path, surface, resolution)
    assert exc.value.code == "MESH_RESOURCE_LIMIT"


def test_mesher_executes_only_fixed_utilities_without_shell(surface, tmp_path, monkeypatch):
    generate_snappyhex_case(tmp_path, surface)
    calls = []
    monkeypatch.setattr("app.module2_simulation.openfoam_fv_mesh.shutil.which", lambda executable: f"/fixed/{executable}")
    monkeypatch.setattr("app.module2_simulation.openfoam_fv_mesh.subprocess.run", lambda args, **kwargs: calls.append((args, kwargs)) or CompletedProcess(args, 0, "ok", ""))
    assert run_snappyhex_mesher(tmp_path) == ("ok", "ok", "ok")
    assert [item[0][0] for item in calls] == ["/fixed/blockMesh", "/fixed/snappyHexMesh", "/fixed/checkMesh"]
    assert all(item[1]["shell"] is False and item[1]["check"] is False for item in calls)


def test_checkmesh_parser_accepts_only_bounded_mesh_ok():
    log = """cells: 40000
faces: 124248
internal faces: 115600
Max aspect ratio = 8.5 OK.
Min volume = 8e-10. Max volume = 1e-8. Total volume = 0.0002. Cell volumes OK.
Mesh non-orthogonality Max: 49.6 average: 2.62
Max skewness = 0.293 OK.
Mesh OK.
"""
    metrics = parse_check_mesh(log)
    assert metrics.mesh_ok and metrics.boundary_face_count == 8648
    with pytest.raises(CFDMeshError): parse_check_mesh(log.replace("Mesh OK.", "Failed 1 mesh checks."))


def test_fem_tet4_remains_unchanged_and_pure_tet_cfd_is_not_claimed(compiled):
    mesh = generate_mesh(compiled, [benchmark_domain()], benchmark_mesh_specification("coarse"))
    assert mesh.metadata.element_types == ["tetra4", "triangle3"]
    entry = SOLVER_REGISTRY["cfd_openfoam_laminar_internal_3d_v1"]
    assert entry.accepted_element_types == ["hex8", "polyhedron"]
    assert any("Pure-TET CFD analytical validation failed" in item for item in entry.known_limitations)


def _typed_certified_mesh(compiled, surface, *, mesh_id="fv-a", mesh_hash="a" * 64):
    patches = [item.model_copy(update={"final_face_count": item.triangle_count}) for item in surface.semantic_patches]
    boundary_faces = sum(item.final_face_count for item in patches)
    return CFDGeneratedMeshV1.model_validate({
        "mesh_id": mesh_id, "mesh_hash": mesh_hash, "design_hash": compiled.design_hash,
        "geometry_fingerprint": compiled.geometry_fingerprint, "fluid_domain_id": benchmark_domain().domain_id,
        "source_body_id": benchmark_domain().source_body_id, "source_surface_hash": surface.certification.source_surface_hash,
        "resolution": CFDMeshResolutionV1().model_dump(mode="json"), "cell_count": 1,
        "face_count": boundary_faces, "internal_face_count": 0, "boundary_face_count": boundary_faces,
        "cell_types": ["hex8"], "semantic_patches": [item.model_dump(mode="json") for item in patches],
        "surface_certification": surface.certification.model_dump(mode="json"),
        "check_mesh": {"mesh_ok": True, "cell_count": 1, "face_count": boundary_faces,
            "internal_face_count": 0, "boundary_face_count": boundary_faces, "max_non_orthogonality": 0,
            "average_non_orthogonality": 0, "max_skewness": 0, "max_aspect_ratio": 1,
            "minimum_volume_m3": surface.certification.cad_volume_m3,
            "total_volume_m3": surface.certification.cad_volume_m3},
        "geometry_certification": {"cad_volume_m3": surface.certification.cad_volume_m3,
            "mesh_volume_m3": surface.certification.cad_volume_m3, "relative_volume_error": 0,
            "cell_center_count": 1, "cell_centers_inside": True, "maximum_boundary_deviation_m": 0,
            "boundary_conforming": True, "semantic_conforming": True, "connected_region_count": 1},
        "limitations": [],
    })


def test_cfd_physics_binds_actual_fv_mesh_without_tet_dependency(compiled, surface, monkeypatch):
    mesh = _typed_certified_mesh(compiled, surface)
    monkeypatch.setattr("app.module2_simulation.meshing.generate_mesh", lambda *args, **kwargs: pytest.fail("TET generation called"))
    model = build_cfd_physics_model(mesh, benchmark_physics_request())
    assert (model.mesh_id, model.mesh_hash) == (mesh.mesh_id, mesh.mesh_hash)
    assert "certified_finite_volume" in model.solver_requirements and "tetra4" not in model.solver_requirements
    assert sum(len(item.boundary_facet_ids) for item in model.semantic_mappings) == mesh.boundary_face_count
    assert build_cfd_physics_model(mesh, benchmark_physics_request()).physics_hash == model.physics_hash

    changed = mesh.model_copy(update={"mesh_id": "fv-b", "mesh_hash": "b" * 64})
    changed_model = build_cfd_physics_model(changed, benchmark_physics_request())
    assert changed_model.physics_hash != model.physics_hash
    from app.module2_simulation.openfoam_case import OpenFOAMCaseError, validate_fv_cfd_scope
    with pytest.raises(OpenFOAMCaseError) as exc:
        validate_fv_cfd_scope(changed, model)
    assert exc.value.code == "AUTHORITATIVE_MESH_REQUIRED"

    request_payload = benchmark_physics_request().model_dump(mode="json")
    changed_bc = type(benchmark_physics_request()).model_validate({
        **request_payload,
        "boundary_conditions": [{**request_payload["boundary_conditions"][0], "velocity_m_s": [0.2, 0, 0]},
                                *request_payload["boundary_conditions"][1:]],
    })
    changed_material = type(benchmark_physics_request()).model_validate({
        **request_payload, "material_assignments": [{"domain_id": benchmark_domain().domain_id, "material_name": "water"}],
    })
    changed_settings = type(benchmark_physics_request()).model_validate({
        **request_payload, "numerical_settings": {**request_payload["numerical_settings"], "tolerance": 1e-7},
    })
    assert len({model.physics_hash,
                build_cfd_physics_model(mesh, changed_bc).physics_hash,
                build_cfd_physics_model(mesh, changed_material).physics_hash,
                build_cfd_physics_model(mesh, changed_settings).physics_hash}) == 4


@pytest.mark.skipif(os.getenv("ASRE_RUN_OPENFOAM_FV_REAL") != "1" or shutil.which("foamRun") is None, reason="dedicated OpenFOAM 14 certified-FV gate")
def test_real_certified_fv_square_duct_accuracy(compiled, tmp_path):
    surface = generate_certified_cfd_surface(compiled, benchmark_domain(), CATEGORIES)
    generated = generate_snappyhex_case(tmp_path, surface)
    outputs = []
    for command in (("blockMesh", "-case", str(tmp_path)), ("snappyHexMesh", "-overwrite", "-case", str(tmp_path)), ("checkMesh", "-case", str(tmp_path))):
        completed = subprocess.run(command, shell=False, capture_output=True, text=True, timeout=1200, check=True)
        outputs.append(completed.stdout + completed.stderr)
    fv_mesh = certify_final_cfd_mesh(compiled, benchmark_domain(), generated, tmp_path, outputs[-1])
    model = build_cfd_physics_model(fv_mesh, benchmark_physics_request())
    definition = generate_laminar_fv_case(fv_mesh, model, tmp_path)
    completed = subprocess.run(("foamRun", "-solver", "incompressibleFluid", "-case", str(tmp_path)), shell=False, capture_output=True, text=True, timeout=1800, check=True)
    solution = parse_cfd_fv_solution(fv_mesh, model, definition, tmp_path, completed.stdout + completed.stderr)
    gate = evaluate_certified_fv_accuracy_gate(tmp_path, fv_mesh, definition, solution)
    assert gate.passed and gate.normalized_pressure_gradient_error <= 0.05
