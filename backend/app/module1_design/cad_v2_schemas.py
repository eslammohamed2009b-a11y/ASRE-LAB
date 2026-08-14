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
    CATEGORICAL = "categorical"


class ParameterDefinition(StrictModel):
    name: str = Field(pattern=IDENTIFIER.pattern)
    parameter_type: ParameterType
    value: float | int | bool | str
    unit: LengthUnit | AngleUnit | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    role: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    design_variable: bool = False
    choices: list[str] = Field(default_factory=list, max_length=1000)

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
        elif self.parameter_type == ParameterType.CATEGORICAL:
            if not isinstance(self.value, str):
                raise ValueError("Categorical parameters require a string value")
            if not self.choices or self.value not in self.choices:
                raise ValueError("Categorical parameter value must be present in choices")
            if len(self.choices) != len(set(self.choices)):
                raise ValueError("Categorical parameter choices must be unique")
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("Categorical parameters cannot declare numeric bounds")
        elif self.parameter_type == ParameterType.INTEGER:
            if type(self.value) is not int:
                raise ValueError("Integer parameters require an integer value")
        elif isinstance(self.value, bool) or not math.isfinite(float(self.value)):
            raise ValueError("Numeric parameter values must be finite")

        numeric = None if isinstance(self.value, (bool, str)) else float(self.value)
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


class CoincidentConstraint(StrictModel):
    constraint_type: Literal["coincident"] = "coincident"
    constraint_id: str = Field(pattern=IDENTIFIER.pattern)
    first_entity_id: str = Field(pattern=IDENTIFIER.pattern)
    second_entity_id: str = Field(pattern=IDENTIFIER.pattern)


class OrientationConstraint(StrictModel):
    constraint_type: Literal["horizontal", "vertical"]
    constraint_id: str = Field(pattern=IDENTIFIER.pattern)
    entity_id: str = Field(pattern=IDENTIFIER.pattern)


class BinaryGeometryConstraint(StrictModel):
    constraint_type: Literal["parallel", "perpendicular", "equal"]
    constraint_id: str = Field(pattern=IDENTIFIER.pattern)
    first_entity_id: str = Field(pattern=IDENTIFIER.pattern)
    second_entity_id: str = Field(pattern=IDENTIFIER.pattern)


class DistanceConstraint(StrictModel):
    constraint_type: Literal["distance", "horizontal_distance", "vertical_distance"]
    constraint_id: str = Field(pattern=IDENTIFIER.pattern)
    first_entity_id: str = Field(pattern=IDENTIFIER.pattern)
    second_entity_id: str = Field(pattern=IDENTIFIER.pattern)
    value: MeasureInput
    first_position: float | None = Field(default=None, ge=0, le=1)
    second_position: float | None = Field(default=None, ge=0, le=1)


class EntityDimensionConstraint(StrictModel):
    constraint_type: Literal["length", "radius", "diameter"]
    constraint_id: str = Field(pattern=IDENTIFIER.pattern)
    entity_id: str = Field(pattern=IDENTIFIER.pattern)
    value: MeasureInput


class AngleConstraint(StrictModel):
    constraint_type: Literal["angle", "tangent"]
    constraint_id: str = Field(pattern=IDENTIFIER.pattern)
    first_entity_id: str = Field(pattern=IDENTIFIER.pattern)
    second_entity_id: str = Field(pattern=IDENTIFIER.pattern)
    value: MeasureInput | None = None


SketchConstraint = Annotated[
    Union[
        FixedConstraint,
        CoincidentConstraint,
        OrientationConstraint,
        BinaryGeometryConstraint,
        DistanceConstraint,
        EntityDimensionConstraint,
        AngleConstraint,
    ],
    Field(discriminator="constraint_type"),
]


class SketchSolveState(str, Enum):
    FULLY_CONSTRAINED = "FULLY_CONSTRAINED"
    UNDERCONSTRAINED = "UNDERCONSTRAINED"
    OVERCONSTRAINED = "OVERCONSTRAINED"
    INVALID = "INVALID"


