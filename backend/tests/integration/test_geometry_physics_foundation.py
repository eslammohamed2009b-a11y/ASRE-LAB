from __future__ import annotations

import json
from pathlib import Path

import cadquery as cq
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.main import app
from app.module1_design.cad_v2_compiler import compile_design
from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2
from app.module2_simulation import geometry_physics_router as api
from app.module2_simulation.geometry_physics_schemas import (
    MeshSpecification,
    PhysicsDomain,
    PhysicsModelRequest,
)
from app.module2_simulation.meshing import MeshingError, generate_mesh, prepare_geometry, write_gmsh22
from app.module2_simulation.physics_model import PhysicsValidationError, build_physics_model


pytestmark = pytest.mark.integration


def q(value: float, unit: str = "mm") -> dict:
    return {"value": value, "unit": unit}


def authoritative_box(*, semantic_edges: bool = False) -> EngineeringDesignDocumentV2:
    semantics: list[dict] = [
        {"tag": "low_end", "body_id": "box", "selector": {
            "selector_type": "extreme_face", "axis": "x", "extreme": "minimum",
        }},
        {"tag": "high_end", "body_id": "box", "selector": {
            "selector_type": "extreme_face", "axis": "x", "extreme": "maximum",
        }},
        {"tag": "walls", "body_id": "box", "selector": "side_faces"},
    ]
    if semantic_edges:
        semantics.append({"tag": "edges", "body_id": "box", "selector": "all_edges"})
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": "phase3a_reference_box",
        "bodies": [{"body_id": "box", "material": "steel"}],
        "sketches": [{"sketch_id": "profile", "entities": [{
            "entity_type": "rectangle", "entity_id": "outline", "width": q(40), "height": q(20),
        }]}],
        "features": [{
            "operation": "extrude", "feature_id": "make_box", "sketch_id": "profile",
            "output_body": "box", "distance": q(20),
        }],
        "output_body_ids": ["box"],
        "semantic_regions": semantics,
    })


def domain(kind: str = "solid") -> PhysicsDomain:
    return PhysicsDomain(
        domain_id=f"{kind}_domain", source_body_id="box", domain_kind=kind,
        explicit_fluid_volume=kind == "fluid",
    )


def mesh_spec(size: float = 10) -> MeshSpecification:
    return MeshSpecification(target_size=q(size), refinement_level="custom")


@pytest.fixture(scope="module")
def compiled_box():
    return compile_design(authoritative_box())


@pytest.fixture(scope="module")
def solid_mesh(compiled_box):
    return generate_mesh(compiled_box, [domain()], mesh_spec())


def test_geometry_preparation_preserves_authoritative_identity_and_rejects_non_solid(compiled_box):
    prepared = prepare_geometry(compiled_box, [domain()])
    assert prepared.status == "READY"
    assert prepared.design_hash == compiled_box.design_hash
    assert prepared.geometry_fingerprint == compiled_box.geometry_fingerprint

    original = compiled_box.bodies["box"]
    try:
        compiled_box.bodies["box"] = cq.Workplane("XY").rect(10, 10)
        invalid = prepare_geometry(compiled_box, [domain()])
        assert invalid.status == "INVALID_FOR_MESHING"
        assert "not a closed solid" in " ".join(invalid.diagnostics)
    finally:
        compiled_box.bodies["box"] = original


def test_real_brep_mesh_is_si_tetrahedral_deterministic_and_quality_checked(compiled_box, solid_mesh, tmp_path):
    assert solid_mesh.metadata.design_hash == compiled_box.design_hash
    assert solid_mesh.metadata.geometry_fingerprint == compiled_box.geometry_fingerprint
    assert solid_mesh.metadata.coordinate_unit == "m"
    assert solid_mesh.metadata.element_types == ["tetra4", "triangle3"]
    assert solid_mesh.metadata.quality.node_count == len(solid_mesh.nodes_m) > 4
    assert solid_mesh.metadata.quality.tetrahedron_count == len(solid_mesh.tetrahedra) > 0
    assert solid_mesh.metadata.quality.boundary_facet_count == len(solid_mesh.boundary_facets) > 0
    assert solid_mesh.metadata.quality.minimum_element_volume_m3 > 0
    assert solid_mesh.metadata.quality.inverted_element_count == 0
    assert solid_mesh.metadata.quality.degenerate_element_count == 0
    # CAD width is 40 mm; the solver-facing coordinate extent must be 0.04 m.
    x_values = [point[0] for point in solid_mesh.nodes_m]
    assert max(x_values) - min(x_values) == pytest.approx(0.04, abs=1e-8)

    repeated = generate_mesh(compiled_box, [domain()], mesh_spec())
    assert repeated.metadata.mesh_hash == solid_mesh.metadata.mesh_hash
    assert repeated.nodes_m == solid_mesh.nodes_m
    refined = generate_mesh(compiled_box, [domain()], mesh_spec(7.5))
    assert refined.metadata.mesh_hash != solid_mesh.metadata.mesh_hash
    assert refined.metadata.design_hash == solid_mesh.metadata.design_hash

    before = solid_mesh.metadata.mesh_hash
    path = tmp_path / "box.msh"
    write_gmsh22(solid_mesh, path)
    assert path.read_text(encoding="utf-8").startswith("$MeshFormat\n2.2")
    assert "$PhysicalNames" in path.read_text(encoding="utf-8")
    assert solid_mesh.metadata.mesh_hash == before


