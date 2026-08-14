"""Deterministic compiler for :mod:`cad_v2_schemas`.

Document and scientific identity lengths are canonical SI metres. CadQuery /
OpenCascade execution is deliberately performed in millimetres. STEP files
therefore declare millimetres; STL is unitless and its coordinates are also
millimetres. This conversion happens only in the resolver below.
"""
from __future__ import annotations

import hashlib
import math
import tempfile
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal

import cadquery as cq

from app.module1_design.cad_v2_schemas import (
    AngleUnit,
    ArcEntity,
    ArtifactMetadata,
    BooleanBase,
    ChamferFeature,
    CircleEntity,
    CircularPatternFeature,
    CompileMetadata,
    EdgeSelector,
    EngineeringDesignDocumentV2,
    ExtrudeFeature,
    FilletFeature,
    GeometryDiagnostic,
    GeometryValidationResult,
    IntersectionFeature,
    LengthUnit,
    LengthVector3,
    LineEntity,
    LinearPatternFeature,
    LoftFeature,
    MeasureInput,
    ParameterDefinition,
    ParameterReference,
    ParameterType,
    Point2D,
    PolylineEntity,
    Quantity,
    RectangleEntity,
    RevolveFeature,
    StandardPlane,
    SubtractFeature,
    TransformFeature,
    UnionFeature,
    ValidationStatus,
)


LENGTH_TO_METRES: dict[LengthUnit, float] = {
    LengthUnit.METRE: 1.0,
    LengthUnit.MILLIMETRE: 1e-3,
    LengthUnit.CENTIMETRE: 1e-2,
    LengthUnit.MICROMETRE: 1e-6,
    LengthUnit.INCH: 0.0254,
}
ANGLE_TO_RADIANS: dict[AngleUnit, float] = {
    AngleUnit.DEGREE: math.pi / 180.0,
    AngleUnit.RADIAN: 1.0,
}


class CADCompilationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        feature_id: str | None = None,
        validation: GeometryValidationResult | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.feature_id = feature_id
        self.validation = validation


@dataclass(frozen=True)
class ResolvedParameter:
    name: str
    parameter_type: ParameterType
    canonical_value: float | int | bool
    design_variable: bool


@dataclass(frozen=True)
class ExportedArtifact:
    metadata: ArtifactMetadata
    path: Path


@dataclass
class CompiledDesign:
    document: EngineeringDesignDocumentV2
    bodies: dict[str, cq.Workplane]
    design_hash: str
    geometry_fingerprint: str
    feature_order: list[str]
    normalized_parameters: dict[str, Any]
    validation: GeometryValidationResult
    geometry_signature: dict[str, Any]
    semantic_regions: list[dict[str, Any]]
    artifacts: list[ExportedArtifact] = field(default_factory=list)

    def metadata(self) -> CompileMetadata:
        return CompileMetadata(
            design_hash=self.design_hash,
            geometry_fingerprint=self.geometry_fingerprint,
            feature_order=self.feature_order,
            normalized_parameters=self.normalized_parameters,
            unit_policy=self.document.unit_policy,
            tolerance_policy=_normalized_tolerance(self.document),
            validation=self.validation,
            artifacts=[item.metadata for item in self.artifacts],
        )


CAD_V2_CAPABILITY_CONTRACT: dict[str, Any] = {
    "schema_version": "2.0",
    "architecture": "typed_feature_graph",
    "implemented_features": [
        "extrude", "revolve", "loft", "union", "subtract", "intersection", "transform",
        "fillet", "chamfer", "linear_pattern", "circular_pattern",
    ],
    "sketch_entities": ["rectangle", "circle", "line", "polyline", "arc"],
    "sketch_constraints": ["fixed"],
    "supported_length_units": [item.value for item in LengthUnit],
    "supported_angle_units": [item.value for item in AngleUnit],
    "canonical_units": {"length": "m", "angle": "rad"},
    "kernel_units": {"length": "mm", "angle": "deg"},
    "exports": {
        "step": {"length_unit": "mm"},
        "stl": {"unitless_coordinates_are": "mm"},
    },
    "multi_body": "supported through named immutable feature outputs and explicit output bodies",
    "semantic_regions": (
        "bounded tags and deterministic selector intent are preserved; persistent face identity "
        "across arbitrary topology-changing edits is not guaranteed"
    ),
    "geometry_validation": "OpenCascade validity, solids, volume, finite bounds, and scale checks",
    "known_limitations": [
        "No arbitrary freeform or surface-class modeling",
        "No assembly mates",
        "No general sketch constraint solver; fixed explicit-coordinate entities only",
        "No guaranteed persistent topological naming across arbitrary topology changes",
        "No arbitrary expressions or executable user code",
    ],
}


