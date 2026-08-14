from __future__ import annotations

import math
import time
from copy import deepcopy
from pathlib import Path

import cadquery as cq
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.main import app
from app.module1_design import router as design_router
from app.module1_design.cad_v2_compiler import (
    CADCompilationError,
    compile_design,
    design_hash,
    export_compiled_design,
)
from app.module1_design.cad_v2_schemas import (
    EngineeringDesignDocumentV2,
    GeometryValidationResult,
    ValidationStatus,
)
from app.module1_design.legacy_cad_adapter import adapt_legacy_design
from app.module1_design.schemas import DesignParameters, GeometryType

pytestmark = pytest.mark.integration


def q(value, unit="mm"):
    return {"value": value, "unit": unit}


def ref(name):
    return {"parameter": name}


def point(x, y, unit="mm"):
    return {"x": q(x, unit), "y": q(y, unit)}


def vector(x=0, y=0, z=0, unit="mm"):
    return {"x": q(x, unit), "y": q(y, unit), "z": q(z, unit)}


def plate_document(*, width=100, width_unit="mm", thickness=10, document_id="plate", metadata=None):
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": document_id,
        "parameters": [
            {"name": "width", "parameter_type": "length", "value": width, "unit": width_unit,
             "design_variable": True},
            {"name": "thickness", "parameter_type": "length", "value": thickness, "unit": "mm",
             "minimum": 0.001, "design_variable": True},
        ],
        "bodies": [{"body_id": "plate", "material": "steel"}],
        "sketches": [{
            "sketch_id": "plate_profile", "plane": "XY", "unit": "mm",
            "entities": [{"entity_type": "rectangle", "entity_id": "outline",
                          "width": ref("width"), "height": q(40)}],
        }],
        "features": [{"operation": "extrude", "feature_id": "extrude_plate",
                      "sketch_id": "plate_profile", "output_body": "plate",
                      "distance": ref("thickness"), "semantic_tags": ["plate_solid"]}],
        "output_body_ids": ["plate"],
        "semantic_regions": [{"tag": "mounting_face", "body_id": "plate",
                              "source_feature_id": "extrude_plate", "selector": "end_faces"}],
        "operational_metadata": metadata or {},
    })


def bracket_document(*, hole_radius=6, spacing=70):
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": "mounting_bracket",
        "parameters": [
            {"name": "plate_thickness", "parameter_type": "length", "value": 10, "unit": "mm",
             "design_variable": True},
            {"name": "hole_radius", "parameter_type": "length", "value": hole_radius, "unit": "mm",
             "design_variable": True},
            {"name": "hole_spacing", "parameter_type": "length", "value": spacing, "unit": "mm",
             "design_variable": True},
            {"name": "hole_count", "parameter_type": "integer", "value": 2},
        ],
        "bodies": [
            {"body_id": name, "material": "steel" if "hole" not in name else None}
            for name in ("base", "upright", "joined", "hole_seed", "hole_pattern", "drilled", "finished")
        ],
        "sketches": [
            {"sketch_id": "base_profile", "plane": "XY", "entities": [
                {"entity_type": "rectangle", "entity_id": "base_outline", "width": q(120), "height": q(60)}]},
            {"sketch_id": "upright_profile", "plane": "XZ", "entities": [
                {"entity_type": "rectangle", "entity_id": "upright_outline", "width": q(120), "height": q(80),
                 "center": point(0, 40)}]},
            {"sketch_id": "hole_profile", "plane": "XZ", "entities": [
                {"entity_type": "circle", "entity_id": "hole_circle", "radius": ref("hole_radius"),
                 "center": point(-35, 40)}]},
        ],
        "features": [
            {"operation": "extrude", "feature_id": "base_extrude", "sketch_id": "base_profile",
             "output_body": "base", "distance": ref("plate_thickness")},
            {"operation": "extrude", "feature_id": "upright_extrude", "sketch_id": "upright_profile",
             "output_body": "upright", "distance": ref("plate_thickness"), "symmetric": True},
            {"operation": "union", "feature_id": "join_plates", "target_body": "base", "tool_body": "upright",
             "output_body": "joined", "semantic_tags": ["structural_body"]},
            {"operation": "extrude", "feature_id": "hole_extrude", "sketch_id": "hole_profile",
             "output_body": "hole_seed", "distance": q(80), "symmetric": True},
            {"operation": "linear_pattern", "feature_id": "pattern_holes", "source_body": "hole_seed",
             "output_body": "hole_pattern", "direction": [1, 0, 0], "spacing": ref("hole_spacing"),
             "count": ref("hole_count")},
            {"operation": "subtract", "feature_id": "cut_holes", "target_body": "joined",
             "tool_body": "hole_pattern", "output_body": "drilled"},
            {"operation": "chamfer", "feature_id": "finish_edges", "source_body": "drilled",
             "output_body": "finished", "distance": q(1), "edge_selector": "parallel_y"},
        ],
        "output_body_ids": ["finished"],
        "semantic_regions": [
            {"tag": "fixed_support_region", "body_id": "finished", "source_feature_id": "finish_edges",
             "selector": "end_faces"},
            {"tag": "mounting_holes", "body_id": "finished", "source_feature_id": "cut_holes",
             "selector": "side_faces"},
        ],
    })


