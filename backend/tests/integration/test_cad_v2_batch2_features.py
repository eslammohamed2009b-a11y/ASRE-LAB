from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.module1_design.cad_v2_compiler import compile_design, export_compiled_design
from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2, ValidationStatus

pytestmark = pytest.mark.integration


def q(value, unit="mm"):
    return {"value": value, "unit": unit}


def ref(name):
    return {"parameter": name}


def p2(x, y):
    return {"x": q(x), "y": q(y)}


def v3(x=0, y=0, z=0):
    return {"x": q(x), "y": q(y), "z": q(z)}


def test_multisection_loft_and_path_sweep_produce_valid_solids():
    document = EngineeringDesignDocumentV2.model_validate({
        "document_id": "advanced_profiles",
        "datum_planes": [
            {"datum_id": "z10", "origin": v3(z=10)},
            {"datum_id": "z20", "origin": v3(z=20)},
        ],
        "paths": [{"path_id": "duct_path", "points": [v3(), v3(x=25), v3(x=50, y=15)]}],
        "bodies": [{"body_id": "lofted"}, {"body_id": "swept"}],
        "sketches": [
            {"sketch_id": "loft_a", "plane": "XY", "entities": [
                {"entity_type": "circle", "entity_id": "a", "radius": q(10)}]},
            {"sketch_id": "loft_b", "plane": "z10", "entities": [
                {"entity_type": "circle", "entity_id": "b", "radius": q(7)}]},
            {"sketch_id": "loft_c", "plane": "z20", "entities": [
                {"entity_type": "circle", "entity_id": "c", "radius": q(12)}]},
            {"sketch_id": "sweep_profile", "plane": "YZ", "entities": [
                {"entity_type": "circle", "entity_id": "pipe_section", "radius": q(3)}]},
        ],
        "features": [
            {"operation": "loft", "feature_id": "three_sections", "sketch_ids": ["loft_a", "loft_b", "loft_c"],
             "output_body": "lofted", "transition": "right"},
            {"operation": "sweep", "feature_id": "path_sweep", "sketch_ids": ["sweep_profile"],
             "path_id": "duct_path", "output_body": "swept", "transition": "round", "is_frenet": True},
        ],
        "output_body_ids": ["lofted", "swept"],
    })
    compiled = compile_design(document)
    assert compiled.validation.status == ValidationStatus.VALID
    assert compiled.validation.solid_count == 2
    assert compiled.bodies["lofted"].val().BoundingBox().zlen == pytest.approx(20, abs=0.1)
    assert compiled.bodies["swept"].val().Volume() > math.pi * 3**2 * 45


def test_shell_taper_mirror_split_grid_and_advanced_transform_are_geometric():
    document = EngineeringDesignDocumentV2.model_validate({
        "document_id": "advanced_feature_matrix",
        "bodies": [{"body_id": name} for name in (
            "box", "shell", "peg", "grid", "mirrored", "rotated", "split_out", "split_in"
        )],
        "sketches": [
            {"sketch_id": "box_profile", "entities": [
                {"entity_type": "rectangle", "entity_id": "box_rect", "width": q(40), "height": q(30)}]},
            {"sketch_id": "peg_profile", "entities": [
                {"entity_type": "circle", "entity_id": "peg_circle", "radius": q(2), "center": p2(5, 0)}]},
        ],
        "features": [
            {"operation": "extrude", "feature_id": "taper_box", "sketch_id": "box_profile", "output_body": "box",
             "distance": q(20), "taper_angle": q(2, "deg")},
            {"operation": "shell", "feature_id": "hollow", "source_body": "box", "output_body": "shell",
             "thickness": q(2), "remove_faces": "max_z"},
            {"operation": "extrude", "feature_id": "make_peg", "sketch_id": "peg_profile", "output_body": "peg",
             "distance": q(10)},
            {"operation": "grid_pattern", "feature_id": "peg_grid", "source_body": "peg", "output_body": "grid",
             "x_spacing": q(10), "y_spacing": q(10), "x_count": 2, "y_count": 3},
            {"operation": "mirror", "feature_id": "mirror_grid", "source_body": "grid", "output_body": "mirrored",
             "plane": "YZ", "union": False},
            {"operation": "transform", "feature_id": "rotate_grid", "source_body": "mirrored", "output_body": "rotated",
             "translation": v3(y=5), "rotation": {"axis_direction": [0, 0, 1], "angle": q(90, "deg")}},
            {"operation": "split", "feature_id": "outside_partition", "target_body": "box", "tool_body": "peg",
             "output_body": "split_out", "keep": "outside"},
            {"operation": "split", "feature_id": "inside_partition", "target_body": "box", "tool_body": "peg",
             "output_body": "split_in", "keep": "inside"},
        ],
        "output_body_ids": ["shell", "rotated", "split_out", "split_in"],
    })
    compiled = compile_design(document)
    assert compiled.validation.status == ValidationStatus.VALID
    assert compiled.bodies["shell"].val().Volume() < compiled.bodies["box"].val().Volume()
    assert compiled.bodies["grid"].solids().size() == 6
    assert compiled.bodies["mirrored"].solids().size() == 6
    assert compiled.bodies["split_out"].val().Volume() < compiled.bodies["box"].val().Volume()
    assert compiled.bodies["split_in"].val().Volume() > 0