def test_semantic_faces_map_to_nonempty_persistable_physical_groups(solid_mesh):
    mappings = {item.semantic_region: item for item in solid_mesh.metadata.semantic_mappings}
    assert set(mappings) == {"low_end", "high_end", "walls"}
    assert all(item.boundary_facet_ids for item in mappings.values())
    assert all(item.topology_signatures for item in mappings.values())
    assert mappings["low_end"].physical_group_id != mappings["high_end"].physical_group_id
    assert set(mappings["low_end"].boundary_facet_ids).isdisjoint(mappings["high_end"].boundary_facet_ids)


def test_edge_region_cannot_be_used_as_surface_sizing_target():
    compiled = compile_design(authoritative_box(semantic_edges=True))
    specification = MeshSpecification(
        target_size=q(10), semantic_sizing=[{"semantic_region": "edges", "target_size": q(5)}]
    )
    with pytest.raises(MeshingError, match="not a boundary face") as exc:
        generate_mesh(compiled, [domain()], specification)
    assert exc.value.code == "SEMANTIC_DIMENSION_MISMATCH"


def thermal_request() -> PhysicsModelRequest:
    return PhysicsModelRequest.model_validate({
        "analysis_family": "thermal",
        "domains": [domain().model_dump(mode="json")],
        "material_assignments": [{"domain_id": "solid_domain", "material_name": "steel"}],
        "boundary_conditions": [
            {"bc_type": "temperature", "bc_id": "cold", "semantic_region": "low_end", "temperature_k": 293.15},
            {"bc_type": "heat_flux", "bc_id": "load", "semantic_region": "high_end", "heat_flux_w_m2": 1000},
        ],
        "numerical_settings": {"settings_type": "steady_thermal", "tolerance": 1e-8},
        "expected_outputs": ["temperature", "heat_flux"],
    })


def test_thermal_and_structural_models_are_solver_ready_and_hash_deterministically(solid_mesh):
    thermal = build_physics_model(solid_mesh, thermal_request())
    assert thermal.validation_status == "VALID"
    assert {item.properties[0].unit for item in thermal.materials} == {"W/(m*K)"}
    assert build_physics_model(solid_mesh, thermal_request()).physics_hash == thermal.physics_hash

    structural_request = PhysicsModelRequest.model_validate({
        "analysis_family": "structural",
        "domains": [domain().model_dump(mode="json")],
        "material_assignments": [{"domain_id": "solid_domain", "material_name": "steel"}],
        "boundary_conditions": [
            {"bc_type": "fixed_support", "bc_id": "fixed", "semantic_region": "low_end"},
            {"bc_type": "force", "bc_id": "load", "semantic_region": "high_end", "force_n": [100, 0, 0]},
        ],
        "numerical_settings": {"settings_type": "linear_static"},
        "expected_outputs": ["displacement", "stress"],
    })
    structural = build_physics_model(solid_mesh, structural_request)
    assert structural.validation_status == "VALID"
    names = {prop.name for prop in structural.materials[0].properties}
    assert names == {"elastic_modulus", "poisson_ratio", "density", "yield_strength"}
    assert structural.physics_hash != thermal.physics_hash