def flange_document(*, thickness=12, bore_radius=10):
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": "rotational_flange",
        "parameters": [
            {"name": "thickness", "parameter_type": "length", "value": thickness, "unit": "mm",
             "design_variable": True},
            {"name": "bore_radius", "parameter_type": "length", "value": bore_radius, "unit": "mm",
             "design_variable": True},
            {"name": "bolt_count", "parameter_type": "integer", "value": 6},
        ],
        "bodies": [{"body_id": name, "material": "steel"} for name in
                   ("revolved", "bore", "hollow", "bolt_seed", "bolt_pattern", "flange")],
        "sketches": [
            {"sketch_id": "radial_profile", "plane": "XZ", "entities": [
                {"entity_type": "polyline", "entity_id": "profile", "points": [
                    point(0, 0), point(40, 0),
                    {"x": q(40), "y": ref("thickness")},
                    {"x": q(0), "y": ref("thickness")},
                ], "closed": True}]},
            {"sketch_id": "bore_profile", "plane": "XY", "entities": [
                {"entity_type": "circle", "entity_id": "bore_circle", "radius": ref("bore_radius")}]},
            {"sketch_id": "bolt_profile", "plane": "XY", "entities": [
                {"entity_type": "circle", "entity_id": "bolt_circle", "radius": q(3), "center": point(28, 0)}]},
        ],
        "features": [
            {"operation": "revolve", "feature_id": "revolve_profile", "sketch_id": "radial_profile",
             "output_body": "revolved", "axis_start": point(0, 0), "axis_end": point(0, 1),
             "angle": q(360, "deg")},
            {"operation": "extrude", "feature_id": "make_bore", "sketch_id": "bore_profile",
             "output_body": "bore", "distance": ref("thickness")},
            {"operation": "subtract", "feature_id": "cut_bore", "target_body": "revolved", "tool_body": "bore",
             "output_body": "hollow"},
            {"operation": "extrude", "feature_id": "make_bolt_hole", "sketch_id": "bolt_profile",
             "output_body": "bolt_seed", "distance": ref("thickness")},
            {"operation": "circular_pattern", "feature_id": "pattern_bolts", "source_body": "bolt_seed",
             "output_body": "bolt_pattern", "count": ref("bolt_count"), "total_angle": q(360, "deg")},
            {"operation": "subtract", "feature_id": "cut_bolts", "target_body": "hollow",
             "tool_body": "bolt_pattern", "output_body": "flange"},
        ],
        "output_body_ids": ["flange"],
        "semantic_regions": [
            {"tag": "bore_surface", "body_id": "flange", "source_feature_id": "cut_bore", "selector": "side_faces"}
        ],
    })


def test_explicit_metre_and_millimetre_inputs_compile_to_equivalent_geometry_and_identity():
    metres = plate_document(width=0.1, width_unit="m", document_id="request_a",
                            metadata={"request_id": "one", "timestamp": "today", "storage_path": "ignored/a"})
    millimetres = plate_document(width=100, width_unit="mm", document_id="request_b",
                                 metadata={"request_id": "two", "timestamp": "tomorrow", "storage_path": "ignored/b"})
    first, second = compile_design(metres), compile_design(millimetres)
    assert first.design_hash == second.design_hash
    assert first.geometry_fingerprint == second.geometry_fingerprint
    assert first.bodies["plate"].val().BoundingBox().xlen == pytest.approx(100)
    assert second.bodies["plate"].val().BoundingBox().xlen == pytest.approx(100)
    assert first.normalized_parameters["width"] == second.normalized_parameters["width"]


