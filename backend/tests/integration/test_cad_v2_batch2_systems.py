from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.module1_design.cad_v2_compiler import (
    CADCompilationError,
    FeatureCompilationCache,
    compile_design,
)
from app.module1_design.cad_v2_constraints import solve_sketch_constraints
from app.module1_design.cad_v2_design_space import (
    V2DesignSpaceRequest,
    build_design_variants,
    execute_design_space,
    variant_chunks,
)
from app.module1_design.cad_v2_plan import DesignIntentPlan, assess_design_plan
from app.module1_design.cad_v2_rebuild import rebuild_design
from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2, SketchDefinition


def q(value: float, unit: str = "mm") -> dict:
    return {"value": value, "unit": unit}


def p(x: float, y: float) -> dict:
    return {"x": q(x), "y": q(y)}


def ref(name: str) -> dict:
    return {"parameter": name}


def _solve(sketch: SketchDefinition):
    def point(value):
        return (value.x.value, value.y.value)

    return solve_sketch_constraints(
        sketch,
        point=point,
        length=lambda value: value.value,
        angle=lambda value: value.value,
        residual_tolerance=1e-8,
    ).result


def test_sketch_solver_reports_all_four_states_without_false_precision():
    fixed = SketchDefinition.model_validate({
        "sketch_id": "fixed",
        "constraint_mode": "constraint_driven",
        "entities": [{"entity_type": "line", "entity_id": "edge", "start": p(0, 0), "end": p(10, 0)}],
        "constraints": [{"constraint_type": "fixed", "constraint_id": "fix", "entity_id": "edge"}],
    })
    under = SketchDefinition.model_validate({
        "sketch_id": "under",
        "constraint_mode": "constraint_driven",
        "entities": [{"entity_type": "line", "entity_id": "edge", "start": p(0, 0), "end": p(10, 1)}],
        "constraints": [{"constraint_type": "horizontal", "constraint_id": "horizontal", "entity_id": "edge"}],
    })
    unsupported = SketchDefinition.model_validate({
        "sketch_id": "invalid",
        "constraint_mode": "constraint_driven",
        "entities": [
            {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(10, 0)},
            {"entity_type": "line", "entity_id": "b", "start": p(10, 0), "end": p(10, 10)},
        ],
        "constraints": [{"constraint_type": "tangent", "constraint_id": "tangent", "first_entity_id": "a", "second_entity_id": "b"}],
    })
    over = SketchDefinition.model_validate({
        "sketch_id": "over",
        "constraint_mode": "constraint_driven",
        "entities": [{"entity_type": "circle", "entity_id": "circle", "radius": q(5)}],
        "constraints": [
            {"constraint_type": "radius", "constraint_id": "r5", "entity_id": "circle", "value": q(5)},
            {"constraint_type": "radius", "constraint_id": "r6", "entity_id": "circle", "value": q(6)},
        ],
    })
    assert _solve(fixed).state.value == "FULLY_CONSTRAINED"
    assert _solve(under).state.value == "UNDERCONSTRAINED"
    invalid = _solve(unsupported)
    assert invalid.state.value == "INVALID"
    assert "not supported" in invalid.diagnostics[0]
    assert _solve(over).state.value == "OVERCONSTRAINED"


