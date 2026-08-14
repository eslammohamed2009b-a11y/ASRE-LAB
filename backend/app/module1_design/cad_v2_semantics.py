"""Deterministic geometric semantic selectors without public face indices."""
from __future__ import annotations

import math
from typing import Any, Callable

import cadquery as cq

from app.module1_design.cad_v2_schemas import (
    CylindricalFaceSelector,
    EngineeringDesignDocumentV2,
    ExtremeFaceSelector,
    GeometryTypeSelector,
    NormalFaceSelector,
    ResolvedSemanticRegion,
    SemanticIdentityStatus,
    SemanticSelector,
)


class SemanticTopologyError(ValueError):
    def __init__(self, code: str, message: str, tag: str):
        super().__init__(message)
        self.code = code
        self.tag = tag


def _rounded(value: float) -> float:
    return float(format(float(value), ".12g"))


def _signature(shape: Any, kind: str) -> str:
    from app.v2.execution import digest

    box = shape.BoundingBox()
    center = shape.Center()
    payload: dict[str, Any] = {
        "kind": kind,
        "geometry_type": shape.geomType().lower(),
        "measure_mm": _rounded(shape.Area() if kind == "face" else shape.Length()),
        "center_mm": [_rounded(center.x), _rounded(center.y), _rounded(center.z)],
        "bounds_mm": [
            _rounded(box.xmin), _rounded(box.ymin), _rounded(box.zmin),
            _rounded(box.xmax), _rounded(box.ymax), _rounded(box.zmax),
        ],
    }
    if kind == "face" and shape.geomType() == "PLANE":
        normal = shape.normalAt()
        payload["normal"] = [_rounded(normal.x), _rounded(normal.y), _rounded(normal.z)]
    if kind == "face" and shape.geomType() == "CYLINDER":
        try:
            payload["radius_mm"] = _rounded(shape._geomAdaptor().Cylinder().Radius())
        except Exception:
            pass
    return digest(payload)


def _axis_value(vector, axis: str) -> float:
    return float(getattr(vector, axis))