def _length_metres(value: float, unit: LengthUnit) -> float:
    result = float(value) * LENGTH_TO_METRES[unit]
    if not math.isfinite(result):
        raise CADCompilationError("NON_FINITE_VALUE", "Resolved length is not finite")
    return result


def _angle_radians(value: float, unit: AngleUnit) -> float:
    result = float(value) * ANGLE_TO_RADIANS[unit]
    if not math.isfinite(result):
        raise CADCompilationError("NON_FINITE_VALUE", "Resolved angle is not finite")
    return result


def resolve_parameters(document: EngineeringDesignDocumentV2) -> dict[str, ResolvedParameter]:
    resolved: dict[str, ResolvedParameter] = {}
    for parameter in document.parameters:
        value: float | int | bool
        if parameter.parameter_type == ParameterType.LENGTH:
            assert isinstance(parameter.unit, LengthUnit)
            value = _length_metres(float(parameter.value), parameter.unit)
        elif parameter.parameter_type == ParameterType.ANGLE:
            assert isinstance(parameter.unit, AngleUnit)
            value = _angle_radians(float(parameter.value), parameter.unit)
        elif parameter.parameter_type == ParameterType.SCALAR:
            value = float(parameter.value)
        elif parameter.parameter_type == ParameterType.INTEGER:
            value = int(parameter.value)
        else:
            value = bool(parameter.value)
        resolved[parameter.name] = ResolvedParameter(
            name=parameter.name,
            parameter_type=parameter.parameter_type,
            canonical_value=value,
            design_variable=parameter.design_variable,
        )
    return resolved


def normalized_parameter_state(document: EngineeringDesignDocumentV2) -> dict[str, Any]:
    resolved = resolve_parameters(document)
    return {
        name: {
            "type": item.parameter_type.value,
            "canonical_value": item.canonical_value,
            "canonical_unit": (
                "m" if item.parameter_type == ParameterType.LENGTH else
                "rad" if item.parameter_type == ParameterType.ANGLE else None
            ),
            "design_variable": item.design_variable,
        }
        for name, item in sorted(resolved.items())
    }


def _resolve_measure_canonical(
    value: MeasureInput,
    expected: Literal["length", "angle"],
    parameters: dict[str, ResolvedParameter],
) -> float:
    if isinstance(value, ParameterReference):
        parameter = parameters.get(value.parameter)
        required_type = ParameterType.LENGTH if expected == "length" else ParameterType.ANGLE
        if parameter is None:
            raise CADCompilationError("UNKNOWN_PARAMETER", f"Unknown parameter '{value.parameter}'")
        if parameter.parameter_type != required_type:
            raise CADCompilationError(
                "PARAMETER_DIMENSION_MISMATCH",
                f"Parameter '{value.parameter}' is not a {expected} parameter",
            )
        return float(parameter.canonical_value)
    if expected == "length":
        if not isinstance(value.unit, LengthUnit):
            raise CADCompilationError("UNIT_DIMENSION_MISMATCH", "Expected a length quantity")
        return _length_metres(value.value, value.unit)
    if not isinstance(value.unit, AngleUnit):
        raise CADCompilationError("UNIT_DIMENSION_MISMATCH", "Expected an angle quantity")
    return _angle_radians(value.value, value.unit)