@pytest.mark.parametrize("constraint,entities", [
    ({"constraint_type": "coincident", "constraint_id": "c", "first_entity_id": "a", "second_entity_id": "b"}, [
        {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(10, 0)},
        {"entity_type": "line", "entity_id": "b", "start": p(10, 0), "end": p(10, 10)},
    ]),
    ({"constraint_type": "horizontal", "constraint_id": "c", "entity_id": "a"}, [
        {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(10, 0)},
    ]),
    ({"constraint_type": "vertical", "constraint_id": "c", "entity_id": "a"}, [
        {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(0, 10)},
    ]),
    ({"constraint_type": "parallel", "constraint_id": "c", "first_entity_id": "a", "second_entity_id": "b"}, [
        {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(10, 0)},
        {"entity_type": "line", "entity_id": "b", "start": p(0, 5), "end": p(10, 5)},
    ]),
    ({"constraint_type": "perpendicular", "constraint_id": "c", "first_entity_id": "a", "second_entity_id": "b"}, [
        {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(10, 0)},
        {"entity_type": "line", "entity_id": "b", "start": p(10, 0), "end": p(10, 10)},
    ]),
    ({"constraint_type": "equal", "constraint_id": "c", "first_entity_id": "a", "second_entity_id": "b"}, [
        {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(10, 0)},
        {"entity_type": "line", "entity_id": "b", "start": p(0, 5), "end": p(10, 5)},
    ]),
    ({"constraint_type": "distance", "constraint_id": "c", "first_entity_id": "a", "second_entity_id": "b", "value": q(10)}, [
        {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(0, 10)},
        {"entity_type": "line", "entity_id": "b", "start": p(10, 0), "end": p(10, 10)},
    ]),
    ({"constraint_type": "length", "constraint_id": "c", "entity_id": "a", "value": q(10)}, [
        {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(10, 0)},
    ]),
    ({"constraint_type": "radius", "constraint_id": "c", "entity_id": "a", "value": q(5)}, [
        {"entity_type": "circle", "entity_id": "a", "radius": q(5)},
    ]),
    ({"constraint_type": "diameter", "constraint_id": "c", "entity_id": "a", "value": q(10)}, [
        {"entity_type": "circle", "entity_id": "a", "radius": q(5)},
    ]),
    ({"constraint_type": "angle", "constraint_id": "c", "first_entity_id": "a", "second_entity_id": "b", "value": q(90, "deg")}, [
        {"entity_type": "line", "entity_id": "a", "start": p(0, 0), "end": p(10, 0)},
        {"entity_type": "line", "entity_id": "b", "start": p(10, 0), "end": p(10, 10)},
    ]),
])
def test_every_claimed_constraint_has_a_real_kernel_solve(constraint, entities):
    sketch = SketchDefinition.model_validate({
        "sketch_id": "constraint_case",
        "constraint_mode": "constraint_driven",
        "entities": entities,
        "constraints": [constraint],
    })
    result = _solve(sketch)
    assert result.state.value == "UNDERCONSTRAINED", result.diagnostics
    assert result.residual <= 1e-8


def test_solved_circle_dimension_drives_compiled_solid_geometry():
    document = EngineeringDesignDocumentV2.model_validate({
        "document_id": "solved_circle",
        "bodies": [{"body_id": "cylinder"}],
        "sketches": [{
            "sketch_id": "profile",
            "constraint_mode": "constraint_driven",
            "entities": [{"entity_type": "circle", "entity_id": "circle", "radius": q(3)}],
            "constraints": [{"constraint_type": "radius", "constraint_id": "radius", "entity_id": "circle", "value": q(5)}],
        }],
        "features": [{"operation": "extrude", "feature_id": "make", "sketch_id": "profile", "output_body": "cylinder", "distance": q(10)}],
        "output_body_ids": ["cylinder"],
    })
    compiled = compile_design(document)
    assert compiled.sketch_solve_results[0].state.value == "UNDERCONSTRAINED"
    assert compiled.bodies["cylinder"].val().Volume() == pytest.approx(3.141592653589793 * 5**2 * 10, rel=1e-6)


def _assembly_document(second_x: float = 30) -> EngineeringDesignDocumentV2:
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": "small_assembly",
        "bodies": [{"body_id": "block"}],
        "sketches": [{"sketch_id": "profile", "entities": [
            {"entity_type": "rectangle", "entity_id": "outline", "width": q(20), "height": q(20)}
        ]}],
        "features": [{"operation": "extrude", "feature_id": "make", "sketch_id": "profile", "output_body": "block", "distance": q(20)}],
        "output_body_ids": ["block"],
        "components": [{"component_id": "block_component", "name": "Block", "body_ids": ["block"], "material": "Aluminium 6061"}],
        "component_instances": [
            {"instance_id": "first", "component_id": "block_component"},
            {"instance_id": "second", "component_id": "block_component", "repeated_from_instance_id": "first",
             "parent_instance_id": "first",
             "placement": {"translation": {"x": q(second_x), "y": q(0), "z": q(0)}}},
        ],
        "assembly_relationships": [{"relationship_id": "placement", "relationship_type": "offset", "first_instance_id": "first", "second_instance_id": "second", "offset": q(second_x)}],
        "detect_interference": True,
    })