def test_parameter_and_feature_changes_change_scientific_design_identity():
    original = plate_document()
    thicker = plate_document(thickness=12)
    transformed_payload = original.model_dump(mode="json")
    transformed_payload["bodies"].append({"body_id": "moved"})
    transformed_payload["features"].append({
        "operation": "transform", "feature_id": "move_plate", "source_body": "plate",
        "output_body": "moved", "translation": vector(x=5),
    })
    transformed_payload["output_body_ids"] = ["moved"]
    transformed = EngineeringDesignDocumentV2.model_validate(transformed_payload)
    assert design_hash(original) != design_hash(thicker)
    assert design_hash(original) != design_hash(transformed)
    assert design_hash(original) == design_hash(plate_document())


def test_rectangle_extrude_validation_and_step_stl_exports_are_real(tmp_path: Path):
    compiled = compile_design(plate_document())
    assert compiled.validation.status == ValidationStatus.VALID
    assert compiled.validation.body_count == 1
    assert compiled.validation.solid_count == 1
    assert compiled.bodies["plate"].val().Volume() == pytest.approx(100 * 40 * 10, rel=1e-6)
    artifacts = export_compiled_design(compiled, tmp_path)
    assert {item.metadata.file_format for item in artifacts} == {"step", "stl"}
    assert all(item.path.stat().st_size > 0 and len(item.metadata.checksum_sha256) == 64 for item in artifacts)
    step = next(item for item in artifacts if item.metadata.file_format == "step")
    assert step.metadata.coordinate_unit == "mm"
    assert cq.importers.importStep(str(step.path)).val().BoundingBox().xlen == pytest.approx(100)


def test_generic_bracket_reference_model_compiles_validates_exports_and_rebuilds(tmp_path: Path):
    started = time.monotonic()
    first = compile_design(bracket_document())
    elapsed = time.monotonic() - started
    modified = compile_design(bracket_document(hole_radius=8))
    rebuilt = compile_design(bracket_document())
    assert first.validation.status == ValidationStatus.VALID
    assert first.bodies["finished"].solids().size() == 1
    assert modified.bodies["finished"].val().Volume() < first.bodies["finished"].val().Volume()
    assert rebuilt.design_hash == first.design_hash
    assert rebuilt.geometry_fingerprint == first.geometry_fingerprint
    assert first.semantic_regions[0]["tag"] == "fixed_support_region"
    assert len(export_compiled_design(first, tmp_path)) == 2
    assert elapsed < 60  # regression guard, not a throughput claim


def test_generic_flange_reference_model_uses_revolve_and_circular_pattern(tmp_path: Path):
    first = compile_design(flange_document())
    thicker = compile_design(flange_document(thickness=16))
    wider_bore = compile_design(flange_document(bore_radius=13))
    assert first.validation.status == ValidationStatus.VALID
    assert first.bodies["flange"].solids().size() == 1
    assert thicker.bodies["flange"].val().Volume() > first.bodies["flange"].val().Volume()
    assert wider_bore.bodies["flange"].val().Volume() < first.bodies["flange"].val().Volume()
    assert len(export_compiled_design(first, tmp_path)) == 2


