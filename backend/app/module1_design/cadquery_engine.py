"""
Module 1 — Parametric Design Engine.
Every design is a mathematical function f(base, height, material) built
with CadQuery. Code-driven CAD is what makes this reproducible and
scriptable at scale (vs. manual CAD authoring).
"""
import uuid
from pathlib import Path
import tempfile

import cadquery as cq

from app.module1_design.schemas import DesignParameters, GeometryType

EXPORT_DIR = Path(tempfile.gettempdir()) / "asre_lab_exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def build_pyramid(p: DesignParameters) -> cq.Workplane:
    base = p.base_length_m
    height = p.height_m
    # Loft from a square base to a point — a direct geometric encoding
    # of the slope_angle_deg parameter.
    return (
        cq.Workplane("XY")
        .rect(base, base)
        .workplane(offset=height)
        .rect(0.001, 0.001)  # near-zero apex; avoids degenerate loft
        .loft(ruled=True)
    )


def build_tower(p: DesignParameters) -> cq.Workplane:
    outer = p.base_length_m
    inner = outer - 2 * (p.wall_thickness_m or 0.5)
    if inner <= 0:
        raise ValueError("wall_thickness_m is too large for the selected base_length_m")
    return cq.Workplane("XY").rect(outer, outer).rect(inner, inner).extrude(p.height_m)


def build_bridge(p: DesignParameters) -> cq.Workplane:
    span = p.base_length_m
    deck_height = 2.0
    return cq.Workplane("XY").box(span, 10, deck_height)


GEOMETRY_BUILDERS = {
    GeometryType.PYRAMID: build_pyramid,
    GeometryType.TOWER: build_tower,
    GeometryType.BRIDGE: build_bridge,
}


def generate_model(params: DesignParameters) -> dict:
    """
    f(base, height, material) -> exported model file.
    Returns metadata (id + file paths) rather than the raw geometry,
    since geometry is streamed to the frontend as STL for Three.js.
    """
    builder = GEOMETRY_BUILDERS.get(params.geometry_type)
    if builder is None:
        raise ValueError(f"No parametric builder for {params.geometry_type}")

    result = builder(params)

    design_id = str(uuid.uuid4())
    stl_path = EXPORT_DIR / f"{design_id}.stl"
    step_path = EXPORT_DIR / f"{design_id}.step"

    cq.exporters.export(result, str(stl_path))
    cq.exporters.export(result, str(step_path))

    return {
        "design_id": design_id,
        "params": params.model_dump(),
        "stl_path": str(stl_path),
        "step_path": str(step_path),
    }