def _resolve_one(region, workplane, *, resolve_length_mm: Callable[[Any], float], tolerance_mm: float):
    faces = list(workplane.faces().vals())
    edges = list(workplane.edges().vals())
    selector = region.selector
    if isinstance(selector, SemanticSelector):
        if selector == SemanticSelector.ALL_FACES:
            return "face", faces, SemanticIdentityStatus.DERIVED
        if selector == SemanticSelector.ALL_EDGES:
            return "edge", edges, SemanticIdentityStatus.DERIVED
        box = workplane.val().BoundingBox()
        lengths = {"x": box.xlen, "y": box.ylen, "z": box.zlen}
        axis = max(lengths, key=lengths.get)
        planar = [item for item in faces if item.geomType() == "PLANE"]
        end_faces = []
        for face in planar:
            normal = face.normalAt()
            if abs(_axis_value(normal, axis)) >= 1 - 1e-7:
                end_faces.append(face)
        selected = end_faces if selector == SemanticSelector.END_FACES else [
            item for item in faces if item not in end_faces
        ]
        return "face", selected, SemanticIdentityStatus.DERIVED

    if isinstance(selector, ExtremeFaceSelector):
        candidates = faces
        expected = selector.surface_type.upper()
        if expected != "ANY":
            candidates = [item for item in candidates if item.geomType() == expected]
        if not candidates:
            return "face", [], SemanticIdentityStatus.LOST
        coordinates = [_axis_value(item.Center(), selector.axis) for item in candidates]
        target = min(coordinates) if selector.extreme == "minimum" else max(coordinates)
        matches = [item for item, coordinate in zip(candidates, coordinates) if abs(coordinate - target) <= tolerance_mm]
        if len(matches) > 1:
            raise SemanticTopologyError(
                "AMBIGUOUS_SEMANTIC_SELECTOR",
                f"Semantic selector for '{region.tag}' matched {len(matches)} equally extreme faces",
                region.tag,
            )
        return "face", matches, SemanticIdentityStatus.EXACT

    if isinstance(selector, NormalFaceSelector):
        magnitude = math.sqrt(sum(item * item for item in selector.direction))
        if magnitude <= 1e-15:
            raise SemanticTopologyError("INVALID_SEMANTIC_SELECTOR", "Normal direction is zero", region.tag)
        direction = tuple(item / magnitude for item in selector.direction)
        candidates = []
        for face in faces:
            if face.geomType() != "PLANE":
                continue
            normal = face.normalAt()
            dot = normal.x * direction[0] + normal.y * direction[1] + normal.z * direction[2]
            if dot >= 1 - 1e-7:
                candidates.append(face)
        if selector.largest and candidates:
            largest = max(item.Area() for item in candidates)
            candidates = [item for item in candidates if abs(item.Area() - largest) <= tolerance_mm**2]
        if len(candidates) > 1:
            raise SemanticTopologyError(
                "AMBIGUOUS_SEMANTIC_SELECTOR", "Normal-face selector has multiple equal matches", region.tag
            )
        return "face", candidates, SemanticIdentityStatus.EXACT

    if isinstance(selector, CylindricalFaceSelector):
        radius = resolve_length_mm(selector.radius)
        radius_tolerance = resolve_length_mm(selector.radius_tolerance)
        candidates = []
        for face in faces:
            if face.geomType() != "CYLINDER":
                continue
            try:
                actual = float(face._geomAdaptor().Cylinder().Radius())
            except Exception:
                continue
            if abs(actual - radius) <= radius_tolerance:
                candidates.append(face)
        if len(candidates) > 1 and not selector.allow_multiple:
            raise SemanticTopologyError(
                "AMBIGUOUS_SEMANTIC_SELECTOR", "Cylindrical selector requires disambiguation", region.tag
            )
        return "face", candidates, SemanticIdentityStatus.RESELECTED

    if isinstance(selector, GeometryTypeSelector):
        pool = faces if selector.topology == "face" else edges
        candidates = [item for item in pool if item.geomType().lower() == selector.geometry_type]
        if len(candidates) > 1 and not selector.allow_multiple:
            raise SemanticTopologyError(
                "AMBIGUOUS_SEMANTIC_SELECTOR", "Geometry-type selector requires disambiguation", region.tag
            )
        return selector.topology, candidates, SemanticIdentityStatus.RESELECTED
    raise SemanticTopologyError("INVALID_SEMANTIC_SELECTOR", "Unknown semantic selector", region.tag)


def resolve_semantic_regions(
    document: EngineeringDesignDocumentV2,
    bodies: dict[str, cq.Workplane],
    *,
    resolve_length_mm: Callable[[Any], float],
    tolerance_mm: float,
    fail_on_lost: bool = True,
) -> list[ResolvedSemanticRegion]:
    resolved: list[ResolvedSemanticRegion] = []
    for region in document.semantic_regions:
        kind, shapes, status = _resolve_one(
            region,
            bodies[region.body_id],
            resolve_length_mm=resolve_length_mm,
            tolerance_mm=tolerance_mm,
        )
        if not shapes:
            if fail_on_lost:
                raise SemanticTopologyError(
                    "SEMANTIC_REGION_LOST", f"Semantic region '{region.tag}' could not be resolved", region.tag
                )
            resolved.append(ResolvedSemanticRegion(
                tag=region.tag,
                body_id=region.body_id,
                status=SemanticIdentityStatus.LOST,
                topology_kind=kind,
                topology_signatures=[],
                diagnostic="The declared selector no longer matches geometry",
            ))
            continue
        signatures = sorted(_signature(item, kind) for item in shapes)
        resolved.append(ResolvedSemanticRegion(
            tag=region.tag,
            body_id=region.body_id,
            status=status,
            topology_kind=kind,
            topology_signatures=signatures,
        ))
    return resolved