def resolve_length_mm(value: MeasureInput, parameters: dict[str, ResolvedParameter]) -> float:
    return _resolve_measure_canonical(value, "length", parameters) * 1000.0


def resolve_angle_degrees(value: MeasureInput, parameters: dict[str, ResolvedParameter]) -> float:
    return math.degrees(_resolve_measure_canonical(value, "angle", parameters))


def resolve_integer(value: int | ParameterReference, parameters: dict[str, ResolvedParameter]) -> int:
    if isinstance(value, ParameterReference):
        parameter = parameters.get(value.parameter)
        if parameter is None:
            raise CADCompilationError("UNKNOWN_PARAMETER", f"Unknown parameter '{value.parameter}'")
        if parameter.parameter_type != ParameterType.INTEGER:
            raise CADCompilationError(
                "PARAMETER_DIMENSION_MISMATCH", f"Parameter '{value.parameter}' is not an integer"
            )
        return int(parameter.canonical_value)
    return int(value)


def _minimum_feature_mm(document: EngineeringDesignDocumentV2) -> float:
    return _length_metres(
        document.tolerance_policy.minimum_feature_size.value,
        document.tolerance_policy.minimum_feature_size.unit,  # type: ignore[arg-type]
    ) * 1000.0


def _positive_feature(value: float, name: str, minimum: float) -> float:
    if not math.isfinite(value) or value < minimum:
        raise CADCompilationError(
            "INVALID_DIMENSION", f"{name} must be at least the configured minimum feature size"
        )
    return value


def _point(point: Point2D, parameters: dict[str, ResolvedParameter]) -> tuple[float, float]:
    return resolve_length_mm(point.x, parameters), resolve_length_mm(point.y, parameters)


def _vector(vector: LengthVector3, parameters: dict[str, ResolvedParameter]) -> tuple[float, float, float]:
    return (
        resolve_length_mm(vector.x, parameters),
        resolve_length_mm(vector.y, parameters),
        resolve_length_mm(vector.z, parameters),
    )


def _unit_vector(value: tuple[float, float, float], name: str) -> tuple[float, float, float]:
    if any(not math.isfinite(item) for item in value):
        raise CADCompilationError("INVALID_VECTOR", f"{name} must be finite")
    magnitude = math.sqrt(sum(item * item for item in value))
    if magnitude <= 1e-15:
        raise CADCompilationError("INVALID_VECTOR", f"{name} must be non-zero")
    return tuple(item / magnitude for item in value)  # type: ignore[return-value]


def _plane_for_sketch(document: EngineeringDesignDocumentV2, plane_ref: StandardPlane | str, parameters):
    name = plane_ref.value if isinstance(plane_ref, StandardPlane) else plane_ref
    if name in {item.value for item in StandardPlane}:
        return name
    datum = next(item for item in document.datum_planes if item.datum_id == name)
    origin = _vector(datum.origin, parameters)
    x_direction = _unit_vector(datum.x_direction, "datum x_direction")
    normal = _unit_vector(datum.normal, "datum normal")
    dot = sum(a * b for a, b in zip(x_direction, normal))
    if abs(dot) > 1.0 - 1e-9:
        raise CADCompilationError("INVALID_DATUM_PLANE", "Datum x direction and normal are parallel")
    return cq.Plane(origin=origin, xDir=x_direction, normal=normal)


