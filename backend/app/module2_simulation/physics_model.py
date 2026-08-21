"""Deterministic validation and construction of solver-ready PhysicsModelV1."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.module2_simulation.geometry_physics_schemas import (
    AnalysisFamilyV1,
    CFDSemanticMeshMappingV1,
    DomainKind,
    GravityBC,
    MaterialPropertySnapshot,
    MaterialSnapshot,
    PhysicsModelRequest,
    PhysicsModelV1,
    SemanticMeshMapping,
    ValidationState,
    VolumetricHeatSourceBC,
)
from app.module2_simulation.materials import get_material
from app.module2_simulation.meshing import GeneratedMesh

if TYPE_CHECKING:
    from app.module2_simulation.openfoam_fv_mesh import CFDGeneratedMeshV1


class PhysicsValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _PhysicsMeshBinding:
    """Minimal authoritative mesh identity consumed by physics validation."""

    design_hash: str
    geometry_fingerprint: str
    mesh_id: str
    mesh_hash: str
    domains: tuple[tuple[str, str, DomainKind], ...]
    semantic_mappings: tuple[SemanticMeshMapping | CFDSemanticMeshMappingV1, ...]
    validation_status: ValidationState
    solver_requirements: tuple[str, ...]


_REQUIRED_PROPERTIES: dict[AnalysisFamilyV1, dict[str, str]] = {
    AnalysisFamilyV1.THERMAL: {"thermal_conductivity": "W/(m*K)"},
    AnalysisFamilyV1.STRUCTURAL: {
        "elastic_modulus": "Pa", "poisson_ratio": "dimensionless", "density": "kg/m3",
    },
    AnalysisFamilyV1.MODAL: {
        "elastic_modulus": "Pa", "poisson_ratio": "dimensionless", "density": "kg/m3",
    },
    AnalysisFamilyV1.CFD: {"density": "kg/m3", "dynamic_viscosity": "Pa*s"},
    AnalysisFamilyV1.ACOUSTICS: {"density": "kg/m3", "speed_of_sound": "m/s"},
    AnalysisFamilyV1.ELECTROSTATICS: {"permittivity": "F/m"},
}

_DOMAIN_COMPATIBILITY: dict[AnalysisFamilyV1, set[DomainKind]] = {
    AnalysisFamilyV1.THERMAL: {DomainKind.SOLID, DomainKind.FLUID},
    AnalysisFamilyV1.STRUCTURAL: {DomainKind.SOLID},
    AnalysisFamilyV1.MODAL: {DomainKind.SOLID},
    AnalysisFamilyV1.CFD: {DomainKind.FLUID},
    AnalysisFamilyV1.ACOUSTICS: {DomainKind.ACOUSTIC, DomainKind.FLUID},
    AnalysisFamilyV1.ELECTROSTATICS: {DomainKind.ELECTROMAGNETIC, DomainKind.SOLID},
}

_BC_COMPATIBILITY: dict[AnalysisFamilyV1, set[str]] = {
    AnalysisFamilyV1.THERMAL: {"temperature", "heat_flux", "convection", "volumetric_heat_source"},
    AnalysisFamilyV1.STRUCTURAL: {"fixed_support", "displacement", "force", "pressure", "gravity"},
    AnalysisFamilyV1.MODAL: {"fixed_support", "displacement"},
    AnalysisFamilyV1.CFD: {"velocity_inlet", "flow_inlet", "pressure_boundary", "wall", "symmetry"},
    AnalysisFamilyV1.ACOUSTICS: {"acoustic_pressure", "acoustic_wall"},
    AnalysisFamilyV1.ELECTROSTATICS: {"electric_potential"},
}

_SETTINGS_COMPATIBILITY = {
    AnalysisFamilyV1.THERMAL: "steady_thermal",
    AnalysisFamilyV1.STRUCTURAL: "linear_static",
    AnalysisFamilyV1.MODAL: "modal_eigen",
    AnalysisFamilyV1.CFD: "steady_flow",
    AnalysisFamilyV1.ACOUSTICS: "harmonic_acoustic",
    AnalysisFamilyV1.ELECTROSTATICS: "electrostatic",
}

_OUTPUTS: dict[AnalysisFamilyV1, set[str]] = {
    AnalysisFamilyV1.THERMAL: {"temperature", "heat_flux"},
    AnalysisFamilyV1.STRUCTURAL: {"displacement", "strain", "stress", "reaction_force"},
    AnalysisFamilyV1.MODAL: {"eigenfrequency", "mode_shape"},
    AnalysisFamilyV1.CFD: {"velocity", "pressure", "mass_flow"},
    AnalysisFamilyV1.ACOUSTICS: {"acoustic_pressure", "sound_pressure_level"},
    AnalysisFamilyV1.ELECTROSTATICS: {"electric_potential", "electric_field"},
}


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _snapshot(material_name: str, family: AnalysisFamilyV1) -> MaterialSnapshot:
    library = get_material(material_name)
    properties: list[MaterialPropertySnapshot] = []
    for name, expected_unit in _REQUIRED_PROPERTIES[family].items():
        prop = library.get(name)
        if prop is None:
            raise PhysicsValidationError(
                "MATERIAL_PROPERTY_MISSING",
                f"Material '{material_name}' lacks required property '{name}' for {family.value}",
            )
        if prop.unit != expected_unit:
            raise PhysicsValidationError(
                "MATERIAL_UNIT_MISMATCH",
                f"Material '{material_name}' property '{name}' must use '{expected_unit}'",
            )
        if not math.isfinite(prop.value) or prop.value <= 0:
            raise PhysicsValidationError(
                "INVALID_MATERIAL_PROPERTY",
                f"Material '{material_name}' property '{name}' must be finite and positive",
            )
        properties.append(MaterialPropertySnapshot(
            name=name, value=prop.value, unit=prop.unit, source=prop.source,
            valid_range=prop.valid_range, notes=prop.notes,
        ))
    # Yield strength is not required to solve linear elasticity, but when the
    # authoritative library supplies it the immutable snapshot makes a clearly
    # labelled factor-of-safety diagnostic possible.
    if family == AnalysisFamilyV1.STRUCTURAL and "yield_strength" in library:
        prop = library["yield_strength"]
        if prop.unit == "Pa" and math.isfinite(prop.value) and prop.value > 0:
            properties.append(MaterialPropertySnapshot(
                name="yield_strength", value=prop.value, unit=prop.unit, source=prop.source,
                valid_range=prop.valid_range, notes=prop.notes,
            ))
    identity = {
        "material_name": material_name.lower().strip(),
        "properties": [item.model_dump(mode="json") for item in properties],
    }
    return MaterialSnapshot(
        material_name=material_name.lower().strip(), properties=properties, snapshot_hash=_hash(identity)
    )


def _validate_minimum_bcs(family: AnalysisFamilyV1, types: set[str]) -> None:
    rules = {
        AnalysisFamilyV1.THERMAL: [({"temperature"}, "a temperature boundary"),
                                   ({"heat_flux", "convection", "volumetric_heat_source"}, "a thermal load/source")],
        AnalysisFamilyV1.STRUCTURAL: [({"fixed_support", "displacement"}, "a support constraint"),
                                      ({"force", "pressure", "gravity"}, "a structural load")],
        AnalysisFamilyV1.MODAL: [({"fixed_support", "displacement"}, "a support constraint")],
        AnalysisFamilyV1.CFD: [({"velocity_inlet", "flow_inlet"}, "an inlet"),
                               ({"pressure_boundary"}, "a pressure boundary"), ({"wall"}, "a wall")],
        AnalysisFamilyV1.ACOUSTICS: [({"acoustic_pressure"}, "an acoustic pressure source")],
        AnalysisFamilyV1.ELECTROSTATICS: [({"electric_potential"}, "an electric-potential boundary")],
    }
    for alternatives, description in rules[family]:
        if not types.intersection(alternatives):
            raise PhysicsValidationError("INCOMPLETE_BOUNDARY_CONDITIONS", f"{family.value} requires {description}")


def _build_physics_model(binding: _PhysicsMeshBinding, request: PhysicsModelRequest) -> PhysicsModelV1:
    if binding.validation_status == ValidationState.INVALID:
        raise PhysicsValidationError("INVALID_MESH", "Physics models require a validated mesh")
    requested_domains = {
        item.domain_id: (item.source_body_id, item.domain_kind) for item in request.domains
    }
    meshed_domains = {
        domain_id: (source_body_id, domain_kind)
        for domain_id, source_body_id, domain_kind in binding.domains
    }
    if requested_domains != meshed_domains:
        raise PhysicsValidationError("MESH_DOMAIN_MISMATCH", "Physics domains do not match the meshed CAD domains")
    domain_by_id = {item.domain_id: item for item in request.domains}
    if len(domain_by_id) != len(request.domains):
        raise PhysicsValidationError("DUPLICATE_DOMAIN", "Physics domain identifiers must be unique")
    for domain in request.domains:
        if domain.domain_kind not in _DOMAIN_COMPATIBILITY[request.analysis_family]:
            raise PhysicsValidationError(
                "INCOMPATIBLE_DOMAIN",
                f"Domain '{domain.domain_id}' kind '{domain.domain_kind.value}' is incompatible with {request.analysis_family.value}",
            )

    assignments = {item.domain_id: item.material_name for item in request.material_assignments}
    if len(assignments) != len(request.material_assignments):
        raise PhysicsValidationError("DUPLICATE_MATERIAL_ASSIGNMENT", "Each domain requires exactly one material assignment")
    missing = sorted(set(domain_by_id) - set(assignments))
    extra = sorted(set(assignments) - set(domain_by_id))
    if missing or extra:
        raise PhysicsValidationError(
            "MATERIAL_ASSIGNMENT_MISMATCH",
            f"Material assignments missing={missing}, unknown={extra}",
        )
    snapshots_by_name = {
        name.lower().strip(): _snapshot(name, request.analysis_family) for name in sorted(set(assignments.values()))
    }

    expected_settings = _SETTINGS_COMPATIBILITY[request.analysis_family]
    if request.numerical_settings.settings_type != expected_settings:
        raise PhysicsValidationError(
            "INCOMPATIBLE_NUMERICAL_SETTINGS",
            f"{request.analysis_family.value} requires '{expected_settings}' settings",
        )
    unsupported_outputs = sorted(set(request.expected_outputs) - _OUTPUTS[request.analysis_family])
    if unsupported_outputs:
        raise PhysicsValidationError("UNSUPPORTED_OUTPUT", f"Unsupported requested outputs: {unsupported_outputs}")

    mapping_by_tag = {item.semantic_region: item for item in binding.semantic_mappings}
    bc_ids = [item.bc_id for item in request.boundary_conditions]
    if len(bc_ids) != len(set(bc_ids)):
        raise PhysicsValidationError("DUPLICATE_BOUNDARY_CONDITION", "Boundary condition identifiers must be unique")
    bc_types = {item.bc_type for item in request.boundary_conditions}
    unsupported_bcs = sorted(bc_types - _BC_COMPATIBILITY[request.analysis_family])
    if unsupported_bcs:
        raise PhysicsValidationError(
            "INCOMPATIBLE_BOUNDARY_CONDITION",
            f"Boundary conditions {unsupported_bcs} are incompatible with {request.analysis_family.value}",
        )
    for bc in request.boundary_conditions:
        if isinstance(bc, (VolumetricHeatSourceBC, GravityBC)):
            if bc.domain_id not in domain_by_id:
                raise PhysicsValidationError(
                    "UNKNOWN_BOUNDARY_DOMAIN", f"Boundary condition '{bc.bc_id}' targets an unknown volume domain"
                )
            continue
        mapping = mapping_by_tag.get(bc.semantic_region)
        mapped_faces = getattr(mapping, "boundary_facet_ids", None) or getattr(mapping, "face_ids", None)
        if mapping is None or not mapped_faces:
            raise PhysicsValidationError(
                "INVALID_SEMANTIC_TARGET",
                f"Boundary condition '{bc.bc_id}' requires a nonempty mapped semantic surface",
            )
        if not set(mapping.domain_ids).intersection(domain_by_id):
            raise PhysicsValidationError(
                "SEMANTIC_DOMAIN_MISMATCH", f"Boundary condition '{bc.bc_id}' is outside selected domains"
            )
    _validate_minimum_bcs(request.analysis_family, bc_types)

    scientific_input = {
        "schema_version": "1.0",
        "analysis_family": request.analysis_family.value,
        "design_hash": binding.design_hash,
        "geometry_fingerprint": binding.geometry_fingerprint,
        "mesh_hash": binding.mesh_hash,
        "domains": [item.model_dump(mode="json") for item in request.domains],
        "material_assignments": [item.model_dump(mode="json") for item in request.material_assignments],
        "material_snapshots": [
            snapshots_by_name[name].model_dump(mode="json") for name in sorted(snapshots_by_name)
        ],
        "boundary_conditions": [item.model_dump(mode="json") for item in request.boundary_conditions],
        "numerical_settings": request.numerical_settings.model_dump(mode="json"),
        "expected_outputs": sorted(request.expected_outputs),
    }
    if "certified_finite_volume" in binding.solver_requirements:
        scientific_input["certified_fv_mesh_id"] = binding.mesh_id
        scientific_input["certified_fv_mesh_hash"] = binding.mesh_hash
    physics_hash = _hash(scientific_input)
    return PhysicsModelV1(
        physics_model_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"asre-lab:physics:{physics_hash}")),
        physics_hash=physics_hash,
        analysis_family=request.analysis_family,
        design_hash=binding.design_hash,
        geometry_fingerprint=binding.geometry_fingerprint,
        mesh_id=binding.mesh_id,
        mesh_hash=binding.mesh_hash,
        domains=request.domains,
        materials=[snapshots_by_name[name] for name in sorted(snapshots_by_name)],
        material_assignments=request.material_assignments,
        semantic_mappings=list(binding.semantic_mappings),
        boundary_conditions=request.boundary_conditions,
        numerical_settings=request.numerical_settings,
        expected_outputs=sorted(request.expected_outputs),
        solver_requirements=[
            *binding.solver_requirements, request.analysis_family.value,
            *sorted(bc_types),
        ],
        validation_status=ValidationState.VALID,
    )


def build_physics_model(mesh: GeneratedMesh, request: PhysicsModelRequest) -> PhysicsModelV1:
    """Build the unchanged FEM/TET-bound physics model."""
    metadata = mesh.metadata
    binding = _PhysicsMeshBinding(
        design_hash=metadata.design_hash,
        geometry_fingerprint=metadata.geometry_fingerprint,
        mesh_id=metadata.mesh_id,
        mesh_hash=metadata.mesh_hash,
        domains=tuple((item.domain_id, item.source_body_id, item.domain_kind) for item in metadata.domains),
        semantic_mappings=tuple(metadata.semantic_mappings),
        validation_status=metadata.validation_status,
        solver_requirements=("authoritative_cad", "3d", "tetra4"),
    )
    return _build_physics_model(binding, request)


def build_cfd_physics_model(mesh: CFDGeneratedMeshV1, request: PhysicsModelRequest) -> PhysicsModelV1:
    """Bind CFD physics directly to a certified CAD-derived finite-volume mesh."""
    from app.module2_simulation.openfoam_fv_mesh import CFDGeneratedMeshV1

    if not isinstance(mesh, CFDGeneratedMeshV1):
        raise PhysicsValidationError("CERTIFIED_FV_MESH_REQUIRED", "CFD physics requires a certified finite-volume mesh")
    if request.analysis_family != AnalysisFamilyV1.CFD:
        raise PhysicsValidationError("CFD_ANALYSIS_REQUIRED", "The certified finite-volume binding is CFD-only")

    mappings: list[CFDSemanticMeshMappingV1] = []
    for patch in mesh.semantic_patches:
        if patch.start_face is None or patch.final_face_count <= 0:
            raise PhysicsValidationError("INVALID_CFD_SEMANTIC_MAPPING", "Certified CFD patch lacks its actual OpenFOAM face range")
        mappings.append(CFDSemanticMeshMappingV1(
            semantic_region=patch.semantic_region,
            body_id=mesh.source_body_id,
            topology_signatures=patch.topology_signatures,
            domain_ids=[mesh.fluid_domain_id],
            source_surface_region=patch.surface_region,
            final_patch=patch.final_patch,
            start_face=patch.start_face,
            face_count=patch.final_face_count,
            face_ids=list(range(patch.start_face, patch.start_face + patch.final_face_count)),
        ))
    if sum(item.face_count for item in mappings) != mesh.boundary_face_count:
        raise PhysicsValidationError("INVALID_CFD_SEMANTIC_MAPPING", "Certified CFD patch faces do not cover the finite-volume boundary")

    binding = _PhysicsMeshBinding(
        design_hash=mesh.design_hash,
        geometry_fingerprint=mesh.geometry_fingerprint,
        mesh_id=mesh.mesh_id,
        mesh_hash=mesh.mesh_hash,
        domains=((mesh.fluid_domain_id, mesh.source_body_id, DomainKind.FLUID),),
        semantic_mappings=tuple(mappings),
        validation_status=mesh.validation_status,
        solver_requirements=("authoritative_cad", "3d", "certified_finite_volume", *mesh.cell_types),
    )
    return _build_physics_model(binding, request)
