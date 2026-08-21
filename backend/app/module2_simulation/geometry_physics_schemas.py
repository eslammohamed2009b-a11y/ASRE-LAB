"""Typed contracts for the authoritative CAD -> mesh -> physics bridge.

All solver-facing geometry is SI.  CadQuery/OpenCascade remains millimetre
native behind the compiler boundary; mesh coordinates and characteristic
lengths in artifacts are metres.
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2, Quantity


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DomainKind(str, Enum):
    SOLID = "solid"
    FLUID = "fluid"
    ACOUSTIC = "acoustic"
    ELECTROMAGNETIC = "electromagnetic"


class AnalysisFamilyV1(str, Enum):
    THERMAL = "thermal"
    STRUCTURAL = "structural"
    MODAL = "modal"
    CFD = "cfd"
    ACOUSTICS = "acoustics"
    ELECTROSTATICS = "electrostatics"


class ValidationState(str, Enum):
    VALID = "VALID"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    INVALID = "INVALID"


class GeometryPreparationState(str, Enum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    INVALID_FOR_MESHING = "INVALID_FOR_MESHING"


class PhysicsDomain(StrictModel):
    domain_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    source_body_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    domain_kind: DomainKind
    description: str | None = Field(default=None, max_length=500)
    explicit_fluid_volume: bool = False

    @model_validator(mode="after")
    def fluid_is_explicit(self) -> "PhysicsDomain":
        if self.domain_kind == DomainKind.FLUID and not self.explicit_fluid_volume:
            raise ValueError("Fluid domains must identify an explicitly modeled fluid volume")
        if self.domain_kind != DomainKind.FLUID and self.explicit_fluid_volume:
            raise ValueError("explicit_fluid_volume applies only to fluid domains")
        return self


class SemanticSizingRule(StrictModel):
    semantic_region: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    target_size: Quantity


class MeshSpecification(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    dimension: Literal[3] = 3
    element_family: Literal["tetrahedron"] = "tetrahedron"
    element_type: Literal["tetra4"] = "tetra4"
    order: Literal[1] = 1
    target_size: Quantity
    minimum_size: Quantity | None = None
    maximum_size: Quantity | None = None
    refinement_level: Literal["coarse", "medium", "fine", "custom"] = "custom"
    semantic_sizing: list[SemanticSizingRule] = Field(default_factory=list, max_length=100)
    growth_ratio: float = Field(default=1.5, ge=1.0, le=3.0)
    curvature_adaptation: bool = True
    deterministic_policy: Literal["asre-tet-v1"] = "asre-tet-v1"
    maximum_nodes: int = Field(default=75_000, ge=16, le=500_000)
    maximum_elements: int = Field(default=300_000, ge=1, le=2_000_000)

    @model_validator(mode="after")
    def sizes_are_lengths(self) -> "MeshSpecification":
        values = [self.target_size, self.minimum_size, self.maximum_size]
        if any(value is not None and value.unit.value in {"deg", "rad"} for value in values):
            raise ValueError("Mesh sizes must use length units")
        if any(value is not None and (not math.isfinite(value.value) or value.value <= 0) for value in values):
            raise ValueError("Mesh sizes must be finite and positive")
        names = [item.semantic_region for item in self.semantic_sizing]
        if len(names) != len(set(names)):
            raise ValueError("A semantic region may have only one local sizing rule")
        return self


class GeometryPreparationResult(StrictModel):
    status: GeometryPreparationState
    design_hash: str
    geometry_fingerprint: str
    selected_body_ids: list[str]
    solid_count: int
    diagnostics: list[str] = Field(default_factory=list)


class MeshQualityMetrics(StrictModel):
    node_count: int = Field(ge=0)
    tetrahedron_count: int = Field(ge=0)
    boundary_facet_count: int = Field(ge=0)
    minimum_element_volume_m3: float = Field(ge=0)
    minimum_edge_length_m: float = Field(ge=0)
    maximum_edge_length_m: float = Field(ge=0)
    maximum_edge_aspect_ratio: float = Field(ge=0)
    minimum_mean_ratio_quality: float = Field(ge=0, le=1)
    inverted_element_count: int = Field(ge=0)
    degenerate_element_count: int = Field(ge=0)


class SemanticMeshMapping(StrictModel):
    semantic_region: str
    body_id: str
    cad_resolution_status: Literal["EXACT", "DERIVED", "RESELECTED"]
    topology_kind: Literal["face"]
    topology_signatures: list[str]
    physical_group_id: int = Field(gt=0)
    boundary_facet_ids: list[int]
    domain_ids: list[str]
    mapping_status: Literal["EXACT", "MAPPED"]
    warnings: list[str] = Field(default_factory=list)


class CFDSemanticMeshMappingV1(StrictModel):
    """Truthful CAD-to-OpenFOAM boundary identity for certified FV meshes."""

    mapping_type: Literal["cfd_openfoam_patch"] = "cfd_openfoam_patch"
    semantic_region: str
    body_id: str
    topology_signatures: list[str]
    domain_ids: list[str]
    source_surface_region: str
    final_patch: str
    start_face: int = Field(ge=0)
    face_count: int = Field(gt=0)
    face_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_face_range(self) -> "CFDSemanticMeshMappingV1":
        if self.face_ids != list(range(self.start_face, self.start_face + self.face_count)):
            raise ValueError("CFD semantic face IDs must equal the certified OpenFOAM patch range")
        return self


class DomainMeshMapping(StrictModel):
    domain_id: str
    source_body_id: str
    domain_kind: DomainKind
    physical_group_id: int = Field(gt=0)
    volume_element_ids: list[int]


class MeshArtifact(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    mesh_id: str
    artifact_id: str | None = None
    mesh_hash: str
    design_hash: str
    geometry_fingerprint: str
    mesher_id: Literal["asre-occ-scipy-tet"] = "asre-occ-scipy-tet"
    mesher_version: str
    coordinate_unit: Literal["m"] = "m"
    dimension: Literal[3] = 3
    element_types: list[Literal["tetra4", "triangle3"]]
    specification: MeshSpecification
    domains: list[DomainMeshMapping]
    semantic_mappings: list[SemanticMeshMapping]
    quality: MeshQualityMetrics
    # The OCC/SciPy adapter may make a bounded series of deterministic
    # BRep-derived sampling attempts.  Keeping the selected attempt in the
    # artifact prevents a nominal requested size from concealing the actual
    # certified fallback resolution.
    fallback_provenance: list[dict[str, Any]] = Field(default_factory=list)
    validation_status: ValidationState
    warnings: list[str] = Field(default_factory=list)


class MaterialPropertySnapshot(StrictModel):
    name: str
    value: float
    unit: str
    source: str
    valid_range: tuple[float, float] | None = None
    notes: str | None = None

    @field_validator("value")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Material values must be finite")
        return value


class MaterialSnapshot(StrictModel):
    material_name: str
    properties: list[MaterialPropertySnapshot]
    snapshot_hash: str


class MaterialAssignment(StrictModel):
    domain_id: str
    material_name: str


class SurfaceBC(StrictModel):
    bc_id: str
    semantic_region: str


class TemperatureBC(SurfaceBC):
    bc_type: Literal["temperature"] = "temperature"
    temperature_k: float = Field(gt=0)


class HeatFluxBC(SurfaceBC):
    bc_type: Literal["heat_flux"] = "heat_flux"
    heat_flux_w_m2: float


class ConvectionBC(SurfaceBC):
    bc_type: Literal["convection"] = "convection"
    coefficient_w_m2_k: float = Field(gt=0)
    ambient_temperature_k: float = Field(gt=0)


class FixedSupportBC(SurfaceBC):
    bc_type: Literal["fixed_support"] = "fixed_support"


class DisplacementBC(SurfaceBC):
    bc_type: Literal["displacement"] = "displacement"
    displacement_m: tuple[float | None, float | None, float | None]


class ForceBC(SurfaceBC):
    bc_type: Literal["force"] = "force"
    force_n: tuple[float, float, float]


class PressureBC(SurfaceBC):
    bc_type: Literal["pressure"] = "pressure"
    pressure_pa: float


class VelocityInletBC(SurfaceBC):
    bc_type: Literal["velocity_inlet"] = "velocity_inlet"
    velocity_m_s: tuple[float, float, float]


class FlowInletBC(SurfaceBC):
    bc_type: Literal["flow_inlet"] = "flow_inlet"
    volumetric_flow_m3_s: float | None = None
    mass_flow_kg_s: float | None = None

    @model_validator(mode="after")
    def exactly_one_flow(self) -> "FlowInletBC":
        if (self.volumetric_flow_m3_s is None) == (self.mass_flow_kg_s is None):
            raise ValueError("Exactly one inlet flow measure is required")
        return self


class PressureBoundaryBC(SurfaceBC):
    bc_type: Literal["pressure_boundary"] = "pressure_boundary"
    pressure_pa: float


class WallBC(SurfaceBC):
    bc_type: Literal["wall"] = "wall"
    no_slip: bool = True


class SymmetryBC(SurfaceBC):
    bc_type: Literal["symmetry"] = "symmetry"


class AcousticPressureBC(SurfaceBC):
    bc_type: Literal["acoustic_pressure"] = "acoustic_pressure"
    pressure_pa: float


class AcousticWallBC(SurfaceBC):
    bc_type: Literal["acoustic_wall"] = "acoustic_wall"
    condition: Literal["rigid", "pressure_release", "impedance"]
    impedance_pa_s_m: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def impedance_value(self) -> "AcousticWallBC":
        if (self.condition == "impedance") != (self.impedance_pa_s_m is not None):
            raise ValueError("Only impedance boundaries require impedance_pa_s_m")
        return self


class ElectrostaticPotentialBC(SurfaceBC):
    bc_type: Literal["electric_potential"] = "electric_potential"
    potential_v: float


class VolumetricHeatSourceBC(StrictModel):
    bc_type: Literal["volumetric_heat_source"] = "volumetric_heat_source"
    bc_id: str
    domain_id: str
    heat_source_w_m3: float


class GravityBC(StrictModel):
    bc_type: Literal["gravity"] = "gravity"
    bc_id: str
    domain_id: str
    acceleration_m_s2: tuple[float, float, float]


BoundaryCondition = Annotated[
    Union[
        TemperatureBC, HeatFluxBC, ConvectionBC, VolumetricHeatSourceBC,
        FixedSupportBC, DisplacementBC, ForceBC, PressureBC, GravityBC,
        VelocityInletBC, FlowInletBC, PressureBoundaryBC, WallBC, SymmetryBC,
        AcousticPressureBC, AcousticWallBC, ElectrostaticPotentialBC,
    ],
    Field(discriminator="bc_type"),
]


class SteadyThermalSettings(StrictModel):
    settings_type: Literal["steady_thermal"] = "steady_thermal"
    tolerance: float = Field(default=1e-8, gt=0, le=1)
    maximum_iterations: int = Field(default=1000, ge=1, le=100_000)


class LinearStructuralSettings(StrictModel):
    settings_type: Literal["linear_static"] = "linear_static"
    tolerance: float = Field(default=1e-8, gt=0, le=1)
    maximum_iterations: int = Field(default=1000, ge=1, le=100_000)


class ModalSettings(StrictModel):
    settings_type: Literal["modal_eigen"] = "modal_eigen"
    requested_modes: int = Field(default=10, ge=1, le=500)


class SteadyFlowSettings(StrictModel):
    settings_type: Literal["steady_flow"] = "steady_flow"
    tolerance: float = Field(default=1e-7, gt=0, le=1)
    maximum_iterations: int = Field(default=2000, ge=1, le=100_000)


class HarmonicAcousticSettings(StrictModel):
    settings_type: Literal["harmonic_acoustic"] = "harmonic_acoustic"
    frequency_hz: float = Field(gt=0)


class ElectrostaticSettings(StrictModel):
    settings_type: Literal["electrostatic"] = "electrostatic"
    tolerance: float = Field(default=1e-8, gt=0, le=1)


NumericalSettingsV1 = Annotated[
    Union[
        SteadyThermalSettings, LinearStructuralSettings, ModalSettings,
        SteadyFlowSettings, HarmonicAcousticSettings, ElectrostaticSettings,
    ],
    Field(discriminator="settings_type"),
]


class PhysicsModelRequest(StrictModel):
    analysis_family: AnalysisFamilyV1
    domains: list[PhysicsDomain] = Field(min_length=1, max_length=100)
    material_assignments: list[MaterialAssignment] = Field(min_length=1, max_length=100)
    boundary_conditions: list[BoundaryCondition] = Field(min_length=1, max_length=500)
    numerical_settings: NumericalSettingsV1
    expected_outputs: list[str] = Field(min_length=1, max_length=100)


class PhysicsModelV1(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    physics_model_id: str
    physics_hash: str
    analysis_family: AnalysisFamilyV1
    design_hash: str
    geometry_fingerprint: str
    mesh_id: str
    mesh_hash: str
    domains: list[PhysicsDomain]
    materials: list[MaterialSnapshot]
    material_assignments: list[MaterialAssignment]
    semantic_mappings: list[Union[SemanticMeshMapping, CFDSemanticMeshMappingV1]]
    boundary_conditions: list[BoundaryCondition]
    numerical_settings: NumericalSettingsV1
    expected_outputs: list[str]
    solver_requirements: list[str]
    validation_status: ValidationState
    warnings: list[str] = Field(default_factory=list)


class MeshCreateRequest(StrictModel):
    experiment_id: str
    document: EngineeringDesignDocumentV2
    domains: list[PhysicsDomain] = Field(min_length=1, max_length=100)
    specification: MeshSpecification


class PhysicsCreateRequest(MeshCreateRequest):
    physics: PhysicsModelRequest


class PhysicsExecutionRequest(StrictModel):
    solver_id: Literal["thermal_fem_3d_v1", "structural_linear_elasticity_3d_v1", "modal_fem_3d_v1"]


class PhysicsExecutionResult(StrictModel):
    simulation_id: str
    solver_id: str
    status: Literal["completed", "failed", "queued", "running"]