def _build_sketch(document, sketch, parameters) -> cq.Workplane:
    plane = _plane_for_sketch(document, sketch.plane, parameters)
    minimum = _minimum_feature_mm(document)
    wires: list[Any] = []
    loose_edges: list[Any] = []

    for entity in sketch.entities:
        if entity.construction:
            continue
        workplane = cq.Workplane(plane)
        if isinstance(entity, RectangleEntity):
            width = _positive_feature(resolve_length_mm(entity.width, parameters), "rectangle width", minimum)
            height = _positive_feature(resolve_length_mm(entity.height, parameters), "rectangle height", minimum)
            if entity.center:
                workplane = workplane.center(*_point(entity.center, parameters))
            wires.append(workplane.rect(width, height).val())
        elif isinstance(entity, CircleEntity):
            radius = _positive_feature(resolve_length_mm(entity.radius, parameters), "circle radius", minimum)
            if entity.center:
                workplane = workplane.center(*_point(entity.center, parameters))
            wires.append(workplane.circle(radius).val())
        elif isinstance(entity, PolylineEntity):
            points = [_point(item, parameters) for item in entity.points]
            if len(set(points)) < 2:
                raise CADCompilationError("INVALID_SKETCH", "Polyline points are degenerate")
            workplane = workplane.moveTo(*points[0]).polyline(points[1:])
            if entity.closed:
                workplane = workplane.close()
                wires.append(workplane.val())
            else:
                values = workplane.vals()
                loose_edges.extend(values)
        elif isinstance(entity, LineEntity):
            start, end = _point(entity.start, parameters), _point(entity.end, parameters)
            if math.dist(start, end) < minimum:
                raise CADCompilationError("INVALID_SKETCH", "Line is shorter than minimum feature size")
            loose_edges.extend(workplane.moveTo(*start).lineTo(*end).vals())
        elif isinstance(entity, ArcEntity):
            start = _point(entity.start, parameters)
            midpoint = _point(entity.midpoint, parameters)
            end = _point(entity.end, parameters)
            if len({start, midpoint, end}) < 3:
                raise CADCompilationError("INVALID_SKETCH", "Arc points must be distinct")
            arc = workplane.moveTo(*start).threePointArc(midpoint, end)
            if entity.close_to_start:
                wires.append(arc.close().val())
            else:
                loose_edges.extend(arc.vals())

    if loose_edges:
        try:
            wires.append(cq.Wire.assembleEdges(loose_edges))
        except Exception as exc:
            raise CADCompilationError(
                "UNSOLVABLE_SKETCH", "Open sketch entities do not form a connected wire"
            ) from exc
    if not wires:
        raise CADCompilationError("EMPTY_SKETCH", f"Sketch '{sketch.sketch_id}' has no profile geometry")
    try:
        return cq.Workplane(plane).newObject(wires).toPending()
    except Exception as exc:
        raise CADCompilationError("UNSOLVABLE_SKETCH", "Sketch profiles could not be constructed") from exc


def _shape_objects(workplane: cq.Workplane) -> list[Any]:
    objects = [item for item in workplane.vals() if hasattr(item, "Solids")]
    if not objects and workplane.val() is not None:
        objects = [workplane.val()]
    return objects


def _workplane_from_shapes(shapes: Iterable[Any]) -> cq.Workplane:
    return cq.Workplane("XY").newObject(list(shapes))


def _ensure_feature_geometry(workplane: cq.Workplane, feature_id: str) -> cq.Workplane:
    try:
        solids = workplane.solids().vals()
    except Exception as exc:
        raise CADCompilationError(
            "CAD_KERNEL_FAILURE", "CAD kernel could not inspect feature output", feature_id=feature_id
        ) from exc
    if not solids:
        raise CADCompilationError(
            "EMPTY_FEATURE_RESULT", "Feature produced no solid geometry", feature_id=feature_id
        )
    if any(not solid.isValid() for solid in solids):
        raise CADCompilationError(
            "INVALID_BREP", "Feature produced invalid BRep geometry", feature_id=feature_id
        )
    return workplane


def _selected_edges(workplane: cq.Workplane, selector: EdgeSelector):
    selector_map = {
        EdgeSelector.PARALLEL_X: "|X",
        EdgeSelector.PARALLEL_Y: "|Y",
        EdgeSelector.PARALLEL_Z: "|Z",
    }
    return workplane.edges(selector_map[selector]) if selector != EdgeSelector.ALL else workplane.edges()