def test_hierarchical_component_instances_and_interference_are_kernel_validated():
    clear = compile_design(_assembly_document())
    assert clear.assembly is not None
    assert clear.assembly.validation.valid
    assert clear.assembly.validation.instance_count == 2
    assert clear.assembly.instance_shapes["second"].val().Center().x - clear.assembly.instance_shapes["first"].val().Center().x == pytest.approx(30)
    assert compile_design(_assembly_document()).assembly.validation.assembly_hash == clear.assembly.validation.assembly_hash
    colliding = compile_design(_assembly_document(5))
    assert colliding.assembly is not None
    assert not colliding.assembly.validation.valid
    assert colliding.assembly.validation.interferences[0].intersection_volume_m3 > 0


def test_assembly_schema_rejects_missing_components_cycles_and_invalid_repeats():
    missing = _assembly_document().model_dump(mode="json")
    missing["component_instances"][0]["component_id"] = "missing"
    with pytest.raises(ValidationError, match="unknown component"):
        EngineeringDesignDocumentV2.model_validate(missing)
    cyclic = _assembly_document().model_dump(mode="json")
    cyclic["component_instances"][0]["parent_instance_id"] = "second"
    with pytest.raises(ValidationError, match="Cyclic component hierarchy"):
        EngineeringDesignDocumentV2.model_validate(cyclic)
    wrong_repeat = _assembly_document().model_dump(mode="json")
    wrong_repeat["components"].append({"component_id": "other", "name": "Other", "body_ids": ["block"]})
    wrong_repeat["component_instances"][1]["component_id"] = "other"
    with pytest.raises(ValidationError, match="different component"):
        EngineeringDesignDocumentV2.model_validate(wrong_repeat)


def _parametric_box(width: float = 20) -> EngineeringDesignDocumentV2:
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": "rebuild_box",
        "parameters": [{"name": "width", "parameter_type": "length", "value": width, "unit": "mm", "minimum": 5, "maximum": 100, "design_variable": True}],
        "bodies": [{"body_id": "box"}],
        "sketches": [{"sketch_id": "profile", "entities": [
            {"entity_type": "rectangle", "entity_id": "outline", "width": ref("width"), "height": q(10)}
        ]}],
        "features": [{"operation": "extrude", "feature_id": "make", "sketch_id": "profile", "output_body": "box", "distance": q(8)}],
        "output_body_ids": ["box"],
        "semantic_regions": [{"tag": "top", "body_id": "box", "source_feature_id": "make", "selector": {"selector_type": "extreme_face", "axis": "z", "extreme": "maximum"}}],
    })


def test_semantic_identity_and_rebuild_report_track_real_dependency_changes():
    initial = compile_design(_parametric_box())
    assert initial.semantic_regions[0]["status"] == "EXACT"
    rebuilt, report = rebuild_design(initial, _parametric_box(25))
    assert report.changed_parameters == ["width"]
    assert report.rebuilt_features == ["make"]
    assert report.changed_bodies == ["box"]
    assert report.semantic_regions_reselected == ["top"]
    assert report.incremental_execution_claimed is False
    assert rebuilt.design_hash != initial.design_hash


def test_rebuild_preserves_unaffected_semantics_and_reports_parameter_driven_loss():
    unchanged_payload = _parametric_box().model_dump(mode="json")
    unchanged_payload["parameters"].append({
        "name": "study_setting", "parameter_type": "scalar", "value": 1.0,
        "minimum": 0, "maximum": 2, "design_variable": True,
    })
    original = EngineeringDesignDocumentV2.model_validate(unchanged_payload)
    previous = compile_design(original)
    unchanged_payload["parameters"][1]["value"] = 2.0
    _, preserved = rebuild_design(previous, EngineeringDesignDocumentV2.model_validate(unchanged_payload))
    assert preserved.unchanged_features == ["make"]
    assert preserved.semantic_regions_preserved == ["top"]

    def plate(diameter: float):
        return EngineeringDesignDocumentV2.model_validate({
            "document_id": "semantic_loss",
            "parameters": [{"name": "diameter", "parameter_type": "length", "value": diameter, "unit": "mm", "design_variable": True}],
            "bodies": [{"body_id": "plate"}, {"body_id": "drilled"}],
            "sketches": [{"sketch_id": "plate_profile", "entities": [{"entity_type": "rectangle", "entity_id": "outline", "width": q(30), "height": q(30)}]}],
            "features": [
                {"operation": "extrude", "feature_id": "make_plate", "sketch_id": "plate_profile", "output_body": "plate", "distance": q(10)},
                {"operation": "hole", "feature_id": "drill", "source_body": "plate", "output_body": "drilled", "hole_type": "through", "diameter": ref("diameter"), "center": {"x": q(0), "y": q(0), "z": q(10)}, "axis_direction": [0, 0, -1]},
            ],
            "output_body_ids": ["drilled"],
            "semantic_regions": [{"tag": "original_bore", "body_id": "drilled", "source_feature_id": "drill", "selector": {"selector_type": "cylindrical_radius", "radius": q(4)}}],
        })

    drilled = compile_design(plate(8))
    _, lost = rebuild_design(drilled, plate(10))
    assert lost.semantic_regions_lost == ["original_bore"]
    assert not lost.semantic_regions_preserved