def test_union_intersection_transform_fillet_and_linear_pattern_geometry():
    payload = plate_document().model_dump(mode="json")
    payload["bodies"] = [{"body_id": name} for name in
                         ("a", "b", "b_moved", "unioned", "intersected", "filleted", "patterned")]
    payload["sketches"] = [
        {"sketch_id": "a_profile", "entities": [
            {"entity_type": "rectangle", "entity_id": "a_rect", "width": q(20), "height": q(20)}]},
        {"sketch_id": "b_profile", "entities": [
            {"entity_type": "rectangle", "entity_id": "b_rect", "width": q(20), "height": q(20)}]},
    ]
    payload["features"] = [
        {"operation": "extrude", "feature_id": "make_a", "sketch_id": "a_profile", "output_body": "a", "distance": q(10)},
        {"operation": "extrude", "feature_id": "make_b", "sketch_id": "b_profile", "output_body": "b", "distance": q(10)},
        {"operation": "transform", "feature_id": "move_b", "source_body": "b", "output_body": "b_moved",
         "translation": vector(x=10)},
        {"operation": "union", "feature_id": "join", "target_body": "a", "tool_body": "b_moved", "output_body": "unioned"},
        {"operation": "intersection", "feature_id": "overlap", "target_body": "a", "tool_body": "b_moved", "output_body": "intersected"},
        {"operation": "fillet", "feature_id": "round", "source_body": "unioned", "output_body": "filleted",
         "radius": q(1), "edge_selector": "parallel_z"},
        {"operation": "linear_pattern", "feature_id": "repeat", "source_body": "filleted", "output_body": "patterned",
         "direction": [0, 1, 0], "spacing": q(30), "count": 2},
    ]
    payload["output_body_ids"] = ["patterned", "intersected"]
    payload["semantic_regions"] = []
    compiled = compile_design(EngineeringDesignDocumentV2.model_validate(payload))
    assert compiled.validation.body_count == 2
    assert compiled.validation.solid_count == 3
    assert compiled.bodies["unioned"].val().Volume() == pytest.approx(6000, rel=1e-5)
    assert compiled.bodies["intersected"].val().Volume() == pytest.approx(2000, rel=1e-5)
    assert compiled.bodies["patterned"].solids().size() == 2


def test_line_and_arc_sketch_entities_construct_closed_engineering_profiles():
    payload = {
        "document_id": "line_arc_profiles",
        "bodies": [{"body_id": "line_body"}, {"body_id": "arc_body"}],
        "sketches": [
            {"sketch_id": "line_profile", "entities": [
                {"entity_type": "line", "entity_id": "l1", "start": point(0, 0), "end": point(20, 0)},
                {"entity_type": "line", "entity_id": "l2", "start": point(20, 0), "end": point(20, 10)},
                {"entity_type": "line", "entity_id": "l3", "start": point(20, 10), "end": point(0, 10)},
                {"entity_type": "line", "entity_id": "l4", "start": point(0, 10), "end": point(0, 0)},
            ]},
            {"sketch_id": "arc_profile", "entities": [
                {"entity_type": "arc", "entity_id": "semicircle", "start": point(-10, 0),
                 "midpoint": point(0, 10), "end": point(10, 0)},
                {"entity_type": "line", "entity_id": "diameter", "start": point(10, 0), "end": point(-10, 0)},
            ]},
        ],
        "features": [
            {"operation": "extrude", "feature_id": "extrude_lines", "sketch_id": "line_profile",
             "output_body": "line_body", "distance": q(5)},
            {"operation": "extrude", "feature_id": "extrude_arc", "sketch_id": "arc_profile",
             "output_body": "arc_body", "distance": q(5)},
        ],
        "output_body_ids": ["line_body", "arc_body"],
    }
    compiled = compile_design(EngineeringDesignDocumentV2.model_validate(payload))
    assert compiled.validation.status == ValidationStatus.VALID
    assert compiled.validation.body_count == 2
    assert compiled.bodies["line_body"].val().Volume() == pytest.approx(1000, rel=1e-6)
    assert compiled.bodies["arc_body"].val().Volume() == pytest.approx(math.pi * 10 * 10 * 0.5 * 5, rel=1e-5)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda payload: payload["features"][0].update(distance=q(0)), "INVALID_DIMENSION"),
        (lambda payload: payload["features"][0].update(distance=q(-1)), "INVALID_DIMENSION"),
    ],
)
def test_invalid_dimensions_fail_without_artifacts(mutate, code):
    payload = plate_document().model_dump(mode="json")
    mutate(payload)
    with pytest.raises(CADCompilationError) as exc:
        compile_design(EngineeringDesignDocumentV2.model_validate(payload))
    assert exc.value.code == code