def _combine_pattern(shapes: list[Any], combine: bool) -> cq.Workplane:
    result = _workplane_from_shapes(shapes)
    if not combine or len(shapes) <= 1:
        return result
    fused = _workplane_from_shapes([shapes[0]])
    for shape in shapes[1:]:
        fused = fused.union(_workplane_from_shapes([shape]), clean=True)
    return fused


def _execute_feature(feature, sketches, bodies, parameters, document) -> cq.Workplane:
    minimum = _minimum_feature_mm(document)
    if isinstance(feature, ExtrudeFeature):
        distance = _positive_feature(resolve_length_mm(feature.distance, parameters), "extrude distance", minimum)
        result = sketches[feature.sketch_id].extrude(distance, both=feature.symmetric)
    elif isinstance(feature, RevolveFeature):
        angle = resolve_angle_degrees(feature.angle, parameters)
        if angle <= 0 or angle > 360 + 1e-9:
            raise CADCompilationError("INVALID_ANGLE", "Revolve angle must be in (0, 360] degrees")
        axis_start = _point(feature.axis_start, parameters)
        axis_end = _point(feature.axis_end, parameters)
        if math.dist(axis_start, axis_end) < minimum:
            raise CADCompilationError("INVALID_AXIS", "Revolve axis must be non-zero")
        result = sketches[feature.sketch_id].revolve(angle, axisStart=axis_start, axisEnd=axis_end)
    elif isinstance(feature, LoftFeature):
        wires = []
        for sketch_id in feature.sketch_ids:
            wires.extend(sketches[sketch_id].vals())
        try:
            result = cq.Workplane("XY").newObject(wires).toPending().loft(ruled=feature.ruled)
        except Exception as exc:
            raise CADCompilationError("LOFT_FAILURE", "Loft profiles could not be joined") from exc
    elif isinstance(feature, UnionFeature):
        result = bodies[feature.target_body].union(bodies[feature.tool_body], clean=True)
    elif isinstance(feature, SubtractFeature):
        result = bodies[feature.target_body].cut(bodies[feature.tool_body], clean=True)
    elif isinstance(feature, IntersectionFeature):
        result = bodies[feature.target_body].intersect(bodies[feature.tool_body], clean=True)
    elif isinstance(feature, TransformFeature):
        translation = _vector(feature.translation, parameters)
        shapes = []
        for original in _shape_objects(bodies[feature.source_body]):
            transformed = original.translate(cq.Vector(*translation))
            if feature.rotation:
                axis_origin = _vector(feature.rotation.axis_origin, parameters)
                direction = _unit_vector(feature.rotation.axis_direction, "rotation axis")
                axis_end = tuple(a + b for a, b in zip(axis_origin, direction))
                angle = resolve_angle_degrees(feature.rotation.angle, parameters)
                transformed = transformed.rotate(axis_origin, axis_end, angle)
            shapes.append(transformed)
        result = _workplane_from_shapes(shapes)
    elif isinstance(feature, FilletFeature):
        radius = _positive_feature(resolve_length_mm(feature.radius, parameters), "fillet radius", minimum)
        result = _selected_edges(bodies[feature.source_body], feature.edge_selector).fillet(radius)
    elif isinstance(feature, ChamferFeature):
        distance = _positive_feature(resolve_length_mm(feature.distance, parameters), "chamfer distance", minimum)
        result = _selected_edges(bodies[feature.source_body], feature.edge_selector).chamfer(distance)
    elif isinstance(feature, LinearPatternFeature):
        count = resolve_integer(feature.count, parameters)
        if count < 1 or count > 10_000:
            raise CADCompilationError("INVALID_PATTERN", "Linear pattern count must be between 1 and 10000")
        spacing = _positive_feature(resolve_length_mm(feature.spacing, parameters), "pattern spacing", minimum)
        direction = _unit_vector(feature.direction, "linear pattern direction")
        shapes = []
        for index in range(count):
            offset = cq.Vector(*(component * spacing * index for component in direction))
            shapes.extend(shape.translate(offset) for shape in _shape_objects(bodies[feature.source_body]))
        result = _combine_pattern(shapes, feature.combine)
    elif isinstance(feature, CircularPatternFeature):
        count = resolve_integer(feature.count, parameters)
        if count < 1 or count > 10_000:
            raise CADCompilationError("INVALID_PATTERN", "Circular pattern count must be between 1 and 10000")
        total_angle = resolve_angle_degrees(feature.total_angle, parameters)
        if total_angle <= 0 or total_angle > 360 + 1e-9:
            raise CADCompilationError("INVALID_PATTERN", "Circular pattern angle must be in (0, 360] degrees")
        origin = _vector(feature.axis_origin, parameters)
        direction = _unit_vector(feature.axis_direction, "circular pattern axis")
        axis_end = tuple(a + b for a, b in zip(origin, direction))
        step = total_angle / count if math.isclose(total_angle, 360.0) else (
            total_angle / (count - 1) if count > 1 else 0.0
        )
        shapes = []
        for index in range(count):
            shapes.extend(
                shape.rotate(origin, axis_end, step * index)
                for shape in _shape_objects(bodies[feature.source_body])
            )
        result = _combine_pattern(shapes, feature.combine)
    else:  # discriminated schema makes this unreachable; it remains fail-closed.
        raise CADCompilationError("UNSUPPORTED_OPERATION", "Unsupported CAD feature operation")
    return _ensure_feature_geometry(result, feature.feature_id)