def test_ambiguous_semantic_selection_fails_instead_of_guessing():
    payload = _parametric_box().model_dump(mode="json")
    payload["semantic_regions"][0]["selector"] = {
        "selector_type": "geometry_type", "topology": "face", "geometry_type": "plane"
    }
    with pytest.raises(CADCompilationError) as caught:
        compile_design(EngineeringDesignDocumentV2.model_validate(payload))
    assert caught.value.code == "AMBIGUOUS_SEMANTIC_SELECTOR"
    schema_text = str(EngineeringDesignDocumentV2.model_json_schema()).lower()
    assert "face_index" not in schema_text
    assert "edge_index" not in schema_text


def test_design_space_is_deterministic_chunked_and_scales_past_fifty_variants():
    request = V2DesignSpaceRequest.model_validate({
        "document": _parametric_box(),
        "sweeps": [{"parameter_name": "width", "method": "linear", "start": q(5), "stop": q(64), "count": 60}],
        "chunk_size": 13,
        "artifact_mode": "deferred",
    })
    first = build_design_variants(request)
    second = build_design_variants(request)
    assert [item.variant_id for item in first] == [item.variant_id for item in second]
    assert [len(chunk) for chunk in variant_chunks(request)] == [13, 13, 13, 13, 8]
    result = execute_design_space(request)
    assert result.requested_count == result.completed_count == 60
    assert result.failed_count == result.cancelled_count == 0
    assert all(not item.artifact_ids for item in result.variants)


def test_all_design_variable_kinds_have_bounded_deterministic_axes():
    payload = _parametric_box().model_dump(mode="json")
    payload["parameters"].extend([
        {"name": "count", "parameter_type": "integer", "value": 2, "minimum": 1, "maximum": 5, "design_variable": True},
        {"name": "enabled", "parameter_type": "boolean", "value": True, "design_variable": True},
        {"name": "mode", "parameter_type": "categorical", "value": "light", "choices": ["light", "stiff"], "design_variable": True},
        {"name": "ratio", "parameter_type": "scalar", "value": 1.0, "minimum": 0.5, "maximum": 2.0, "design_variable": True},
    ])
    document = EngineeringDesignDocumentV2.model_validate(payload)
    request = V2DesignSpaceRequest.model_validate({
        "document": document,
        "sweeps": [
            {"parameter_name": "count", "method": "integer_range", "start": 1, "stop": 3},
            {"parameter_name": "enabled", "method": "boolean"},
            {"parameter_name": "mode", "method": "categorical", "values": ["light", "stiff"]},
            {"parameter_name": "ratio", "method": "explicit", "values": [0.5, 1.5]},
        ],
    })
    variants = build_design_variants(request)
    assert len(variants) == 24
    assert variants[0].parameter_values == {"count": 1, "enabled": False, "mode": "light", "ratio": 0.5}
    assert variants[-1].parameter_values == {"count": 3, "enabled": True, "mode": "stiff", "ratio": 1.5}