def test_empty_boolean_and_invalid_fillet_fail_closed():
    payload = plate_document().model_dump(mode="json")
    payload["bodies"].extend([{"body_id": "far"}, {"body_id": "empty"}])
    payload["features"].extend([
        {"operation": "transform", "feature_id": "move_far", "source_body": "plate", "output_body": "far",
         "translation": vector(x=1000)},
        {"operation": "intersection", "feature_id": "empty_intersection", "target_body": "plate", "tool_body": "far",
         "output_body": "empty"},
    ])
    payload["output_body_ids"] = ["empty"]
    with pytest.raises(CADCompilationError) as empty:
        compile_design(EngineeringDesignDocumentV2.model_validate(payload))
    assert empty.value.code in {"EMPTY_FEATURE_RESULT", "CAD_KERNEL_FAILURE"}

    payload = plate_document().model_dump(mode="json")
    payload["bodies"].append({"body_id": "impossible_fillet"})
    payload["features"].append({"operation": "fillet", "feature_id": "bad_fillet", "source_body": "plate",
                                "output_body": "impossible_fillet", "radius": q(1000)})
    payload["output_body_ids"] = ["impossible_fillet"]
    with pytest.raises(CADCompilationError):
        compile_design(EngineeringDesignDocumentV2.model_validate(payload))


def test_parameter_dimension_mismatch_fails_safely():
    payload = plate_document().model_dump(mode="json")
    payload["parameters"].append({"name": "copies", "parameter_type": "integer", "value": 2})
    payload["features"][0]["distance"] = ref("copies")
    with pytest.raises(CADCompilationError) as exc:
        compile_design(EngineeringDesignDocumentV2.model_validate(payload))
    assert exc.value.code == "PARAMETER_DIMENSION_MISMATCH"


def test_invalid_geometry_is_never_exported(tmp_path: Path):
    compiled = compile_design(plate_document())
    compiled.validation = GeometryValidationResult(
        status=ValidationStatus.INVALID,
        diagnostics=[],
        body_count=1,
        solid_count=1,
    )
    with pytest.raises(CADCompilationError, match="Invalid geometry"):
        export_compiled_design(compiled, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_authenticated_v2_compile_persists_private_opaque_artifacts_with_owner_isolation(
    tmp_path: Path, monkeypatch,
):
    repository = LocalSQLiteRepository(tmp_path / "cad-v2.sqlite3")
    storage = LocalFileStorage(tmp_path / "objects")
    monkeypatch.setattr(design_router, "get_repository", lambda: repository)
    monkeypatch.setattr(design_router, "get_storage", lambda: storage)
    payload = plate_document().model_dump(mode="json")
    try:
        app.dependency_overrides[get_current_user] = lambda: {"id": "cad-owner", "role": "researcher"}
        owner = TestClient(app)
        response = owner.post("/api/design/v2/compile", json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        serialized = response.text.lower()
        assert "object_key" not in serialized and "users/" not in serialized and "storage_path" not in serialized
        artifact_ids = [item["artifact_id"] for item in body["metadata"]["artifacts"]]
        assert len(artifact_ids) == 2
        for artifact_id in artifact_ids:
            download = owner.get(f"/api/design/files/{artifact_id}/download")
            assert download.status_code == 200
            assert len(download.content) > 0

        app.dependency_overrides[get_current_user] = lambda: {"id": "other-user", "role": "researcher"}
        other = TestClient(app)
        for artifact_id in artifact_ids:
            assert other.get(f"/api/design/files/{artifact_id}/download").status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("params", "output_body", "expected_extents_mm"),
    [
        (DesignParameters(geometry_type=GeometryType.PYRAMID, base_length_m=10, height_m=5),
         "pyramid", (10_000, 10_000, 5_000)),
        (DesignParameters(geometry_type=GeometryType.TOWER, base_length_m=4, height_m=12, wall_thickness_m=0.25),
         "tower_shell", (4_000, 4_000, 12_000)),
        (DesignParameters(geometry_type=GeometryType.BRIDGE, base_length_m=20),
         "bridge_deck", (20_000, 10_000, 2_000)),
    ],
)
def test_legacy_supported_families_have_an_explicit_unit_safe_v2_adapter(
    params, output_body, expected_extents_mm,
):
    document = adapt_legacy_design(params)
    assert all(feature.operation not in {"pyramid", "tower", "bridge"} for feature in document.features)
    compiled = compile_design(document)
    box = compiled.bodies[output_body].val().BoundingBox()
    assert (box.xlen, box.ylen, box.zlen) == pytest.approx(expected_extents_mm, rel=1e-4, abs=2)