def _canonical_model(value: Any, parameters: dict[str, ResolvedParameter]) -> Any:
    if isinstance(value, ParameterReference):
        resolved = parameters[value.parameter]
        return {
            "parameter": value.parameter,
            "type": resolved.parameter_type.value,
            "canonical_value": resolved.canonical_value,
        }
    if isinstance(value, Quantity):
        if isinstance(value.unit, LengthUnit):
            return {"dimension": "length", "value": _length_metres(value.value, value.unit), "unit": "m"}
        return {"dimension": "angle", "value": _angle_radians(value.value, value.unit), "unit": "rad"}
    if isinstance(value, ParameterDefinition):
        return normalized_parameter_state_for_one(value)
    if hasattr(value, "model_fields"):
        return {
            name: _canonical_model(getattr(value, name), parameters)
            for name in value.model_fields
            if name != "operational_metadata"
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical_model(child, parameters) for key, child in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical_model(child, parameters) for child in value]
    return value


def normalized_parameter_state_for_one(parameter: ParameterDefinition) -> dict[str, Any]:
    if parameter.parameter_type == ParameterType.LENGTH:
        assert isinstance(parameter.unit, LengthUnit)
        value: Any = _length_metres(float(parameter.value), parameter.unit)
        unit = "m"
    elif parameter.parameter_type == ParameterType.ANGLE:
        assert isinstance(parameter.unit, AngleUnit)
        value = _angle_radians(float(parameter.value), parameter.unit)
        unit = "rad"
    else:
        value = parameter.value
        unit = None
    factor = (
        LENGTH_TO_METRES[parameter.unit] if isinstance(parameter.unit, LengthUnit) else
        ANGLE_TO_RADIANS[parameter.unit] if isinstance(parameter.unit, AngleUnit) else 1.0
    )
    return {
        "name": parameter.name,
        "type": parameter.parameter_type.value,
        "canonical_value": value,
        "canonical_unit": unit,
        "minimum": parameter.minimum * factor if parameter.minimum is not None else None,
        "maximum": parameter.maximum * factor if parameter.maximum is not None else None,
        "role": parameter.role,
        "description": parameter.description,
        "design_variable": parameter.design_variable,
    }


def _normalized_tolerance(document: EngineeringDesignDocumentV2) -> dict[str, Any]:
    policy = document.tolerance_policy
    return {
        "kernel_tolerance_m": _length_metres(policy.kernel_tolerance.value, policy.kernel_tolerance.unit),  # type: ignore[arg-type]
        "geometric_equality_absolute_m": _length_metres(
            policy.geometric_equality_absolute.value, policy.geometric_equality_absolute.unit  # type: ignore[arg-type]
        ),
        "geometric_equality_relative": policy.geometric_equality_relative,
        "minimum_feature_size_m": _length_metres(
            policy.minimum_feature_size.value, policy.minimum_feature_size.unit  # type: ignore[arg-type]
        ),
    }


