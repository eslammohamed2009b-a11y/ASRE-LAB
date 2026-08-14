"""Explicit bridge from legacy shape-family requests to the general V2 IR.

The existing public endpoints intentionally remain on ``cadquery_engine`` in
Batch 1 to avoid changing their historical numeric/export behaviour. This
adapter proves a bounded migration path and makes unit conversion explicit;
new V2 compilation is the authority for documents it returns.
"""
from __future__ import annotations

from app.module1_design.schemas import DesignParameters, GeometryType
from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2


def _q(value, unit="m") -> dict:
    return {"value": value, "unit": unit}


def _ref(name: str) -> dict:
    return {"parameter": name}


def adapt_legacy_design(params: DesignParameters) -> EngineeringDesignDocumentV2:
    """Translate one supported legacy family into generic V2 operations.

    This is a compatibility adapter, not a CAD builder: its result contains
    only public V2 parameters, sketches, datum planes, and generic features.
    Legacy ``*_m`` values are interpreted as metres and converted by the V2
    compiler to its millimetre kernel boundary.
    """
    geometry = params.geometry_type
    material = params.material.value if params.material else None
    if geometry == GeometryType.BRIDGE:
        return EngineeringDesignDocumentV2.model_validate({
            "document_id": "legacy_bridge_adapter",
            "parameters": [
                {"name": "span", "parameter_type": "length", "value": params.base_length_m, "unit": "m"},
                {"name": "deck_width", "parameter_type": "length", "value": 10, "unit": "m"},
                {"name": "deck_height", "parameter_type": "length", "value": 2, "unit": "m"},
            ],
            "bodies": [{"body_id": "bridge_deck", "material": material}],
            "sketches": [{"sketch_id": "deck_profile", "plane": "XY", "entities": [{
                "entity_type": "rectangle", "entity_id": "deck_outline",
                "width": _ref("span"), "height": _ref("deck_width"),
            }]}],
            "features": [{"operation": "extrude", "feature_id": "extrude_deck",
                          "sketch_id": "deck_profile", "output_body": "bridge_deck",
                          "distance": _ref("deck_height")}],
            "output_body_ids": ["bridge_deck"],
        })
    if geometry == GeometryType.TOWER:
        wall = params.wall_thickness_m or 0.5
        inner = float(params.base_length_m) - 2 * wall
        if inner <= 0:
            raise ValueError("Legacy tower wall thickness leaves no interior")
        return EngineeringDesignDocumentV2.model_validate({
            "document_id": "legacy_tower_adapter",
            "parameters": [
                {"name": "outer_width", "parameter_type": "length", "value": params.base_length_m, "unit": "m"},
                {"name": "inner_width", "parameter_type": "length", "value": inner, "unit": "m",
                 "role": "resolved legacy derived dimension"},
                {"name": "tower_height", "parameter_type": "length", "value": params.height_m, "unit": "m"},
            ],
            "bodies": [{"body_id": "tower_shell", "material": material}],
            "sketches": [{"sketch_id": "shell_profile", "plane": "XY", "entities": [
                {"entity_type": "rectangle", "entity_id": "outer", "width": _ref("outer_width"),
                 "height": _ref("outer_width")},
                {"entity_type": "rectangle", "entity_id": "inner", "width": _ref("inner_width"),
                 "height": _ref("inner_width")},
            ]}],
            "features": [{"operation": "extrude", "feature_id": "extrude_shell",
                          "sketch_id": "shell_profile", "output_body": "tower_shell",
                          "distance": _ref("tower_height")}],
            "output_body_ids": ["tower_shell"],
        })
    if geometry == GeometryType.PYRAMID:
        return EngineeringDesignDocumentV2.model_validate({
            "document_id": "legacy_pyramid_adapter",
            "parameters": [
                {"name": "base_width", "parameter_type": "length", "value": params.base_length_m, "unit": "m"},
                {"name": "pyramid_height", "parameter_type": "length", "value": params.height_m, "unit": "m"},
            ],
            "datum_planes": [{"datum_id": "apex_plane", "origin": {"z": _ref("pyramid_height")}}],
            "bodies": [{"body_id": "pyramid", "material": material}],
            "sketches": [
                {"sketch_id": "base_profile", "plane": "XY", "entities": [{
                    "entity_type": "rectangle", "entity_id": "base", "width": _ref("base_width"),
                    "height": _ref("base_width")}]},
                {"sketch_id": "apex_profile", "plane": "apex_plane", "entities": [{
                    "entity_type": "rectangle", "entity_id": "apex", "width": _q(1, "mm"),
                    "height": _q(1, "mm")}]},
            ],
            "features": [{"operation": "loft", "feature_id": "loft_pyramid",
                          "sketch_ids": ["base_profile", "apex_profile"], "output_body": "pyramid",
                          "ruled": True}],
            "output_body_ids": ["pyramid"],
        })
    raise ValueError(f"Legacy geometry '{geometry}' has no executable V2 compatibility adapter")
