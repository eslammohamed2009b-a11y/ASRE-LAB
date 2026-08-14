from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2

pytestmark = pytest.mark.unit


def _quantity(value, unit="mm"):
    return {"value": value, "unit": unit}


def valid_document() -> dict:
    return {
        "schema_version": "2.0",
        "document_id": "generic_plate",
        "parameters": [
            {
                "name": "width",
                "parameter_type": "length",
                "value": 100,
                "unit": "mm",
                "minimum": 1,
                "design_variable": True,
            }
        ],
        "bodies": [{"body_id": "plate"}],
        "sketches": [
            {
                "sketch_id": "profile",
                "plane": "XY",
                "unit": "mm",
                "entities": [
                    {
                        "entity_type": "rectangle",
                        "entity_id": "outline",
                        "width": {"parameter": "width"},
                        "height": _quantity(40),
                    }
                ],
                "constraints": [
                    {"constraint_type": "fixed", "constraint_id": "fixed_outline", "entity_id": "outline"}
                ],
            }
        ],
        "features": [
            {
                "operation": "extrude",
                "feature_id": "make_plate",
                "sketch_id": "profile",
                "output_body": "plate",
                "distance": _quantity(10),
            }
        ],
        "output_body_ids": ["plate"],
        "semantic_regions": [
            {"tag": "mounting_face", "body_id": "plate", "source_feature_id": "make_plate", "selector": "end_faces"}
        ],
    }


def test_valid_typed_design_document_and_fixed_constraint():
    document = EngineeringDesignDocumentV2.model_validate(valid_document())
    assert document.schema_version == "2.0"
    assert document.features[0].operation == "extrude"
    assert document.sketches[0].constraints[0].constraint_type == "fixed"


def test_duplicate_feature_ids_are_rejected():
    payload = valid_document()
    second = deepcopy(payload["features"][0])
    second["output_body"] = "plate_2"
    payload["bodies"].append({"body_id": "plate_2"})
    payload["features"].append(second)
    with pytest.raises(ValidationError, match="Duplicate feature ID"):
        EngineeringDesignDocumentV2.model_validate(payload)


def test_unknown_dependency_is_rejected():
    payload = valid_document()
    payload["features"][0]["dependencies"] = ["missing_feature"]
    with pytest.raises(ValidationError, match="unknown dependency"):
        EngineeringDesignDocumentV2.model_validate(payload)


def test_dependency_cycle_is_rejected():
    payload = valid_document()
    payload["bodies"].append({"body_id": "second"})
    payload["sketches"].append({
        "sketch_id": "profile_2", "entities": [{
            "entity_type": "circle", "entity_id": "circle", "radius": _quantity(5)
        }]
    })
    payload["features"][0]["dependencies"] = ["make_second"]
    payload["features"].append({
        "operation": "extrude", "feature_id": "make_second", "dependencies": ["make_plate"],
        "sketch_id": "profile_2", "output_body": "second", "distance": _quantity(10),
    })
    with pytest.raises(ValidationError, match="cycle"):
        EngineeringDesignDocumentV2.model_validate(payload)


def test_unknown_operation_is_rejected_without_dynamic_dispatch():
    payload = valid_document()
    payload["features"][0]["operation"] = "execute_python"
    payload["features"][0]["source"] = "__import__('os').system('unsafe')"
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        EngineeringDesignDocumentV2.model_validate(payload)


def test_unknown_parameter_reference_is_rejected():
    payload = valid_document()
    payload["sketches"][0]["entities"][0]["width"] = {"parameter": "missing"}
    with pytest.raises(ValidationError, match="Unknown parameter"):
        EngineeringDesignDocumentV2.model_validate(payload)


def test_unknown_body_reference_is_rejected():
    payload = valid_document()
    payload["bodies"].append({"body_id": "moved"})
    payload["features"].append({
        "operation": "transform", "feature_id": "move", "source_body": "missing_body",
        "output_body": "moved",
    })
    payload["output_body_ids"] = ["moved"]
    with pytest.raises(ValidationError, match="unknown body"):
        EngineeringDesignDocumentV2.model_validate(payload)


@pytest.mark.parametrize(
    "parameter",
    [
        {"name": "bad_length", "parameter_type": "length", "value": 1},
        {"name": "bad_scalar", "parameter_type": "scalar", "value": 1, "unit": "mm"},
        {"name": "bad_bool", "parameter_type": "boolean", "value": 1},
    ],
)
def test_invalid_parameter_units_or_types_are_rejected(parameter):
    payload = valid_document()
    payload["parameters"] = [parameter]
    payload["sketches"][0]["entities"][0]["width"] = _quantity(100)
    with pytest.raises(ValidationError):
        EngineeringDesignDocumentV2.model_validate(payload)


def test_deterministic_graph_order_is_independent_of_authored_list_order_for_peers():
    payload = valid_document()
    payload["bodies"].append({"body_id": "pin"})
    payload["sketches"].append({
        "sketch_id": "pin_profile", "entities": [{
            "entity_type": "circle", "entity_id": "pin_circle", "radius": _quantity(2)
        }]
    })
    payload["features"].append({
        "operation": "extrude", "feature_id": "alpha_pin", "sketch_id": "pin_profile",
        "output_body": "pin", "distance": _quantity(5),
    })
    first = EngineeringDesignDocumentV2.model_validate(payload)
    payload["features"].reverse()
    second = EngineeringDesignDocumentV2.model_validate(payload)
    assert first.deterministic_feature_order() == second.deterministic_feature_order()
    assert first.deterministic_feature_order() == ["alpha_pin", "make_plate"]


def test_datum_and_semantic_references_fail_closed():
    payload = valid_document()
    payload["sketches"][0]["plane"] = "missing_datum"
    with pytest.raises(ValidationError, match="unknown plane"):
        EngineeringDesignDocumentV2.model_validate(payload)


def test_unreasonable_tolerance_policy_is_rejected():
    payload = valid_document()
    payload["tolerance_policy"] = {
        "kernel_tolerance": _quantity(1),
        "geometric_equality_absolute": _quantity(0.1),
        "minimum_feature_size": _quantity(2),
    }
    with pytest.raises(ValidationError, match="equality tolerance"):
        EngineeringDesignDocumentV2.model_validate(payload)

    payload["tolerance_policy"]["geometric_equality_absolute"] = _quantity(1)
    with pytest.raises(ValidationError, match="at least 10x"):
        EngineeringDesignDocumentV2.model_validate(payload)