@pytest.mark.parametrize("hole_type", ["through", "blind", "counterbore", "countersink"])
def test_typed_engineering_holes_remove_expected_material(hole_type):
    feature = {
        "operation": "hole", "feature_id": "drill", "source_body": "plate", "output_body": "drilled",
        "center": v3(z=20), "axis_direction": [0, 0, -1], "hole_type": hole_type,
        "diameter": q(8), "depth": q(20),
    }
    if hole_type == "counterbore":
        feature.update(counterbore_diameter=q(14), counterbore_depth=q(5))
    if hole_type == "countersink":
        feature.update(countersink_diameter=q(16), countersink_angle=q(90, "deg"))
    document = EngineeringDesignDocumentV2.model_validate({
        "document_id": f"{hole_type}_hole",
        "bodies": [{"body_id": "plate"}, {"body_id": "drilled"}],
        "sketches": [{"sketch_id": "plate_profile", "entities": [
            {"entity_type": "rectangle", "entity_id": "outline", "width": q(50), "height": q(40)}]}],
        "features": [
            {"operation": "extrude", "feature_id": "plate_extrude", "sketch_id": "plate_profile",
             "output_body": "plate", "distance": q(20)},
            feature,
        ],
        "output_body_ids": ["drilled"],
        "semantic_regions": [{
            "tag": "bolt_hole_wall", "body_id": "drilled", "source_feature_id": "drill",
            "selector": {"selector_type": "cylindrical_radius", "radius": q(4), "allow_multiple": False},
        }],
    })
    compiled = compile_design(document)
    removed = compiled.bodies["plate"].val().Volume() - compiled.bodies["drilled"].val().Volume()
    assert removed > math.pi * 4**2 * 10
    assert compiled.semantic_regions[0]["topology_signatures"]


def test_complex_authoritative_export_can_select_step_or_preview_only(tmp_path: Path):
    document = EngineeringDesignDocumentV2.model_validate({
        "document_id": "selective_artifacts",
        "bodies": [{"body_id": "solid"}],
        "sketches": [{"sketch_id": "profile", "entities": [
            {"entity_type": "circle", "entity_id": "circle", "radius": q(10)}]}],
        "features": [{"operation": "extrude", "feature_id": "make", "sketch_id": "profile",
                      "output_body": "solid", "distance": q(20)}],
        "output_body_ids": ["solid"],
    })
    preview = export_compiled_design(compile_design(document), tmp_path, formats=("stl",))
    authoritative = export_compiled_design(compile_design(document), tmp_path, formats=("step",))
    assert [item.metadata.file_format for item in preview] == ["stl"]
    assert [item.metadata.file_format for item in authoritative] == ["step"]