def test_design_space_rejects_bad_units_bounds_and_selection_indices():
    base = {"document": _parametric_box(), "artifact_mode": "deferred"}
    for sweep, message in [
        ({"parameter_name": "width", "method": "explicit", "values": [q(20, "deg")]}, "length unit"),
        ({"parameter_name": "width", "method": "explicit", "values": [q(101)]}, "maximum"),
    ]:
        request = V2DesignSpaceRequest.model_validate({**base, "sweeps": [sweep]})
        with pytest.raises(ValueError, match=message):
            build_design_variants(request)
    bad_selection = V2DesignSpaceRequest.model_validate({
        **base,
        "artifact_mode": "selected",
        "selected_artifact_indices": [3],
        "sweeps": [{"parameter_name": "width", "method": "explicit", "values": [q(10), q(20)]}],
    })
    with pytest.raises(ValueError, match="outside"):
        build_design_variants(bad_selection)


def test_selective_artifacts_cancellation_and_partial_geometry_failure(tmp_path):
    selected = V2DesignSpaceRequest.model_validate({
        "document": _parametric_box(),
        "sweeps": [{"parameter_name": "width", "method": "explicit", "values": [q(10), q(20), q(30)]}],
        "artifact_mode": "selected",
        "selected_artifact_indices": [1],
    })
    exported = execute_design_space(selected, export_directory=tmp_path)
    assert [len(item.artifact_ids) for item in exported.variants] == [0, 2, 0]
    cancelled = execute_design_space(selected, cancelled=lambda: True)
    assert cancelled.cancelled_count == cancelled.requested_count == 3

    shell_payload = _parametric_box().model_dump(mode="json")
    shell_payload["parameters"][0] = {
        "name": "draft", "parameter_type": "angle", "value": 2, "unit": "deg",
        "minimum": 0, "maximum": 60, "design_variable": True,
    }
    shell_payload["sketches"][0]["entities"][0]["width"] = q(30)
    shell_payload["features"][0]["taper_angle"] = ref("draft")
    shell_payload["semantic_regions"] = []
    partial = V2DesignSpaceRequest.model_validate({
        "document": shell_payload,
        "sweeps": [{"parameter_name": "draft", "method": "explicit", "values": [q(2, "deg"), q(50, "deg")]}],
        "continue_on_error": True,
    })
    result = execute_design_space(partial)
    assert result.completed_count == result.failed_count == 1
    assert result.variants[1].error_code == "INVALID_DRAFT"


def test_feature_cache_invalidates_only_the_affected_dependency_branch():
    payload = {
        "document_id": "independent_branches",
        "parameters": [
            {"name": "a", "parameter_type": "length", "value": 10, "unit": "mm", "design_variable": True},
            {"name": "b", "parameter_type": "length", "value": 20, "unit": "mm", "design_variable": True},
        ],
        "bodies": [{"body_id": "a_body"}, {"body_id": "b_body"}],
        "sketches": [
            {"sketch_id": "a_profile", "entities": [{"entity_type": "circle", "entity_id": "a_circle", "radius": ref("a")}]},
            {"sketch_id": "b_profile", "entities": [{"entity_type": "circle", "entity_id": "b_circle", "radius": ref("b")}]},
        ],
        "features": [
            {"operation": "extrude", "feature_id": "make_a", "sketch_id": "a_profile", "output_body": "a_body", "distance": q(5)},
            {"operation": "extrude", "feature_id": "make_b", "sketch_id": "b_profile", "output_body": "b_body", "distance": q(5)},
        ],
        "output_body_ids": ["a_body", "b_body"],
    }
    cache = FeatureCompilationCache()
    initial = compile_design(EngineeringDesignDocumentV2.model_validate(payload), cache=cache)
    payload["parameters"][1]["value"] = 25
    changed = compile_design(EngineeringDesignDocumentV2.model_validate(payload), cache=cache)
    assert initial.cache_hits == []
    assert changed.cache_hits == ["make_a"]
    assert initial.feature_hashes["make_a"] == changed.feature_hashes["make_a"]
    assert initial.feature_hashes["make_b"] != changed.feature_hashes["make_b"]


def test_design_plan_is_non_authoritative_until_ambiguity_is_removed():
    blocked = DesignIntentPlan.model_validate({"questions": ["What wall thickness?"]})
    assert not assess_design_plan(blocked).ready_for_translation
    ready = DesignIntentPlan.model_validate({"design_document_candidate": _parametric_box().model_dump(mode="json")})
    assessment = assess_design_plan(ready)
    assert assessment.ready_for_translation
    assert assessment.candidate_validated