class SketchSolveResult(StrictModel):
    sketch_id: str
    state: SketchSolveState
    residual: float
    degrees_of_freedom: int
    diagnostics: list[str] = Field(default_factory=list)


class SketchDefinition(StrictModel):
    sketch_id: str = Field(pattern=IDENTIFIER.pattern)
    plane: StandardPlane | str = StandardPlane.XY
    unit: LengthUnit = LengthUnit.MILLIMETRE
    entities: list[SketchEntity] = Field(min_length=1, max_length=1000)
    constraints: list[SketchConstraint] = Field(default_factory=list, max_length=1000)
    constraint_mode: Literal["explicit_coordinates", "constraint_driven"] = "explicit_coordinates"

    @model_validator(mode="after")
    def validate_entities(self) -> "SketchDefinition":
        entity_ids = [item.entity_id for item in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError(f"Sketch '{self.sketch_id}' contains duplicate entity IDs")
        known = set(entity_ids)
        constraint_ids = [item.constraint_id for item in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError(f"Sketch '{self.sketch_id}' contains duplicate constraint IDs")
        referenced: list[str] = []
        for item in self.constraints:
            referenced.extend(
                getattr(item, field_name)
                for field_name in ("entity_id", "first_entity_id", "second_entity_id")
                if hasattr(item, field_name)
            )
        if any(item not in known for item in referenced):
            raise ValueError(f"Sketch '{self.sketch_id}' constraint references an unknown entity")
        return self


class BodyDefinition(StrictModel):
    body_id: str = Field(pattern=IDENTIFIER.pattern)
    name: str | None = Field(default=None, max_length=200)
    component_id: str | None = Field(default=None, pattern=IDENTIFIER.pattern)
    material: str | None = Field(default=None, max_length=100)
    semantic_tags: list[str] = Field(default_factory=list, max_length=100)


class PathDefinition(StrictModel):
    path_id: str = Field(pattern=IDENTIFIER.pattern)
    points: list[LengthVector3] = Field(min_length=2, max_length=1000)
    closed: bool = False


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
    taper_angle: MeasureInput | None = None


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
    make_solid: bool = True
    transition: Literal["right", "round", "transformed"] = "right"


class SweepFeature(FeatureBase):
    operation: Literal["sweep"] = "sweep"
    sketch_ids: list[str] = Field(min_length=1, max_length=100)
    path_id: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    make_solid: bool = True
    is_frenet: bool = False
    transition: Literal["right", "round", "transformed"] = "right"


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


class SplitFeature(BooleanBase):
    operation: Literal["split"] = "split"
    keep: Literal["outside", "inside"] = "outside"


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


class MirrorFeature(FeatureBase):
    operation: Literal["mirror"] = "mirror"
    source_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    plane: StandardPlane | str = StandardPlane.YZ
    union: bool = False


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


class ShellFeature(FeatureBase):
    operation: Literal["shell"] = "shell"
    source_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    thickness: MeasureInput
    remove_faces: Literal["max_x", "min_x", "max_y", "min_y", "max_z", "min_z"]
    kind: Literal["arc", "intersection"] = "arc"
    inward: bool = True


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


class GridPatternFeature(FeatureBase):
    operation: Literal["grid_pattern"] = "grid_pattern"
    source_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    x_direction: tuple[float, float, float] = (1.0, 0.0, 0.0)
    y_direction: tuple[float, float, float] = (0.0, 1.0, 0.0)
    x_spacing: MeasureInput
    y_spacing: MeasureInput
    x_count: IntegerInput = Field(union_mode="left_to_right")
    y_count: IntegerInput = Field(union_mode="left_to_right")
    combine: bool = False


class HoleFeature(FeatureBase):
    operation: Literal["hole"] = "hole"
    source_body: str = Field(pattern=IDENTIFIER.pattern)
    output_body: str = Field(pattern=IDENTIFIER.pattern)
    center: LengthVector3 = Field(default_factory=LengthVector3)
    axis_direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
    hole_type: Literal["simple", "through", "blind", "counterbore", "countersink"] = "through"
    diameter: MeasureInput
    depth: MeasureInput | None = None
    counterbore_diameter: MeasureInput | None = None
    counterbore_depth: MeasureInput | None = None
    countersink_diameter: MeasureInput | None = None
    countersink_angle: MeasureInput | None = None


Feature = Annotated[
    Union[
        ExtrudeFeature,
        RevolveFeature,
        LoftFeature,
        SweepFeature,
        UnionFeature,
        SubtractFeature,
        IntersectionFeature,
        SplitFeature,
        TransformFeature,
        MirrorFeature,
        FilletFeature,
        ChamferFeature,
        ShellFeature,
        LinearPatternFeature,
        CircularPatternFeature,
        GridPatternFeature,
        HoleFeature,
    ],
    Field(discriminator="operation"),
]


class SemanticSelector(str, Enum):
    ALL_FACES = "all_faces"
    END_FACES = "end_faces"
    SIDE_FACES = "side_faces"
    ALL_EDGES = "all_edges"


class ExtremeFaceSelector(StrictModel):
    selector_type: Literal["extreme_face"] = "extreme_face"
    axis: Literal["x", "y", "z"]
    extreme: Literal["minimum", "maximum"]
    surface_type: Literal["any", "plane", "cylinder"] = "plane"


class NormalFaceSelector(StrictModel):
    selector_type: Literal["normal_face"] = "normal_face"
    direction: tuple[float, float, float]
    largest: bool = True


class CylindricalFaceSelector(StrictModel):
    selector_type: Literal["cylindrical_radius"] = "cylindrical_radius"
    radius: MeasureInput
    radius_tolerance: Quantity = Field(
        default_factory=lambda: Quantity(value=1e-4, unit=LengthUnit.MILLIMETRE)
    )
    allow_multiple: bool = False


class GeometryTypeSelector(StrictModel):
    selector_type: Literal["geometry_type"] = "geometry_type"
    topology: Literal["face", "edge"]
    geometry_type: Literal["plane", "cylinder", "cone", "sphere", "torus", "line", "circle"]
    allow_multiple: bool = False


AdvancedTopologySelector = Annotated[
    Union[ExtremeFaceSelector, NormalFaceSelector, CylindricalFaceSelector, GeometryTypeSelector],
    Field(discriminator="selector_type"),
]


class SemanticIdentityStatus(str, Enum):
    EXACT = "EXACT"
    DERIVED = "DERIVED"
    RESELECTED = "RESELECTED"
    LOST = "LOST"


class SemanticRegion(StrictModel):
    tag: str = Field(pattern=IDENTIFIER.pattern)
    body_id: str = Field(pattern=IDENTIFIER.pattern)
    source_feature_id: str | None = Field(default=None, pattern=IDENTIFIER.pattern)
    selector: SemanticSelector | AdvancedTopologySelector
    description: str | None = Field(default=None, max_length=500)


class ResolvedSemanticRegion(StrictModel):
    tag: str
    body_id: str
    status: SemanticIdentityStatus
    topology_kind: Literal["face", "edge"]
    topology_signatures: list[str]
    diagnostic: str | None = None


class EngineeringInterface(StrictModel):
    interface_id: str = Field(pattern=IDENTIFIER.pattern)
    region_tag: str = Field(pattern=IDENTIFIER.pattern)
    role: Literal[
        "structural_support_candidate",
        "thermal_boundary_candidate",
        "flow_inlet_candidate",
        "flow_outlet_candidate",
        "contact_interface",
        "symmetry_plane",
    ]
    compatible_physics: list[Literal["structural", "thermal", "fluid", "contact"]] = Field(
        default_factory=list
    )


class ComponentDefinition(StrictModel):
    component_id: str = Field(pattern=IDENTIFIER.pattern)
    name: str = Field(min_length=1, max_length=200)
    body_ids: list[str] = Field(min_length=1, max_length=1000)
    material: str | None = Field(default=None, max_length=100)
    interface_ids: list[str] = Field(default_factory=list, max_length=1000)


class ComponentPlacement(StrictModel):
    translation: LengthVector3 = Field(default_factory=LengthVector3)
    rotation: RotationSpec | None = None


class ComponentInstance(StrictModel):
    instance_id: str = Field(pattern=IDENTIFIER.pattern)
    component_id: str = Field(pattern=IDENTIFIER.pattern)
    placement: ComponentPlacement = Field(default_factory=ComponentPlacement)
    parent_instance_id: str | None = Field(default=None, pattern=IDENTIFIER.pattern)
    repeated_from_instance_id: str | None = Field(default=None, pattern=IDENTIFIER.pattern)


class AssemblyRelationship(StrictModel):
    relationship_id: str = Field(pattern=IDENTIFIER.pattern)
    relationship_type: Literal[
        "fixed_placement", "offset", "aligned_axis", "coincident_plane", "concentric_axis"
    ]
    first_instance_id: str = Field(pattern=IDENTIFIER.pattern)
    second_instance_id: str | None = Field(default=None, pattern=IDENTIFIER.pattern)
    offset: MeasureInput | None = None
    axis: tuple[float, float, float] | None = None

    @model_validator(mode="after")
    def validate_relationship(self) -> "AssemblyRelationship":
        if self.relationship_type == "fixed_placement":
            return self
        if self.second_instance_id is None:
            raise ValueError(f"{self.relationship_type} requires two component instances")
        if self.relationship_type == "offset" and self.offset is None:
            raise ValueError("Offset relationship requires an explicit offset")
        if self.relationship_type in {"aligned_axis", "concentric_axis"} and self.axis is None:
            raise ValueError(f"{self.relationship_type} requires an explicit axis")
        return self


class AssemblyInterference(StrictModel):
    first_instance_id: str
    second_instance_id: str
    intersection_volume_m3: float


class AssemblyValidationResult(StrictModel):
    valid: bool
    instance_count: int
    interferences: list[AssemblyInterference] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    assembly_hash: str


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
        (
            TransformFeature, MirrorFeature, FilletFeature, ChamferFeature, ShellFeature,
            LinearPatternFeature, CircularPatternFeature, GridPatternFeature, HoleFeature,
        ),
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
    paths: list[PathDefinition] = Field(default_factory=list, max_length=1000)
    bodies: list[BodyDefinition] = Field(min_length=1, max_length=1000)
    sketches: list[SketchDefinition] = Field(min_length=1, max_length=1000)
    features: list[Feature] = Field(min_length=1, max_length=5000)
    output_body_ids: list[str] = Field(min_length=1, max_length=1000)
    semantic_regions: list[SemanticRegion] = Field(default_factory=list, max_length=1000)
    engineering_interfaces: list[EngineeringInterface] = Field(default_factory=list, max_length=1000)
    components: list[ComponentDefinition] = Field(default_factory=list, max_length=1000)
    component_instances: list[ComponentInstance] = Field(default_factory=list, max_length=5000)
    assembly_relationships: list[AssemblyRelationship] = Field(default_factory=list, max_length=5000)
    detect_interference: bool = False
    operational_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_document_graph(self) -> "EngineeringDesignDocumentV2":
        named_lists = {
            "parameter": [item.name for item in self.parameters],
            "datum plane": [item.datum_id for item in self.datum_planes],
            "path": [item.path_id for item in self.paths],
            "body": [item.body_id for item in self.bodies],
            "sketch": [item.sketch_id for item in self.sketches],
            "feature": [item.feature_id for item in self.features],
            "semantic tag": [item.tag for item in self.semantic_regions],
            "engineering interface": [item.interface_id for item in self.engineering_interfaces],
            "component": [item.component_id for item in self.components],
            "component instance": [item.instance_id for item in self.component_instances],
            "assembly relationship": [item.relationship_id for item in self.assembly_relationships],
        }
        for label, identifiers in named_lists.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"Duplicate {label} ID")

        parameter_names = set(named_lists["parameter"])
        unknown_parameters = set(_walk_parameter_references([
            self.tolerance_policy, self.datum_planes, self.paths, self.sketches, self.features,
            self.component_instances, self.assembly_relationships,
        ])) - parameter_names
        if unknown_parameters:
            raise ValueError(f"Unknown parameter reference(s): {sorted(unknown_parameters)}")

        body_ids = set(named_lists["body"])
        sketch_ids = set(named_lists["sketch"])
        datum_ids = set(named_lists["datum plane"])
        path_ids = set(named_lists["path"])
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
            if isinstance(feature, SweepFeature):
                unknown_sketches = set(feature.sketch_ids) - sketch_ids
                if unknown_sketches:
                    raise ValueError(
                        f"Feature '{feature.feature_id}' references unknown sketch(es): {sorted(unknown_sketches)}"
                    )
                if feature.path_id not in path_ids:
                    raise ValueError(f"Feature '{feature.feature_id}' references unknown path '{feature.path_id}'")

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

        region_tags = set(named_lists["semantic tag"])
        interface_ids = set(named_lists["engineering interface"])
        for interface in self.engineering_interfaces:
            if interface.region_tag not in region_tags:
                raise ValueError(f"Engineering interface '{interface.interface_id}' references unknown region")

        component_ids = set(named_lists["component"])
        for body in self.bodies:
            if body.component_id and body.component_id not in component_ids:
                raise ValueError(f"Body '{body.body_id}' references unknown component")
            if body.material is not None and not body.material.strip():
                raise ValueError(f"Body '{body.body_id}' has an empty material assignment")
        for component in self.components:
            if set(component.body_ids) - body_ids:
                raise ValueError(f"Component '{component.component_id}' references unknown body")
            if set(component.interface_ids) - interface_ids:
                raise ValueError(f"Component '{component.component_id}' references unknown interface")
            if component.material is not None and not component.material.strip():
                raise ValueError(f"Component '{component.component_id}' has an empty material assignment")

        instance_ids = set(named_lists["component instance"])
        instance_by_id = {item.instance_id: item for item in self.component_instances}
        parent_by_instance: dict[str, str | None] = {}
        for instance in self.component_instances:
            if instance.component_id not in component_ids:
                raise ValueError(f"Instance '{instance.instance_id}' references unknown component")
            if instance.parent_instance_id and instance.parent_instance_id not in instance_ids:
                raise ValueError(f"Instance '{instance.instance_id}' references unknown parent instance")
            if instance.repeated_from_instance_id and instance.repeated_from_instance_id not in instance_ids:
                raise ValueError(f"Instance '{instance.instance_id}' references unknown repeated instance")
            if (
                instance.repeated_from_instance_id
                and instance_by_id[instance.repeated_from_instance_id].component_id != instance.component_id
            ):
                raise ValueError(
                    f"Instance '{instance.instance_id}' repeats an instance of a different component"
                )
            parent_by_instance[instance.instance_id] = instance.parent_instance_id
        for instance_id in parent_by_instance:
            seen: set[str] = set()
            current: str | None = instance_id
            while current is not None:
                if current in seen:
                    raise ValueError(f"Cyclic component hierarchy detected at '{instance_id}'")
                seen.add(current)
                current = parent_by_instance.get(current)
        for relationship in self.assembly_relationships:
            if relationship.first_instance_id not in instance_ids:
                raise ValueError(f"Assembly relationship '{relationship.relationship_id}' references unknown instance")
            if relationship.second_instance_id and relationship.second_instance_id not in instance_ids:
                raise ValueError(f"Assembly relationship '{relationship.relationship_id}' references unknown instance")
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
    sketch_solve_results: list[SketchSolveResult] = Field(default_factory=list)
    semantic_regions: list[ResolvedSemanticRegion] = Field(default_factory=list)
    assembly_validation: AssemblyValidationResult | None = None
    feature_hashes: dict[str, str] = Field(default_factory=dict)
    cache_hits: list[str] = Field(default_factory=list)
    repair_provenance: list[dict[str, Any]] = Field(default_factory=list)
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
