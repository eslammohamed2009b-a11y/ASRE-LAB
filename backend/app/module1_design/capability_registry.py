"""Authoritative, machine-readable contract for executable CAD generation.

Natural-language interpretation may recognise more geometry words than the
CadQuery engine can execute.  This registry is the execution gate; it is not a
second parser vocabulary.
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class DesignImplementationStatus(str, Enum):
    SUPPORTED = "supported"
    UNDERSTOOD_BUT_UNSUPPORTED = "understood_but_unsupported"


DESIGN_CAPABILITY_REGISTRY: dict[str, dict[str, Any]] = {
    "pyramid": {
        "geometry_id": "pyramid", "display_name": "Square pyramid",
        "implementation_status": "supported", "cad_backend": "CadQuery/OCP",
        "generator_version": "1.0.0",
        "supported_parameters": ["base_length_m", "height_m", "slope_angle_deg", "material"],
        "required_parameters": [], "derived_parameters": ["slope_angle_deg", "base_length_m", "height_m"],
        "supported_operations": ["generate", "export_step", "export_stl"], "units": "SI metres/degrees",
        "known_constraints": ["square base", "positive dimensions"],
        "known_limitations": ["Near-zero apex is used to avoid a degenerate CAD loft."],
        "produces_step": True, "produces_stl": True,
        "implementation_reference": "app.module1_design.cadquery_engine.build_pyramid",
    },
    "tower": {
        "geometry_id": "tower", "display_name": "Hollow square tower",
        "implementation_status": "supported", "cad_backend": "CadQuery/OCP", "generator_version": "1.0.0",
        "supported_parameters": ["base_length_m", "height_m", "wall_thickness_m", "material"],
        "required_parameters": [], "derived_parameters": [],
        "supported_operations": ["generate", "export_step", "export_stl"], "units": "SI metres",
        "known_constraints": ["wall thickness must be less than half the base length"],
        "known_limitations": ["Straight prismatic square section only."], "produces_step": True, "produces_stl": True,
        "implementation_reference": "app.module1_design.cadquery_engine.build_tower",
    },
    "bridge": {
        "geometry_id": "bridge", "display_name": "Rectangular bridge deck",
        "implementation_status": "supported", "cad_backend": "CadQuery/OCP", "generator_version": "1.0.0",
        "supported_parameters": ["base_length_m", "material"], "required_parameters": [], "derived_parameters": [],
        "supported_operations": ["generate", "export_step", "export_stl"], "units": "SI metres",
        "known_constraints": ["positive span"],
        "known_limitations": ["This is a rectangular deck primitive, not a structural bridge model."],
        "produces_step": True, "produces_stl": True,
        "implementation_reference": "app.module1_design.cadquery_engine.build_bridge",
    },
    "arch": {"geometry_id": "arch", "display_name": "Arch", "implementation_status": "understood_but_unsupported",
             "known_limitations": ["Recognised by the language interface; no CAD builder exists."], "produces_step": False, "produces_stl": False,
             "implementation_reference": None},
    "dome": {"geometry_id": "dome", "display_name": "Dome", "implementation_status": "understood_but_unsupported",
             "known_limitations": ["Recognised by the language interface; no CAD builder exists."], "produces_step": False, "produces_stl": False,
             "implementation_reference": None},
}


class UnsupportedRecognizedGeometryError(ValueError):
    def __init__(self, geometry_id: str):
        super().__init__(f"Geometry '{geometry_id}' is understood but unsupported: no CAD generator is available.")
        self.geometry_id = geometry_id


def require_executable_geometry(geometry_id: str) -> dict[str, Any]:
    entry = DESIGN_CAPABILITY_REGISTRY.get(geometry_id)
    if entry is None or entry["implementation_status"] != DesignImplementationStatus.SUPPORTED.value:
        raise UnsupportedRecognizedGeometryError(geometry_id)
    return entry


def list_design_capabilities() -> list[dict[str, Any]]:
    return list(DESIGN_CAPABILITY_REGISTRY.values())