def test_explicit_fluid_domain_builds_cfd_foundation_and_solid_does_not(compiled_box):
    fluid = domain("fluid")
    fluid_mesh = generate_mesh(compiled_box, [fluid], mesh_spec())
    request = PhysicsModelRequest.model_validate({
        "analysis_family": "cfd",
        "domains": [fluid.model_dump(mode="json")],
        "material_assignments": [{"domain_id": "fluid_domain", "material_name": "air"}],
        "boundary_conditions": [
            {"bc_type": "velocity_inlet", "bc_id": "in", "semantic_region": "low_end", "velocity_m_s": [1, 0, 0]},
            {"bc_type": "pressure_boundary", "bc_id": "out", "semantic_region": "high_end", "pressure_pa": 0},
            {"bc_type": "wall", "bc_id": "wall", "semantic_region": "walls", "no_slip": True},
        ],
        "numerical_settings": {"settings_type": "steady_flow"},
        "expected_outputs": ["velocity", "pressure"],
    })
    model = build_physics_model(fluid_mesh, request)
    assert model.analysis_family == "cfd"
    assert {item.name for item in model.materials[0].properties} == {"density", "dynamic_viscosity"}
    with pytest.raises(ValueError, match="explicitly modeled fluid volume"):
        PhysicsDomain(domain_id="fluid", source_body_id="box", domain_kind="fluid")


def test_physics_validation_fails_missing_material_bad_bc_and_bad_semantic_target(solid_mesh):
    payload = thermal_request().model_dump(mode="json")
    payload["material_assignments"] = []
    with pytest.raises(ValueError):
        PhysicsModelRequest.model_validate(payload)

    payload = thermal_request().model_dump(mode="json")
    payload["boundary_conditions"][1] = {
        "bc_type": "force", "bc_id": "wrong", "semantic_region": "high_end", "force_n": [1, 0, 0]
    }
    with pytest.raises(PhysicsValidationError) as incompatible:
        build_physics_model(solid_mesh, PhysicsModelRequest.model_validate(payload))
    assert incompatible.value.code == "INCOMPATIBLE_BOUNDARY_CONDITION"

    payload = thermal_request().model_dump(mode="json")
    payload["boundary_conditions"][0]["semantic_region"] = "does_not_exist"
    with pytest.raises(PhysicsValidationError) as missing:
        build_physics_model(solid_mesh, PhysicsModelRequest.model_validate(payload))
    assert missing.value.code == "INVALID_SEMANTIC_TARGET"


def test_authenticated_api_persists_opaque_owner_scoped_mesh_and_physics(tmp_path, monkeypatch):
    repo = LocalSQLiteRepository(tmp_path / "records.db")
    storage = LocalFileStorage(tmp_path / "private")
    monkeypatch.setattr(api, "get_repository", lambda: repo)
    monkeypatch.setattr(api, "get_storage", lambda: storage)
    owner_a = "phase3-owner-a"
    owner_b = "phase3-owner-b"
    experiment_id = repo.create_experiment(owner_a, "Phase 3A")
    payload = {
        "experiment_id": experiment_id,
        "document": authoritative_box().model_dump(mode="json"),
        "domains": [domain().model_dump(mode="json")],
        "specification": mesh_spec().model_dump(mode="json"),
    }
    app.dependency_overrides[get_current_user] = lambda: {"id": owner_a}
    try:
        client = TestClient(app)
        created = client.post("/api/geometry-physics/meshes", json=payload)
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["artifact_id"]
        serialized = json.dumps(body).lower()
        assert "object_key" not in serialized and "storage" not in serialized and "temp" not in serialized
        assert client.get(f"/api/geometry-physics/meshes/{body['mesh_id']}").status_code == 200
        assert client.get(f"/api/geometry-physics/artifacts/{body['artifact_id']}/download").status_code == 200

        physics_payload = {
            **payload,
            "physics": thermal_request().model_dump(mode="json"),
        }
        physics = client.post("/api/geometry-physics/physics", json=physics_payload)
        assert physics.status_code == 200, physics.text
        physics_body = physics.json()
        assert client.get(f"/api/geometry-physics/physics/{physics_body['physics_model_id']}").status_code == 200
        executed = client.post(
            f"/api/geometry-physics/physics/{physics_body['physics_model_id']}/execute",
            json={"solver_id": "thermal_fem_3d_v1"}, headers={"Idempotency-Key": "phase3b-thermal"},
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["status"] == "completed"

        app.dependency_overrides[get_current_user] = lambda: {"id": owner_b}
        assert client.get(f"/api/geometry-physics/meshes/{body['mesh_id']}").status_code == 404
        assert client.get(f"/api/geometry-physics/artifacts/{body['artifact_id']}/download").status_code == 404
        assert client.get(f"/api/geometry-physics/physics/{physics_body['physics_model_id']}").status_code == 404
        assert client.post(
            f"/api/geometry-physics/physics/{physics_body['physics_model_id']}/execute",
            json={"solver_id": "thermal_fem_3d_v1"}, headers={"Idempotency-Key": "phase3b-other"},
        ).status_code == 404
    finally:
        app.dependency_overrides.clear()
