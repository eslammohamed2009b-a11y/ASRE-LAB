"""Typed, unit-explicit engineering design document for the general CAD path.

The V2 document is an authored feature graph, not a catalogue of named object
families.  Every executable operation is represented by a bounded Pydantic
model; no user-provided code or expression is ever evaluated.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LengthUnit(str, Enum):
    METRE = "m"
    MILLIMETRE = "mm"
    CENTIMETRE = "cm"
    MICROMETRE = "um"
    INCH = "in"


class AngleUnit(str, Enum):
    DEGREE = "deg"
    RADIAN = "rad"


class Quantity(StrictModel):
    value: float
    unit: LengthUnit | AngleUnit

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Quantity values must be finite")
        return value


class ParameterReference(StrictModel):
    parameter: str = Field(pattern=IDENTIFIER.pattern)


MeasureInput = Quantity | ParameterReference
ScalarInput = float | int | ParameterReference
IntegerInput = int | ParameterReference


class ParameterType(str, Enum):
    LENGTH = "length"
    ANGLE = "angle"
    SCALAR = "scalar"
    INTEGER = "integer"
    BOOLEAN = "boolean"


class ParameterDefinition(StrictModel):
    name: str = Field(pattern=IDENTIFIER.pattern)
    parameter_type: ParameterType
    value: float | int | bool
    unit: LengthUnit | AngleUnit | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    role: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    design_variable: bool = False

    @model_validator(mode="after")
    def validate_dimension(self) -> "ParameterDefinition":
        if self.parameter_type == ParameterType.LENGTH:
            if not isinstance(self.unit, LengthUnit):
                raise ValueError("Length parameters require a supported length unit")
            if isinstance(self.value, bool):
                raise ValueError("Length parameters require a numeric value")
        elif self.parameter_type == ParameterType.ANGLE:
            if not isinstance(self.unit, AngleUnit):
                raise ValueError("Angle parameters require a supported angle unit")
            if isinstance(self.value, bool):
                raise ValueError("Angle parameters require a numeric value")
        elif self.unit is not None:
            raise ValueError(f"{self.parameter_type.value} parameters must not declare a unit")

        if self.parameter_type == ParameterType.BOOLEAN:
            if type(self.value) is not bool:
                raise ValueError("Boolean parameters require a boolean value")
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("Boolean parameters cannot declare numeric bounds")
        elif self.parameter_type == ParameterType.INTEGER:
            if type(self.value) is not int:
                raise ValueError("Integer parameters require an integer value")
        elif isinstance(self.value, bool) or not math.isfinite(float(self.value)):
            raise ValueError("Numeric parameter values must be finite")

        numeric = None if isinstance(self.value, bool) else float(self.value)
        if self.minimum is not None and numeric is not None and numeric < self.minimum:
            raise ValueError(f"Parameter '{self.name}' is below its minimum")
        if self.maximum is not None and numeric is not None and numeric > self.maximum:
            raise ValueError(f"Parameter '{self.name}' is above its maximum")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Parameter minimum cannot exceed maximum")
        return self


class LengthVector3(StrictModel):
    x: MeasureInput = Field(default_factory=lambda: Quantity(value=0, unit=LengthUnit.MILLIMETRE))
    y: MeasureInput = Field(default_factory=lambda: Quantity(value=0, unit=LengthUnit.MILLIMETRE))
    z: MeasureInput = Field(default_factory=lambda: Quantity(value=0, unit=LengthUnit.MILLIMETRE))


class Point2D(StrictModel):
    x: MeasureInput
    y: MeasureInput


class StandardPlane(str, Enum):
    XY = "XY"
    XZ = "XZ"
    YZ = "YZ"


class DatumPlane(StrictModel):
    datum_id: str = Field(pattern=IDENTIFIER.pattern)
    origin: LengthVector3 = Field(default_factory=LengthVector3)
    x_direction: tuple[float, float, float] = (1.0, 0.0, 0.0)
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0)


class RectangleEntity(StrictModel):
    entity_type: Literal["rectangle"] = "rectangle"
    entity_id: str = Field(pattern=IDENTIFIER.pattern)
    width: MeasureInput
    height: MeasureInput
    center: Point2D | None = None
    construction: bool = False


class CircleEntity(StrictModel):
    entity_type: Literal["circle"] = "circle"
    entity_id: str = Field(pattern=IDENTIFIER.pattern)
    radius: MeasureInput
    center: Point2D | None = None
    construction: bool = False


class LineEntity(StrictModel):
    entity_type: Literal["line"] = "line"
    entity_id: str = Field(pattern=IDENTIFIER.pattern)
    start: Point2D
    end: Point2D
    construction: bool = False


class PolylineEntity(StrictModel):
    entity_type: Literal["polyline"] = "polyline"
    entity_id: str = Field(pattern=IDENTIFIER.pattern)
    points: list[Point2D] = Field(min_length=2, max_length=500)
    closed: bool = True
    construction: bool = False


class ArcEntity(StrictModel):
    entity_type: Literal["arc"] = "arc"
    entity_id: str = Field(pattern=IDENTIFIER.pattern)
    start: Point2D
    midpoint: Point2D
    end: Point2D
    close_to_start: bool = False
    construction: bool = False


SketchEntity = Annotated[
    Union[RectangleEntity, CircleEntity, LineEntity, PolylineEntity, ArcEntity],
    Field(discriminator="entity_type"),
]


class FixedConstraint(StrictModel):
    """Truthful bounded constraint support: coordinates are already explicit.

    A fixed constraint records that an entity is fully defined by its authored
    coordinates.  V2 does not claim a general geometric constraint solver.
    """

    constraint_type: Literal["fixed"] = "fixed"
    constraint_id: str = Field(pattern=IDENTIFIER.pattern)
    entity_id: str = Field(pattern=IDENTIFIER.pattern)


class SketchDefinition(StrictModel):
    sketch_id: str = Field(pattern=IDENTIFIER.pattern)
    plane: StandardPlane | str = StandardPlane.XY
    unit: LengthUnit = LengthUnit.MILLIMETRE
    entities: list[SketchEntity] = Field(min_length=1, max_length=1000)
    constraints: list[FixedConstraint] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_entities(self) -> "SketchDefinition":
        entity_ids = [item.entity_id for item in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError(f"Sketch '{self.sketch_id}' contains duplicate entity IDs")
        known = set(entity_ids)
        constraint_ids = [item.constraint_id for item in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError(f"Sketch '{self.sketch_id}' contains duplicate constraint IDs")
        if any(item.entity_id not in known for item in self.constraints):
            raise ValueError(f"Sketch '{self.sketch_id}' constraint references an unknown entity")
        return self


class BodyDefinition(StrictModel):
    body_id: str = Field(pattern=IDENTIFIER.pattern)
    name: str | None = Field(default=None, max_length=200)
    component_id: str | None = Field(default=None, pattern=IDENTIFIER.pattern)
    material: str | None = Field(default=None, max_length=100)


class FeatureBase(StrictModel):
    feature_id: str = Field(pattern=IDENTIFIER.pattern)
    dependencies: list[str] = Field(default_factory=list, max_length=1000)
    semantic_tags: list[str] = Field(default_factory=list, max_length=100)


class ExtrudeFeature(FeatureBase):
    operation: Literal["extrude"] = "extrude"
    sketch_id: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    distance: MeasureInput
    symmetric: bool = False


class RevolveFeature(FeatureBase):
    operation: Literal["revolve"] = "revolve"
    sketch_id: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    angle: MeasureInput = Field(default_factory=lambda: Quantity(value=360, unit=AngleUnit.DEGREE))
    axis_start: Point2D
    axis_end: Point2D


class LoftFeature(FeatureBase):
    operation: Literal["loft"] = "loft"
    sketch_ids: list[str] = Field(min_length=2, max_length=100)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    ruled: bool = False


class BooleanBase(FeatureBase):
    target_body: str = Field(pattern=IDENTIFIER.pattern)
    tool_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)


class UnionFeature(BooleanBase):
    operation: Literal["union"] = "union"


class SubtractFeature(BooleanBase):
    operation: Literal["subtract"] = "subtract"


class IntersectionFeature(BooleanBase):
    operation: Literal["intersection"] = "intersection"


class RotationSpec(StrictModel):
    axis_origin: LengthVector3 = Field(default_factory=LengthVector3)
    axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
    angle: MeasureInput


class TransformFeature(FeatureBase):
    operation: Literal["transform"] = "transform"
    source_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    translation: LengthVector3 = Field(default_factory=LengthVector3)
    rotation: RotationSpec | None = None


class EdgeSelector(str, Enum):
    ALL = "all"
    PARALLEL_X = "parallel_x"
    PARALLEL_Y = "parallel_y"
    PARALLEL_Z = "parallel_z"


class FilletFeature(FeatureBase):
    operation: Literal["fillet"] = "fillet"
    source_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    radius: MeasureInput
    edge_selector: EdgeSelector = EdgeSelector.ALL


class ChamferFeature(FeatureBase):
    operation: Literal["chamfer"] = "chamfer"
    source_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    distance: MeasureInput
    edge_selector: EdgeSelector = EdgeSelector.ALL


class LinearPatternFeature(FeatureBase):
    operation: Literal["linear_pattern"] = "linear_pattern"
    source_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    direction: tuple[float, float, float]
    spacing: MeasureInput
    count: IntegerInput = Field(union_mode="left_to_right")
    combine: bool = False


class CircularPatternFeature(FeatureBase):
    operation: Literal["circular_pattern"] = "circular_pattern"
    source_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    axis_origin: LengthVector3 = Field(default_factory=LengthVector3)
    axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
    count: IntegerInput = Field(union_mode="left_to_right")
    total_angle: MeasureInput = Field(default_factory=lambda: Quantity(value=360, unit=AngleUnit.DEGREE))
    combine: bool = False


Feature = Annotated[
    Union[
        ExtrudeFeature,
        RevolveFeature,
        LoftFeature,
        UnionFeature,
        SubtractFeature,
        IntersectionFeature,
        TransformFeature,
        FilletFeature,
        ChamferFeature,
        LinearPatternFeature,
        CircularPatternFeature,
    ],
    Field(discriminator="operation"),
]


class SemanticSelector(str, Enum):
    ALL_FACES = "all_faces"
    END_FACES = "end_faces"
    SIDE_FACES = "side_faces"
    ALL_EDGES = "all_edges"


class SemanticRegion(StrictModel):
    tag: str = Field(pattern=IDENTIFIER.pattern)
    body_id: str = Field(pattern=IDENTIFIER.pattern)
    source_feature_id: str | None = Field(default=None, pattern=IDENTIFIER.pattern)
    selector: SemanticSelector
    description: str | None = Field(default=None, max_length=500)


class TolerancePolicy(StrictModel):
    kernel_tolerance: Quantity = Field(
        default_factory=lambda: Quantity(value=1e-6, unit=LengthUnit.MILLIMETRE)
    )
    geometric_equality_absolute: Quantity = Field(
        default_factory=lambda: Quantity(value=1e-6, unit=LengthUnit.MILLIMETRE)
    )
    geometric_equality_relative: float = Field(default=1e-9, gt=0, le=1e-3)
    minimum_feature_size: Quantity = Field(
        default_factory=lambda: Quantity(value=1e-5, unit=LengthUnit.MILLIMETRE)
    )

    @model_validator(mode="after")
    def validate_length_units(self) -> "TolerancePolicy":
        to_metres = {
            LengthUnit.METRE: 1.0,
            LengthUnit.MILLIMETRE: 1e-3,
            LengthUnit.CENTIMETRE: 1e-2,
            LengthUnit.MICROMETRE: 1e-6,
            LengthUnit.INCH: 0.0254,
        }
        for name in ("kernel_tolerance", "geometric_equality_absolute", "minimum_feature_size"):
            if not isinstance(getattr(self, name).unit, LengthUnit):
                raise ValueError(f"{name} must be a length quantity")
            if getattr(self, name).value <= 0:
                raise ValueError(f"{name} must be positive")
        kernel = self.kernel_tolerance.value * to_metres[self.kernel_tolerance.unit]  # type: ignore[index]
        equality = (
            self.geometric_equality_absolute.value
            * to_metres[self.geometric_equality_absolute.unit]  # type: ignore[index]
        )
        minimum = self.minimum_feature_size.value * to_metres[self.minimum_feature_size.unit]  # type: ignore[index]
        if equality < kernel:
            raise ValueError("Geometric equality tolerance cannot be tighter than kernel tolerance")
        if minimum < kernel * 10:
            raise ValueError("Minimum feature size must be at least 10x the kernel tolerance")
        return self


class UnitPolicy(StrictModel):
    document_values: Literal["explicit_per_value"] = "explicit_per_value"
    canonical_length: Literal["m"] = "m"
    canonical_angle: Literal["rad"] = "rad"
    kernel_length: Literal["mm"] = "mm"
    step_length: Literal["mm"] = "mm"
    preview_coordinates: Literal["mm"] = "mm"
    stl_unit_note: Literal["STL is unitless; coordinates are millimetres"] = (
        "STL is unitless; coordinates are millimetres"
    )


def _feature_input_bodies(feature: Feature) -> list[str]:
    if isinstance(feature, BooleanBase):
        return [feature.target_body, feature.tool_body]
    if isinstance(
        feature,
        (TransformFeature, FilletFeature, ChamferFeature, LinearPatternFeature, CircularPatternFeature),
    ):
        return [feature.source_body]
    return []


def _feature_output_body(feature: Feature) -> str:
    return feature.output_body


def _walk_parameter_references(value: Any) -> list[str]:
    if isinstance(value, ParameterReference):
        return [value.parameter]
    if isinstance(value, BaseModel):
        found: list[str] = []
        for name in value.model_fields:
            found.extend(_walk_parameter_references(getattr(value, name)))
        return found
    if isinstance(value, (list, tuple)):
        return [item for child in value for item in _walk_parameter_references(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _walk_parameter_references(child)]
    return []


class EngineeringDesignDocumentV2(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    document_id: str = Field(pattern=IDENTIFIER.pattern)
    unit_policy: UnitPolicy = Field(default_factory=UnitPolicy)
    tolerance_policy: TolerancePolicy = Field(default_factory=TolerancePolicy)
    parameters: list[ParameterDefinition] = Field(default_factory=list, max_length=1000)
    datum_planes: list[DatumPlane] = Field(default_factory=list, max_length=1000)
    bodies: list[BodyDefinition] = Field(min_length=1, max_length=1000)
    sketches: list[SketchDefinition] = Field(min_length=1, max_length=1000)
    features: list[Feature] = Field(min_length=1, max_length=5000)
    output_body_ids: list[str] = Field(min_length=1, max_length=1000)
    semantic_regions: list[SemanticRegion] = Field(default_factory=list, max_length=1000)
    operational_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_document_graph(self) -> "EngineeringDesignDocumentV2":
        named_lists = {
            "parameter": [item.name for item in self.parameters],
            "datum plane": [item.datum_id for item in self.datum_planes],
            "body": [item.body_id for item in self.bodies],
            "sketch": [item.sketch_id for item in self.sketches],
            "feature": [item.feature_id for item in self.features],
            "semantic tag": [item.tag for item in self.semantic_regions],
        }
        for label, identifiers in named_lists.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"Duplicate {label} ID")

        parameter_names = set(named_lists["parameter"])
        unknown_parameters = set(_walk_parameter_references([
            self.tolerance_policy, self.datum_planes, self.sketches, self.features
        ])) - parameter_names
        if unknown_parameters:
            raise ValueError(f"Unknown parameter reference(s): {sorted(unknown_parameters)}")

        body_ids = set(named_lists["body"])
        sketch_ids = set(named_lists["sketch"])
        datum_ids = set(named_lists["datum plane"])
        feature_by_id = {item.feature_id: item for item in self.features}

        for sketch in self.sketches:
            plane = sketch.plane.value if isinstance(sketch.plane, StandardPlane) else sketch.plane
            if plane not in {item.value for item in StandardPlane} | datum_ids:
                raise ValueError(f"Sketch '{sketch.sketch_id}' references unknown plane '{plane}'")

        producers: dict[str, str] = {}
        for feature in self.features:
            if feature.output_body not in body_ids:
                raise ValueError(
                    f"Feature '{feature.feature_id}' has unknown output body '{feature.output_body}'"
                )
            if feature.output_body in producers:
                raise ValueError(
                    f"Body '{feature.output_body}' has multiple producers; V2 bodies are immutable feature outputs"
                )
            producers[feature.output_body] = feature.feature_id
            if isinstance(feature, (ExtrudeFeature, RevolveFeature)) and feature.sketch_id not in sketch_ids:
                raise ValueError(f"Feature '{feature.feature_id}' references unknown sketch '{feature.sketch_id}'")
            if isinstance(feature, LoftFeature):
                unknown_sketches = set(feature.sketch_ids) - sketch_ids
                if unknown_sketches:
                    raise ValueError(
                        f"Feature '{feature.feature_id}' references unknown sketch(es): {sorted(unknown_sketches)}"
                    )

        unknown_outputs = set(self.output_body_ids) - body_ids
        if unknown_outputs:
            raise ValueError(f"Unknown output body reference(s): {sorted(unknown_outputs)}")
        unproduced_outputs = set(self.output_body_ids) - set(producers)
        if unproduced_outputs:
            raise ValueError(f"Output body has no producing feature: {sorted(unproduced_outputs)}")

        dependencies: dict[str, set[str]] = {}
        for feature in self.features:
            explicit = set(feature.dependencies)
            unknown = explicit - set(feature_by_id)
            if unknown:
                raise ValueError(
                    f"Feature '{feature.feature_id}' has unknown dependency reference(s): {sorted(unknown)}"
                )
            if feature.feature_id in explicit:
                raise ValueError(f"Feature '{feature.feature_id}' cannot depend on itself")
            inferred: set[str] = set()
            for body_id in _feature_input_bodies(feature):
                if body_id not in body_ids:
                    raise ValueError(
                        f"Feature '{feature.feature_id}' references unknown body '{body_id}'"
                    )
                producer = producers.get(body_id)
                if producer is None:
                    raise ValueError(
                        f"Feature '{feature.feature_id}' references unproduced body '{body_id}'"
                    )
                inferred.add(producer)
            dependencies[feature.feature_id] = explicit | inferred

        indegree = {feature_id: len(items) for feature_id, items in dependencies.items()}
        dependants: dict[str, set[str]] = defaultdict(set)
        for feature_id, items in dependencies.items():
            for dependency in items:
                dependants[dependency].add(feature_id)
        ready = sorted(item for item, degree in indegree.items() if degree == 0)
        visited: list[str] = []
        while ready:
            current = ready.pop(0)
            visited.append(current)
            for dependant in sorted(dependants[current]):
                indegree[dependant] -= 1
                if indegree[dependant] == 0:
                    ready.append(dependant)
                    ready.sort()
        if len(visited) != len(self.features):
            cyclic = sorted(item for item, degree in indegree.items() if degree > 0)
            raise ValueError(f"Feature dependency cycle detected: {cyclic}")

        for region in self.semantic_regions:
            if region.body_id not in body_ids:
                raise ValueError(f"Semantic region '{region.tag}' references unknown body")
            if region.source_feature_id and region.source_feature_id not in feature_by_id:
                raise ValueError(f"Semantic region '{region.tag}' references unknown feature")
        return self

    def deterministic_feature_order(self) -> list[str]:
        """Return a stable topological order including inferred body dependencies."""
        producer = {item.output_body: item.feature_id for item in self.features}
        dependencies = {
            item.feature_id: set(item.dependencies)
            | {producer[body] for body in _feature_input_bodies(item)}
            for item in self.features
        }
        remaining = {key: set(value) for key, value in dependencies.items()}
        ordered: list[str] = []
        while remaining:
            ready = sorted(key for key, value in remaining.items() if not value)
            if not ready:  # guarded by model validation
                raise ValueError("Feature dependency cycle detected")
            for feature_id in ready:
                ordered.append(feature_id)
                remaining.pop(feature_id)
            for value in remaining.values():
                value.difference_update(ready)
        return ordered


class ValidationStatus(str, Enum):
    VALID = "VALID"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    INVALID = "INVALID"


class GeometryDiagnostic(StrictModel):
    code: str
    message: str
    body_id: str | None = None
    feature_id: str | None = None


class GeometryValidationResult(StrictModel):
    status: ValidationStatus
    diagnostics: list[GeometryDiagnostic] = Field(default_factory=list)
    body_count: int
    solid_count: int


class ArtifactMetadata(StrictModel):
    artifact_id: str
    file_format: Literal["step", "stl"]
    checksum_sha256: str
    byte_size: int
    media_type: str
    coordinate_unit: str


class CompileMetadata(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    design_hash: str
    geometry_fingerprint: str
    feature_order: list[str]
    normalized_parameters: dict[str, Any]
    unit_policy: UnitPolicy
    tolerance_policy: dict[str, Any]
    validation: GeometryValidationResult
    artifacts: list[ArtifactMetadata] = Field(default_factory=list)


class DesignV2ValidationResponse(StrictModel):
    valid: bool
    design_hash: str
    feature_order: list[str]
    normalized_parameters: dict[str, Any]


class DesignV2CompileResponse(StrictModel):
    design_id: str
    experiment_id: str
    metadata: CompileMetadata