def scientific_design_state(document: EngineeringDesignDocumentV2) -> dict[str, Any]:
    parameters = resolve_parameters(document)
    feature_by_id = {item.feature_id: item for item in document.features}
    return {
        "schema_version": document.schema_version,
        "unit_policy": document.unit_policy.model_dump(mode="json"),
        "tolerance_policy": _normalized_tolerance(document),
        "parameters": [
            normalized_parameter_state_for_one(item) for item in sorted(document.parameters, key=lambda x: x.name)
        ],
        "datum_planes": [
            _canonical_model(item, parameters) for item in sorted(document.datum_planes, key=lambda x: x.datum_id)
        ],
        "bodies": [
            _canonical_model(item, parameters) for item in sorted(document.bodies, key=lambda x: x.body_id)
        ],
        "sketches": [
            _canonical_model(item, parameters) for item in sorted(document.sketches, key=lambda x: x.sketch_id)
        ],
        "features": [
            _canonical_model(feature_by_id[item], parameters) for item in document.deterministic_feature_order()
        ],
        "output_body_ids": sorted(document.output_body_ids),
        "semantic_regions": [
            _canonical_model(item, parameters) for item in sorted(document.semantic_regions, key=lambda x: x.tag)
        ],
    }


def design_hash(document: EngineeringDesignDocumentV2) -> str:
    # Phase 1's canonical normalizer is the single scientific JSON authority.
    from app.v2.execution import digest

    return digest(scientific_design_state(document))


def validate_geometry(
    document: EngineeringDesignDocumentV2, bodies: dict[str, cq.Workplane]
) -> tuple[GeometryValidationResult, dict[str, Any]]:
    diagnostics: list[GeometryDiagnostic] = []
    signatures: list[dict[str, Any]] = []
    total_solids = 0
    minimum_mm = _minimum_feature_mm(document)
    for body_id in document.output_body_ids:
        workplane = bodies.get(body_id)
        if workplane is None:
            diagnostics.append(GeometryDiagnostic(
                code="MISSING_BODY", message="Output body was not compiled", body_id=body_id
            ))
            continue
        solids = workplane.solids().vals()
        total_solids += len(solids)
        if not solids:
            diagnostics.append(GeometryDiagnostic(
                code="NO_SOLID", message="Output body contains no solid", body_id=body_id
            ))
            continue
        body_volume = 0.0
        body_area = 0.0
        bounds: list[tuple[float, float, float, float, float, float]] = []
        for solid in solids:
            if not solid.isValid():
                diagnostics.append(GeometryDiagnostic(
                    code="INVALID_BREP", message="OpenCascade reports an invalid BRep", body_id=body_id
                ))
            volume = float(solid.Volume())
            area = float(solid.Area())
            box = solid.BoundingBox()
            values = (box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax)
            if not all(math.isfinite(item) for item in (*values, volume, area)):
                diagnostics.append(GeometryDiagnostic(
                    code="NON_FINITE_GEOMETRY", message="Geometry metrics are not finite", body_id=body_id
                ))
                continue
            if volume <= 0:
                diagnostics.append(GeometryDiagnostic(
                    code="NON_POSITIVE_VOLUME", message="Expected solid has non-positive volume", body_id=body_id
                ))
            if min(box.xlen, box.ylen, box.zlen) < minimum_mm:
                diagnostics.append(GeometryDiagnostic(
                    code="DEGENERATE_SCALE", message="Body extent is below minimum feature size", body_id=body_id
                ))
            body_volume += volume
            body_area += area
            bounds.append(values)
        signatures.append({
            "body_id": body_id,
            "solid_count": len(solids),
            "volume_m3": body_volume * 1e-9,
            "surface_area_m2": body_area * 1e-6,
            "bounding_boxes_m": [[coordinate * 1e-3 for coordinate in item] for item in bounds],
        })
    result = GeometryValidationResult(
        status=ValidationStatus.INVALID if diagnostics else ValidationStatus.VALID,
        diagnostics=diagnostics,
        body_count=len(document.output_body_ids),
        solid_count=total_solids,
    )
    signature = {
        "schema_version": "geometry-signature-1.0",
        "kernel_length_unit": "mm",
        "bodies": sorted(signatures, key=lambda item: item["body_id"]),
    }
    return result, signature


