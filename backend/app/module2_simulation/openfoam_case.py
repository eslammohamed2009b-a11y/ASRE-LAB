"""Bounded OpenFOAM 14 steady laminar CFD case, execution, and result parsing."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.module2_simulation.geometry_physics_schemas import (
    AnalysisFamilyV1,
    FlowInletBC,
    PhysicsModelV1,
    PressureBoundaryBC,
    VelocityInletBC,
    WallBC,
)
from app.module2_simulation.meshing import GeneratedMesh
from app.module2_simulation.openfoam_mesh import PolyMeshExport, export_poly_mesh

SOLVER_ID = "cfd_openfoam_laminar_internal_3d_v1"
SOLVER_VERSION = "1.0.0"
BACKEND_ID = "openfoam-foundation-14"
BACKEND_VERSION = "20260724"
CASE_GENERATOR_VERSION = "asre-openfoam-laminar-case-1.0"
MASS_IMBALANCE_LIMIT = 1e-3
_DYNAMIC_TOKENS = ("#code", "#codeStream", "#codeDict", "dynamicCode", "codedFixedValue", "#include", "libs")
_U_DIMENSIONS = (0, 1, -1, 0, 0, 0, 0)
_P_DIMENSIONS = (0, 2, -2, 0, 0, 0, 0)
_PHI_DIMENSIONS = (0, 3, -1, 0, 0, 0, 0)


class OpenFOAMCaseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _StrictResult(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CFDFieldV1(_StrictResult):
    name: Literal["U", "p"]
    field_class: Literal["volVectorField", "volScalarField"]
    dimensions: tuple[int, int, int, int, int, int, int]
    unit: str
    location_type: Literal["cell_centered"] = "cell_centered"
    count: int = Field(gt=0)
    values: list[float] | list[tuple[float, float, float]]
    finite: Literal[True] = True


class CFDDiagnosticsV1(_StrictResult):
    final_u_residual: float = Field(ge=0)
    final_p_residual: float = Field(ge=0)
    volumetric_flow_in_m3_s: float = Field(ge=0)
    volumetric_flow_out_m3_s: float = Field(ge=0)
    mass_flow_in_kg_s: float = Field(ge=0)
    mass_flow_out_kg_s: float = Field(ge=0)
    normalized_mass_imbalance: float = Field(ge=0)
    acceptance_threshold: Literal[0.001] = MASS_IMBALANCE_LIMIT


class CFDFluxFieldV1(_StrictResult):
    name: Literal["phi"] = "phi"
    field_class: Literal["surfaceScalarField"] = "surfaceScalarField"
    dimensions: tuple[int, int, int, int, int, int, int]
    unit: Literal["m3/s"] = "m3/s"
    location_type: Literal["face_centered"] = "face_centered"
    internal_face_count: int = Field(ge=0)
    boundary_face_count: int = Field(gt=0)
    total_face_count: int = Field(gt=0)
    finite: Literal[True] = True


class CFDMaterialV1(_StrictResult):
    density_kg_m3: float = Field(gt=0)
    dynamic_viscosity_pa_s: float = Field(gt=0)
    density_source: str
    dynamic_viscosity_source: str


class CFDPressureInterpretationV1(_StrictResult):
    raw_quantity: Literal["kinematic_pressure"] = "kinematic_pressure"
    raw_dimensions: tuple[int, int, int, int, int, int, int] = _P_DIMENSIONS
    raw_unit: Literal["m2/s2"] = "m2/s2"
    conversion: Literal["physical_pressure_pa = authoritative_density_kg_m3 * p_kinematic_m2_s2"] = "physical_pressure_pa = authoritative_density_kg_m3 * p_kinematic_m2_s2"
    density_kg_m3: float = Field(gt=0)
    density_source: str


class CFDSolutionV1(_StrictResult):
    schema_version: Literal["1.0"] = "1.0"
    solver_id: Literal["cfd_openfoam_laminar_internal_3d_v1"] = SOLVER_ID
    solver_version: Literal["1.0.0"] = SOLVER_VERSION
    backend_id: Literal["openfoam-foundation-14"] = BACKEND_ID
    backend_version: Literal["20260724"] = BACKEND_VERSION
    mesh_id: str
    mesh_hash: str
    poly_mesh_hash: str
    case_fingerprint: str
    converged: bool
    iterations: int = Field(gt=0)
    summary_metrics: dict[str, float]
    fields: dict[str, CFDFieldV1]
    flux: CFDFluxFieldV1
    material: CFDMaterialV1
    pressure_interpretation: CFDPressureInterpretationV1
    diagnostics: CFDDiagnosticsV1
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str]


@dataclass(frozen=True)
class CFDCaseDefinition:
    case_fingerprint: str
    density_kg_m3: float
    dynamic_viscosity_pa_s: float
    density_source: str
    dynamic_viscosity_source: str
    kinematic_viscosity_m2_s: float
    inlet_velocity_m_s: tuple[float, float, float]
    outlet_pressure_pa: float
    inlet_patch: str
    outlet_patch: str
    wall_patch: str
    generated_files: tuple[str, ...]


@dataclass(frozen=True)
class ParsedSurfaceFlux:
    dimensions: tuple[int, int, int, int, int, int, int]
    internal_count: int
    boundary_values: dict[str, tuple[float, ...]]


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _foam_header(class_name: str, location: str, object_name: str) -> str:
    return (
        "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
        f"    class {class_name};\n    location \"{location}\";\n    object {object_name};\n}}\n"
    )


def _material(model: PhysicsModelV1) -> tuple[float, float, str, str]:
    if len(model.materials) != 1:
        raise OpenFOAMCaseError("CFD_SINGLE_MATERIAL_REQUIRED", "The bounded CFD solver requires exactly one fluid material")
    properties = {item.name: item for item in model.materials[0].properties}
    for required, unit in (("density", "kg/m3"), ("dynamic_viscosity", "Pa*s")):
        value = properties.get(required)
        if value is None:
            raise OpenFOAMCaseError("MATERIAL_PROPERTY_MISSING", f"CFD requires authoritative {required}")
        if value.unit != unit or not math.isfinite(value.value) or value.value <= 0:
            raise OpenFOAMCaseError("INVALID_MATERIAL_PROPERTY", f"CFD {required} must be positive finite {unit}")
    return (properties["density"].value, properties["dynamic_viscosity"].value,
        properties["density"].source, properties["dynamic_viscosity"].source)


def validate_cfd_scope(mesh: GeneratedMesh, model: PhysicsModelV1) -> tuple[VelocityInletBC, PressureBoundaryBC, WallBC, float, float]:
    if model.analysis_family != AnalysisFamilyV1.CFD:
        raise OpenFOAMCaseError("UNSUPPORTED_CFD_FAMILY", "Only the typed CFD analysis family is accepted")
    if model.mesh_id != mesh.metadata.mesh_id or model.mesh_hash != mesh.metadata.mesh_hash:
        raise OpenFOAMCaseError("AUTHORITATIVE_MESH_REQUIRED", "PhysicsModel does not match the authoritative mesh")
    if any(domain.domain_kind.value != "fluid" or not domain.explicit_fluid_volume for domain in model.domains):
        raise OpenFOAMCaseError("FLUID_DOMAIN_REQUIRED", "Only explicit FLUID CAD volumes are supported")
    if model.numerical_settings.settings_type != "steady_flow":
        raise OpenFOAMCaseError("UNSUPPORTED_CFD_SCOPE", "Only steady incompressible laminar flow is supported")
    if any(isinstance(bc, FlowInletBC) for bc in model.boundary_conditions):
        raise OpenFOAMCaseError("UNSUPPORTED_CFD_BOUNDARY", "FlowInletBC conversion is not implemented")
    inlets = [bc for bc in model.boundary_conditions if isinstance(bc, VelocityInletBC)]
    outlets = [bc for bc in model.boundary_conditions if isinstance(bc, PressureBoundaryBC)]
    walls = [bc for bc in model.boundary_conditions if isinstance(bc, WallBC)]
    if len(inlets) != 1 or len(outlets) != 1 or len(walls) != 1 or len(model.boundary_conditions) != 3:
        raise OpenFOAMCaseError("UNSUPPORTED_CFD_BOUNDARY", "Exactly one velocity inlet, pressure outlet, and no-slip wall group are required")
    if not walls[0].no_slip:
        raise OpenFOAMCaseError("UNSUPPORTED_CFD_BOUNDARY", "Only no-slip walls are supported")
    if not all(math.isfinite(value) for value in inlets[0].velocity_m_s):
        raise OpenFOAMCaseError("INVALID_INLET_VELOCITY", "Inlet velocity must be finite")
    density, viscosity, _, _ = _material(model)
    return inlets[0], outlets[0], walls[0], density, viscosity


def generate_laminar_case(mesh: GeneratedMesh, model: PhysicsModelV1, poly: PolyMeshExport, case: Path) -> CFDCaseDefinition:
    inlet, outlet, wall, density, viscosity = validate_cfd_scope(mesh, model)
    _, _, density_source, viscosity_source = _material(model)
    patch_by_semantic = {item.semantic_region: item.patch_name for item in poly.patches}
    try:
        inlet_patch, outlet_patch, wall_patch = (patch_by_semantic[inlet.semantic_region], patch_by_semantic[outlet.semantic_region], patch_by_semantic[wall.semantic_region])
    except KeyError as exc:
        raise OpenFOAMCaseError("PATCH_MAPPING_MISMATCH", "A CFD boundary has no exported polyMesh patch") from exc
    if len({inlet_patch, outlet_patch, wall_patch}) != 3 or set(patch_by_semantic) != {inlet.semantic_region, outlet.semantic_region, wall.semantic_region}:
        raise OpenFOAMCaseError("PATCH_MAPPING_MISMATCH", "CFD semantic boundaries must map one-to-one onto all patches")
    tolerance = model.numerical_settings.tolerance
    maximum_iterations = model.numerical_settings.maximum_iterations
    nu = viscosity / density
    pressure_kinematic = outlet.pressure_pa / density
    u = " ".join(f"{value:.17g}" for value in inlet.velocity_m_s)
    generated: dict[str, str] = {}
    generated["system/controlDict"] = _foam_header("dictionary", "system", "controlDict") + (
        f"solver incompressibleFluid;\nstartFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime {maximum_iterations};\n"
        "deltaT 1;\nwriteControl timeStep;\nwriteInterval 1;\npurgeWrite 2;\nwriteFormat ascii;\n"
        "writePrecision 17;\nwriteCompression off;\ntimeFormat general;\ntimePrecision 12;\nrunTimeModifiable false;\n"
    )
    generated["system/fvSchemes"] = _foam_header("dictionary", "system", "fvSchemes") + (
        "ddtSchemes { default steadyState; }\ngradSchemes { default Gauss linear; }\n"
        "divSchemes { default none; div(phi,U) bounded Gauss upwind; div((nuEff*dev2(T(grad(U))))) Gauss linear; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\ninterpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\nfluxRequired { default no; p; }\n"
    )
    generated["system/fvSolution"] = _foam_header("dictionary", "system", "fvSolution") + (
        "solvers\n{\n p { solver PCG; preconditioner DIC; tolerance 1e-10; relTol 0.05; maxIter 200; }\n"
        " U { solver PBiCGStab; preconditioner DILU; tolerance 1e-10; relTol 0.05; maxIter 200; }\n}\n"
        f"SIMPLE\n{{\n nNonOrthogonalCorrectors 2;\n residualControl {{ p {tolerance:.17g}; U {tolerance:.17g}; }}\n}}\n"
        "relaxationFactors { fields { p 0.3; } equations { U 0.7; } }\n"
    )
    generated["constant/physicalProperties"] = _foam_header("dictionary", "constant", "physicalProperties") + f"viscosityModel constant;\nnu {nu:.17g};\n"
    generated["constant/momentumTransport"] = _foam_header("dictionary", "constant", "momentumTransport") + "simulationType laminar;\n"
    generated["0/U"] = _foam_header("volVectorField", "0", "U") + (
        "dimensions [0 1 -1 0 0 0 0];\ninternalField uniform (0 0 0);\nboundaryField\n{\n"
        f" {inlet_patch} {{ type fixedValue; value uniform ({u}); }}\n"
        f" {outlet_patch} {{ type zeroGradient; }}\n {wall_patch} {{ type noSlip; }}\n}}\n"
    )
    generated["0/p"] = _foam_header("volScalarField", "0", "p") + (
        "dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 0;\nboundaryField\n{\n"
        f" {inlet_patch} {{ type zeroGradient; }}\n"
        f" {outlet_patch} {{ type fixedValue; value uniform {pressure_kinematic:.17g}; }}\n"
        f" {wall_patch} {{ type zeroGradient; }}\n}}\n"
    )
    if any(token in content for content in generated.values() for token in _DYNAMIC_TOKENS):
        raise OpenFOAMCaseError("DYNAMIC_CODE_FORBIDDEN", "Generated case contains a forbidden OpenFOAM directive")
    for relative, content in generated.items():
        target = case / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    science = {
        "generator": CASE_GENERATOR_VERSION,
        "solver": [SOLVER_ID, SOLVER_VERSION, BACKEND_ID, BACKEND_VERSION, "foamRun", "incompressibleFluid"],
        "mesh": [mesh.metadata.mesh_id, mesh.metadata.mesh_hash, poly.poly_mesh_hash],
        "physics_hash": model.physics_hash,
        "materials": [item.model_dump(mode="json") for item in model.materials],
        "boundaries": [item.model_dump(mode="json") for item in model.boundary_conditions],
        "settings": model.numerical_settings.model_dump(mode="json"),
        "patches": [asdict(item) for item in poly.patches],
        "files": generated,
    }
    return CFDCaseDefinition(_hash(science), density, viscosity, density_source, viscosity_source, nu,
        tuple(inlet.velocity_m_s), outlet.pressure_pa, inlet_patch, outlet_patch, wall_patch, tuple(sorted(generated)))


def prepare_laminar_case(mesh: GeneratedMesh, model: PhysicsModelV1, case: Path) -> tuple[PolyMeshExport, CFDCaseDefinition]:
    inlet, outlet, wall, _, _ = validate_cfd_scope(mesh, model)
    poly = export_poly_mesh(mesh, case, {inlet.semantic_region: "inlet", outlet.semantic_region: "outlet", wall.semantic_region: "wall"})
    return poly, generate_laminar_case(mesh, model, poly, case)


def _header_value(text: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}\s+([^;]+);", text)
    if not match:
        raise OpenFOAMCaseError("MALFORMED_FIELD", f"Missing OpenFOAM {key}")
    return match.group(1).strip()


def _dimensions(text: str) -> tuple[int, int, int, int, int, int, int]:
    raw = _header_value(text, "dimensions")
    values = tuple(int(value) for value in re.findall(r"[-+]?\d+", raw))
    if len(values) != 7:
        raise OpenFOAMCaseError("MALFORMED_FIELD", "Field dimensions must contain seven exponents")
    return values  # type: ignore[return-value]


def _internal_values(text: str, vector: bool, expected_count: int) -> list:
    uniform = re.search(r"\binternalField\s+uniform\s+(\([^;]+\)|[^;\s]+)\s*;", text)
    width = 3 if vector else 1
    if uniform:
        values = [float(value) for value in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", uniform.group(1))]
        if len(values) != width:
            raise OpenFOAMCaseError("MALFORMED_FIELD", "Uniform field value has the wrong width")
        item = tuple(values) if vector else values[0]
        return [item for _ in range(expected_count)]
    kind = "vector" if vector else "scalar"
    match = re.search(rf"\binternalField\s+nonuniform\s+List<{kind}>\s+(\d+)\s*\((.*?)\)\s*;", text, re.S)
    if not match or int(match.group(1)) != expected_count:
        raise OpenFOAMCaseError("FIELD_COUNT_MISMATCH", "Field internal count does not match the authoritative cell count")
    numbers = [float(value) for value in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", match.group(2))]
    if len(numbers) != expected_count * width:
        raise OpenFOAMCaseError("MALFORMED_FIELD", "Field value count is malformed")
    return [tuple(numbers[index:index + 3]) for index in range(0, len(numbers), 3)] if vector else numbers


def parse_volume_field(path: Path, name: Literal["U", "p"], expected_count: int) -> CFDFieldV1:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OpenFOAMCaseError("MALFORMED_FIELD", f"Cannot read {name} field") from exc
    expected_class = "volVectorField" if name == "U" else "volScalarField"
    expected_dimensions = _U_DIMENSIONS if name == "U" else _P_DIMENSIONS
    if _header_value(text, "class") != expected_class or _header_value(text, "object") != name:
        raise OpenFOAMCaseError("UNEXPECTED_FIELD", f"{name} has an unexpected class or object")
    dimensions = _dimensions(text)
    if dimensions != expected_dimensions:
        raise OpenFOAMCaseError("UNEXPECTED_FIELD_DIMENSIONS", f"{name} dimensions are not the reviewed incompressible dimensions")
    values = _internal_values(text, name == "U", expected_count)
    if not np.isfinite(np.asarray(values, dtype=float)).all():
        raise OpenFOAMCaseError("NONFINITE_FIELD", f"{name} contains nonfinite values")
    return CFDFieldV1(name=name, field_class=expected_class, dimensions=dimensions, unit="m/s" if name == "U" else "m2/s2 (kinematic pressure)", count=expected_count, values=values)


def _extract_braced(text: str, start: int) -> str:
    opening = text.find("{", start)
    if opening < 0:
        raise OpenFOAMCaseError("MALFORMED_FIELD", "Expected OpenFOAM dictionary block")
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{": depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0: return text[opening + 1:index]
    raise OpenFOAMCaseError("MALFORMED_FIELD", "Unclosed OpenFOAM dictionary block")


def parse_surface_flux(path: Path, patches: dict[str, int], internal_faces: int) -> ParsedSurfaceFlux:
    try: text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc: raise OpenFOAMCaseError("MALFORMED_FIELD", "Cannot read phi field") from exc
    if _header_value(text, "class") != "surfaceScalarField" or _header_value(text, "object") != "phi":
        raise OpenFOAMCaseError("UNEXPECTED_FIELD", "phi has an unexpected class or object")
    dimensions = _dimensions(text)
    if dimensions != _PHI_DIMENSIONS: raise OpenFOAMCaseError("UNEXPECTED_FIELD_DIMENSIONS", "phi is not volumetric surface flux")
    _internal_values(text, False, internal_faces)
    boundary = _extract_braced(text, text.find("boundaryField")); values: dict[str, tuple[float, ...]] = {}
    for patch, count in patches.items():
        match = re.search(rf"(?m)^\s*{re.escape(patch)}\s*$", boundary)
        if not match: raise OpenFOAMCaseError("FIELD_COUNT_MISMATCH", f"phi is missing patch {patch}")
        block = _extract_braced(boundary, match.end())
        parsed = _internal_values("internalField " + (_header_value(block, "value") if "uniform" in _header_value(block, "value") else _header_value(block, "value")) + ";", False, count)
        if len(parsed) != count or not np.isfinite(parsed).all(): raise OpenFOAMCaseError("NONFINITE_FIELD", "phi patch values are invalid")
        values[patch] = tuple(float(item) for item in parsed)
    if sum(len(items) for items in values.values()) + internal_faces <= 0: raise OpenFOAMCaseError("FIELD_COUNT_MISMATCH", "phi has no faces")
    return ParsedSurfaceFlux(dimensions, internal_faces, values)


def parse_residuals(log: str) -> tuple[int, float, float, bool]:
    pattern = re.compile(r"Solving for (Ux|Uy|Uz|p), Initial residual = ([0-9.eE+-]+), Final residual = ([0-9.eE+-]+), No Iterations (\d+)")
    records = [(name, float(initial), float(final), int(count)) for name, initial, final, count in pattern.findall(log)]
    if not records: raise OpenFOAMCaseError("RESIDUAL_PARSE_FAILED", "No reviewed OpenFOAM residual records were found")
    u = [item for item in records if item[0].startswith("U")]; p = [item for item in records if item[0] == "p"]
    if not u or not p: raise OpenFOAMCaseError("RESIDUAL_PARSE_FAILED", "Both U and p residuals are required")
    convergence = re.search(r"SIMPLE solution converged in (\d+) iterations", log)
    times = [int(float(value)) for value in re.findall(r"(?m)^Time = ([0-9.eE+-]+)$", log)]
    iterations = int(convergence.group(1)) if convergence else (max(times) if times else 0)
    if iterations <= 0: raise OpenFOAMCaseError("RESIDUAL_PARSE_FAILED", "Global iteration count is unavailable")
    # The final global nonlinear residual is the initial residual of the last linear solve.
    last_by_component = {name: initial for name, initial, _, _ in records}
    final_u = max(last_by_component[name] for name in last_by_component if name.startswith("U"))
    return iterations, final_u, last_by_component["p"], convergence is not None


def mass_flow_diagnostics(q_in: float, q_out: float, density: float) -> tuple[float, float, float]:
    if not all(math.isfinite(value) and value >= 0 for value in (q_in, q_out)) or not math.isfinite(density) or density <= 0:
        raise OpenFOAMCaseError("INVALID_FLOW_DIAGNOSTIC", "Flow rates and density must be finite and nonnegative/positive")
    mass_in, mass_out = density * q_in, density * q_out
    return mass_in, mass_out, abs(mass_in - mass_out) / max(abs(mass_in), abs(mass_out), 1e-30)


def _latest_time(case: Path) -> Path:
    times = [(float(path.name), path) for path in case.iterdir() if path.is_dir() and re.fullmatch(r"\d+(?:\.\d+)?", path.name) and float(path.name) > 0]
    if not times: raise OpenFOAMCaseError("SOLVER_OUTPUT_MISSING", "OpenFOAM wrote no completed time directory")
    return max(times, key=lambda item: item[0])[1]


def parse_cfd_solution(mesh: GeneratedMesh, model: PhysicsModelV1, poly: PolyMeshExport, definition: CFDCaseDefinition, case: Path, solver_log: str) -> CFDSolutionV1:
    final = _latest_time(case); cell_count = len(mesh.tetrahedra)
    u = parse_volume_field(final / "U", "U", cell_count); p = parse_volume_field(final / "p", "p", cell_count)
    patch_counts = {item.patch_name: len(item.boundary_facet_ids) for item in poly.patches}
    phi = parse_surface_flux(final / "phi", patch_counts, poly.internal_face_count)
    iterations, u_residual, p_residual, log_converged = parse_residuals(solver_log)
    q_in = -sum(phi.boundary_values[definition.inlet_patch]); q_out = sum(phi.boundary_values[definition.outlet_patch])
    if q_in < 0 or q_out < 0: raise OpenFOAMCaseError("FLOW_DIRECTION_INVALID", "Surface flux direction does not match inlet/outlet semantics")
    density = definition.density_kg_m3
    mass_in, mass_out, imbalance = mass_flow_diagnostics(q_in, q_out, density)
    velocity = np.asarray(u.values, dtype=float); speed = np.linalg.norm(velocity, axis=1)
    boundary_by_id = {index: tuple(sorted(face)) for index, face in enumerate(mesh.boundary_facets, 1)}
    mapping = {item.semantic_region: item for item in mesh.metadata.semantic_mappings}
    outlet_ids = set(mapping[next(item.semantic_region for item in poly.patches if item.patch_name == definition.outlet_patch)].boundary_facet_ids)
    inlet_ids = set(mapping[next(item.semantic_region for item in poly.patches if item.patch_name == definition.inlet_patch)].boundary_facet_ids)
    face_owner: dict[tuple[int, int, int], int] = {}
    for cell_id, cell in enumerate(mesh.tetrahedra):
        for local in ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)):
            key = tuple(sorted(cell[index] for index in local)); face_owner[key] = cell_id if key not in face_owner else -1
    outlet_cells = sorted({face_owner[boundary_by_id[index]] for index in outlet_ids}); inlet_cells = sorted({face_owner[boundary_by_id[index]] for index in inlet_ids})
    pressure = np.asarray(p.values, dtype=float); raw_drop = float(np.mean(pressure[inlet_cells]) - np.mean(pressure[outlet_cells]))
    area_out = 0.0; points = np.asarray(mesh.nodes_m, dtype=float)
    for facet_id in outlet_ids:
        a, b, c = points[list(boundary_by_id[facet_id])]; area_out += 0.5 * float(np.linalg.norm(np.cross(b - a, c - a)))
    mean_outlet = q_out / area_out
    tolerance = model.numerical_settings.tolerance
    converged = bool(log_converged and u_residual <= tolerance and p_residual <= tolerance and imbalance <= MASS_IMBALANCE_LIMIT)
    diagnostics = CFDDiagnosticsV1(final_u_residual=u_residual, final_p_residual=p_residual, volumetric_flow_in_m3_s=q_in, volumetric_flow_out_m3_s=q_out, mass_flow_in_kg_s=mass_in, mass_flow_out_kg_s=mass_out, normalized_mass_imbalance=imbalance)
    return CFDSolutionV1(
        mesh_id=mesh.metadata.mesh_id, mesh_hash=mesh.metadata.mesh_hash, poly_mesh_hash=poly.poly_mesh_hash,
        case_fingerprint=definition.case_fingerprint, converged=converged, iterations=iterations,
        summary_metrics={"max_velocity_m_s": float(speed.max()), "mean_velocity_m_s": float(speed.mean()), "mean_outlet_velocity_m_s": mean_outlet, "volumetric_flow_in_m3_s": q_in, "volumetric_flow_out_m3_s": q_out, "mass_flow_in_kg_s": mass_in, "mass_flow_out_kg_s": mass_out, "normalized_mass_imbalance": imbalance, "pressure_drop_raw_m2_s2": raw_drop, "physical_pressure_drop_pa": density * raw_drop},
        fields={"U": u, "p": p},
        flux=CFDFluxFieldV1(dimensions=phi.dimensions, internal_face_count=phi.internal_count,
            boundary_face_count=sum(len(items) for items in phi.boundary_values.values()),
            total_face_count=phi.internal_count + sum(len(items) for items in phi.boundary_values.values())),
        material=CFDMaterialV1(density_kg_m3=density, dynamic_viscosity_pa_s=definition.dynamic_viscosity_pa_s,
            density_source=definition.density_source, dynamic_viscosity_source=definition.dynamic_viscosity_source),
        pressure_interpretation=CFDPressureInterpretationV1(density_kg_m3=density, density_source=definition.density_source),
        diagnostics=diagnostics,
        warnings=["Analytical benchmark and mesh-refinement validation are pending Phase 3C-2B"],
        limitations=["Steady 3D incompressible Newtonian single-phase isothermal laminar internal flow only", "No turbulence, transient, compressible, multiphase, non-Newtonian, porous, rotating-frame, CHT, FSI, or combustion support"],
    )