def compile_design(document: EngineeringDesignDocumentV2) -> CompiledDesign:
    parameters = resolve_parameters(document)
    sketches = {
        sketch.sketch_id: _build_sketch(document, sketch, parameters) for sketch in document.sketches
    }
    feature_by_id = {item.feature_id: item for item in document.features}
    order = document.deterministic_feature_order()
    bodies: dict[str, cq.Workplane] = {}
    for feature_id in order:
        feature = feature_by_id[feature_id]
        try:
            bodies[feature.output_body] = _execute_feature(
                feature, sketches, bodies, parameters, document
            )
        except CADCompilationError as exc:
            if exc.feature_id is None:
                exc.feature_id = feature_id
            raise
        except Exception as exc:
            raise CADCompilationError(
                "CAD_KERNEL_FAILURE",
                f"Feature '{feature_id}' could not be constructed",
                feature_id=feature_id,
            ) from exc
    validation, signature = validate_geometry(document, bodies)
    if validation.status == ValidationStatus.INVALID:
        raise CADCompilationError(
            "INVALID_GEOMETRY", "Compiled geometry failed validation", validation=validation
        )
    from app.v2.execution import digest

    return CompiledDesign(
        document=document,
        bodies=bodies,
        design_hash=design_hash(document),
        geometry_fingerprint=digest(signature),
        feature_order=order,
        normalized_parameters=normalized_parameter_state(document),
        validation=validation,
        geometry_signature=signature,
        semantic_regions=[item.model_dump(mode="json") for item in document.semantic_regions],
    )


def _compound_for_outputs(compiled: CompiledDesign):
    shapes: list[Any] = []
    for body_id in compiled.document.output_body_ids:
        shapes.extend(_shape_objects(compiled.bodies[body_id]))
    if not shapes:
        raise CADCompilationError("EMPTY_EXPORT", "No validated geometry is available for export")
    return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_compiled_design(
    compiled: CompiledDesign, directory: Path | None = None
) -> list[ExportedArtifact]:
    if compiled.validation.status == ValidationStatus.INVALID:
        raise CADCompilationError("INVALID_EXPORT", "Invalid geometry cannot be exported")
    export_dir = directory or Path(tempfile.gettempdir()) / "asre_lab_v2_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    shape = _compound_for_outputs(compiled)
    artifacts: list[ExportedArtifact] = []
    for file_format, media_type, coordinate_unit in (
        ("step", "model/step", "mm"),
        ("stl", "model/stl", "unitless; coordinates are mm"),
    ):
        artifact_id = str(uuid.uuid4())
        path = export_dir / f"{artifact_id}.{file_format}"
        try:
            cq.exporters.export(shape, str(path))
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise CADCompilationError(
                "EXPORT_FAILURE", f"Validated geometry could not be exported as {file_format.upper()}"
            ) from exc
        if not path.is_file() or path.stat().st_size <= 0:
            path.unlink(missing_ok=True)
            raise CADCompilationError("EXPORT_FAILURE", f"{file_format.upper()} export is empty")
        artifacts.append(ExportedArtifact(
            metadata=ArtifactMetadata(
                artifact_id=artifact_id,
                file_format=file_format,  # type: ignore[arg-type]
                checksum_sha256=_checksum(path),
                byte_size=path.stat().st_size,
                media_type=media_type,
                coordinate_unit=coordinate_unit,
            ),
            path=path,
        ))
    compiled.artifacts = artifacts
    return artifacts
