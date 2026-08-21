"""Square-duct analytical benchmark and retained pure-TET diagnostic bridge.

Production CFD authority is the certified finite-volume path in
``openfoam_fv_mesh``.  Pure-TET results must not be represented as validated.
"""
from __future__ import annotations

import math
import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2
from app.module2_simulation.geometry_physics_schemas import (
    MeshSpecification,
    PhysicsDomain,
    PhysicsModelRequest,
    PhysicsModelV1,
)
from app.module2_simulation.meshing import GeneratedMesh
from app.module2_simulation.openfoam_case import (
    BACKEND_ID,
    BACKEND_VERSION,
    MASS_IMBALANCE_LIMIT,
    SOLVER_ID,
    SOLVER_VERSION,
    CFDCaseDefinition,
    CFDSolutionV1,
    validate_cfd_scope,
)
from app.module2_simulation.openfoam_mesh import PolyMeshExport

BENCHMARK_ID = "cfd_square_duct_poiseuille_v1"
BENCHMARK_VERSION = "1.0.0"
ANALYTICAL_METHOD_VERSION = "rectangular-duct-odd-series-1.0"
DUCT_LENGTH_M = 0.50
DUCT_WIDTH_M = 0.02
DUCT_HEIGHT_M = 0.02
INLET_VELOCITY_M_S = (0.1, 0.0, 0.0)
OUTLET_PRESSURE_PA = 0.0
FIT_WINDOW = (0.75, 0.95)
FIT_R2_MINIMUM = 0.99
FINE_ERROR_LIMIT = 0.05
SERIES_TOLERANCE = 1e-14
SERIES_MAX_TERMS = 100_000
MESH_TARGETS_M = {"coarse": 0.005, "medium": 0.00375, "fine": 0.0025}
PURE_TET_CFD_VALIDATION_STATUS = "FAILED_NOT_PRODUCTION_AUTHORITY"


class CFDBenchmarkError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RectangularDuctSeriesV1(_Strict):
    correction: float = Field(gt=0, le=1)
    terms: int = Field(gt=0)
    tolerance: Literal[1e-14] = SERIES_TOLERANCE
    converged: Literal[True] = True


class PressureFitV1(_Strict):
    slope_pa_m: float
    intercept_pa: float
    gradient_pa_m: float = Field(gt=0)
    r_squared: float = Field(ge=0, le=1)
    cell_count: int = Field(ge=3)
    distinct_x_count: int = Field(ge=3)
    x_min_m: float
    x_max_m: float
    normalized_window: tuple[Literal[0.75], Literal[0.95]] = FIT_WINDOW


class CFDBenchmarkLevelV1(_Strict):
    level: Literal["coarse", "medium", "fine"]
    target_size_m: float
    mesh_id: str
    mesh_hash: str
    poly_mesh_hash: str
    case_fingerprint: str
    cell_count: int = Field(gt=0)
    boundary_face_count: int = Field(gt=0)
    analytical_gradient_pa_m: float = Field(gt=0)
    numerical_gradient_pa_m: float = Field(gt=0)
    normalized_pressure_gradient_error: float = Field(ge=0)
    pressure_fit: PressureFitV1
    normalized_mass_imbalance: float = Field(ge=0)
    final_u_residual: float = Field(ge=0)
    final_p_residual: float = Field(ge=0)
    solver_converged: bool
    passed_secondary_checks: bool


class CFDBenchmarkResultV1(_Strict):
    benchmark_id: Literal["cfd_square_duct_poiseuille_v1"] = BENCHMARK_ID
    benchmark_version: Literal["1.0.0"] = BENCHMARK_VERSION
    solver_id: Literal["cfd_openfoam_laminar_internal_3d_v1"] = SOLVER_ID
    solver_version: Literal["1.0.0"] = SOLVER_VERSION
    backend_id: Literal["openfoam-foundation-14"] = BACKEND_ID
    backend_version: Literal["20260724"] = BACKEND_VERSION
    geometry_dimensions_m: tuple[Literal[0.5], Literal[0.02], Literal[0.02]] = (DUCT_LENGTH_M, DUCT_WIDTH_M, DUCT_HEIGHT_M)
    material_snapshot_hash: str
    boundary_identity: str
    analytical_method: Literal["exact fully-developed rectangular-duct odd series"] = "exact fully-developed rectangular-duct odd series"
    analytical_method_version: Literal["rectangular-duct-odd-series-1.0"] = ANALYTICAL_METHOD_VERSION
    series: RectangularDuctSeriesV1
    volumetric_flow_m3_s: float = Field(gt=0)
    hydraulic_diameter_m: float = Field(gt=0)
    reynolds_number: float = Field(gt=0)
    entrance_length_screen_m: float = Field(gt=0)
    fit_start_from_inlet_m: float = Field(gt=0)
    fit_start_over_hydraulic_diameter: float = Field(gt=0)
    fit_start_over_entrance_screen: float = Field(gt=0)
    laminar_valid: bool
    levels: tuple[CFDBenchmarkLevelV1, CFDBenchmarkLevelV1, CFDBenchmarkLevelV1]
    errors: tuple[float, float, float]
    monotonic_error_reduction: bool
    observed_order: float | None
    fine_error_limit: Literal[0.05] = FINE_ERROR_LIMIT
    passed: bool
    diagnostics: list[str]
    limitations: list[str]


class CFDFVAccuracyGateV1(_Strict):
    benchmark_id: Literal["cfd_square_duct_poiseuille_v1"] = BENCHMARK_ID
    mesh_id: str
    mesh_hash: str
    cell_count: int = Field(gt=0)
    analytical_gradient_pa_m: float = Field(gt=0)
    numerical_gradient_pa_m: float = Field(gt=0)
    normalized_pressure_gradient_error: float = Field(ge=0)
    pressure_r_squared: float = Field(ge=0, le=1)
    fit_cell_count: int = Field(ge=20)
    mean_velocity_m_s: float = Field(gt=0)
    maximum_velocity_m_s: float = Field(gt=0)
    maximum_over_mean_velocity: float = Field(gt=0)
    normalized_mass_imbalance: float = Field(ge=0)
    final_u_residual: float = Field(ge=0)
    final_p_residual: float = Field(ge=0)
    passed: bool


@dataclass(frozen=True)
class BenchmarkGeometry:
    axis: np.ndarray
    x_min_m: float
    x_max_m: float
    length_m: float
    width_m: float
    height_m: float
    inlet_area_m2: float
    hydraulic_diameter_m: float
    reynolds_number: float
    entrance_length_screen_m: float
    fit_start_from_inlet_m: float


def benchmark_design() -> EngineeringDesignDocumentV2:
    q = lambda value: {"value": value, "unit": "mm"}
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": BENCHMARK_ID,
        "bodies": [{"body_id": "duct_fluid", "material": "air"}],
        "sketches": [{"sketch_id": "duct_section", "entities": [{
            "entity_type": "rectangle", "entity_id": "section", "width": q(500), "height": q(20),
        }]}],
        "features": [{"operation": "extrude", "feature_id": "duct_volume", "sketch_id": "duct_section",
            "output_body": "duct_fluid", "distance": q(20)}],
        "output_body_ids": ["duct_fluid"],
        "semantic_regions": [
            {"tag": "low_end", "body_id": "duct_fluid", "selector": {"selector_type": "extreme_face", "axis": "x", "extreme": "minimum"}},
            {"tag": "high_end", "body_id": "duct_fluid", "selector": {"selector_type": "extreme_face", "axis": "x", "extreme": "maximum"}},
            {"tag": "walls", "body_id": "duct_fluid", "selector": "side_faces"},
        ],
    })


def benchmark_domain() -> PhysicsDomain:
    return PhysicsDomain(domain_id="benchmark_fluid", source_body_id="duct_fluid", domain_kind="fluid", explicit_fluid_volume=True)


def benchmark_mesh_specification(level: Literal["coarse", "medium", "fine"]) -> MeshSpecification:
    return MeshSpecification(target_size={"value": MESH_TARGETS_M[level] * 1000, "unit": "mm"}, refinement_level=level)


def benchmark_physics_request() -> PhysicsModelRequest:
    domain = benchmark_domain()
    return PhysicsModelRequest.model_validate({
        "analysis_family": "cfd", "domains": [domain.model_dump(mode="json")],
        "material_assignments": [{"domain_id": domain.domain_id, "material_name": "air"}],
        "boundary_conditions": [
            {"bc_type": "velocity_inlet", "bc_id": "benchmark_inlet", "semantic_region": "low_end", "velocity_m_s": INLET_VELOCITY_M_S},
            {"bc_type": "pressure_boundary", "bc_id": "benchmark_outlet", "semantic_region": "high_end", "pressure_pa": OUTLET_PRESSURE_PA},
            {"bc_type": "wall", "bc_id": "benchmark_walls", "semantic_region": "walls", "no_slip": True},
        ],
        "numerical_settings": {"settings_type": "steady_flow", "tolerance": 1e-6, "maximum_iterations": 2000},
        "expected_outputs": ["velocity", "pressure", "mass_flow"],
    })


def rectangular_duct_series(width_m: float, height_m: float, *, tolerance: float = SERIES_TOLERANCE, maximum_terms: int = SERIES_MAX_TERMS) -> RectangularDuctSeriesV1:
    if not all(math.isfinite(value) and value > 0 for value in (width_m, height_m, tolerance)) or height_m > width_m or maximum_terms < 1:
        raise CFDBenchmarkError("INVALID_ANALYTICAL_INPUT", "Require finite positive h <= w, tolerance, and term bound")
    total = 0.0; prefactor = 192.0 * height_m / (math.pi ** 5 * width_m)
    for index in range(maximum_terms):
        n = 2 * index + 1
        contribution = math.tanh(n * math.pi * width_m / (2.0 * height_m)) / n ** 5
        total += contribution
        if prefactor * abs(contribution) <= tolerance:
            correction = 1.0 - prefactor * total
            if not 0 < correction <= 1: raise CFDBenchmarkError("ANALYTICAL_SERIES_INVALID", "Rectangular-duct correction is nonphysical")
            return RectangularDuctSeriesV1(correction=correction, terms=index + 1, tolerance=SERIES_TOLERANCE)
    raise CFDBenchmarkError("ANALYTICAL_SERIES_NOT_CONVERGED", "Rectangular-duct odd series exceeded its fixed term bound")


def analytical_pressure_gradient(width_m: float, height_m: float, dynamic_viscosity_pa_s: float, volumetric_flow_m3_s: float) -> tuple[float, RectangularDuctSeriesV1]:
    if not all(math.isfinite(value) and value > 0 for value in (dynamic_viscosity_pa_s, volumetric_flow_m3_s)):
        raise CFDBenchmarkError("INVALID_ANALYTICAL_INPUT", "Viscosity and volumetric flow must be finite and positive")
    series = rectangular_duct_series(width_m, height_m)
    gradient = 12.0 * dynamic_viscosity_pa_s * volumetric_flow_m3_s / (width_m * height_m ** 3 * series.correction)
    return gradient, series


def _facet_centroid(mesh: GeneratedMesh, facet_id: int) -> np.ndarray:
    return np.asarray(mesh.nodes_m, dtype=float)[list(mesh.boundary_facets[facet_id - 1])].mean(axis=0)


def validate_benchmark_identity(mesh: GeneratedMesh, model: PhysicsModelV1, definition: CFDCaseDefinition | None = None) -> BenchmarkGeometry:
    inlet, outlet, wall, density, viscosity = validate_cfd_scope(mesh, model)
    if (inlet.semantic_region, outlet.semantic_region, wall.semantic_region) != ("low_end", "high_end", "walls"):
        raise CFDBenchmarkError("BENCHMARK_BOUNDARY_MISMATCH", "Benchmark semantic boundaries are fixed server-side")
    if tuple(inlet.velocity_m_s) != INLET_VELOCITY_M_S or outlet.pressure_pa != OUTLET_PRESSURE_PA or not wall.no_slip:
        raise CFDBenchmarkError("BENCHMARK_BOUNDARY_MISMATCH", "Benchmark boundary values were changed")
    if model.materials[0].material_name.lower() != "air" or density != 1.204 or viscosity != 1.81e-5:
        raise CFDBenchmarkError("BENCHMARK_MATERIAL_MISMATCH", "Benchmark requires the authoritative air snapshot")
    mappings = {item.semantic_region: item for item in mesh.metadata.semantic_mappings}
    if set(mappings) != {"low_end", "high_end", "walls"}:
        raise CFDBenchmarkError("BENCHMARK_BOUNDARY_MISMATCH", "Benchmark requires exactly inlet, outlet, and walls")
    inlet_points = np.asarray([_facet_centroid(mesh, item) for item in mappings["low_end"].boundary_facet_ids])
    outlet_points = np.asarray([_facet_centroid(mesh, item) for item in mappings["high_end"].boundary_facet_ids])
    axis_vector = outlet_points.mean(axis=0) - inlet_points.mean(axis=0); length = float(np.linalg.norm(axis_vector))
    if length <= 0: raise CFDBenchmarkError("BENCHMARK_GEOMETRY_MISMATCH", "Inlet and outlet planes are not distinct")
    axis = axis_vector / length; points = np.asarray(mesh.nodes_m, dtype=float); projected = points @ axis
    x_min, x_max = float(projected.min()), float(projected.max()); extents = np.ptp(points, axis=0)
    sorted_cross = sorted(float(value) for value in extents if value > 1e-12)[:2]
    if len(sorted_cross) != 2:
        raise CFDBenchmarkError("BENCHMARK_GEOMETRY_MISMATCH", "Duct cross-section cannot be derived")
    width, height = max(sorted_cross), min(sorted_cross)
    expected = (DUCT_LENGTH_M, DUCT_WIDTH_M, DUCT_HEIGHT_M)
    if not (math.isclose(x_max - x_min, expected[0], abs_tol=1e-10) and math.isclose(width, expected[1], abs_tol=1e-10) and math.isclose(height, expected[2], abs_tol=1e-10)):
        raise CFDBenchmarkError("BENCHMARK_GEOMETRY_MISMATCH", "Mesh does not represent the server-owned 0.50 x 0.02 x 0.02 m duct")
    inlet_projection = inlet_points @ axis; outlet_projection = outlet_points @ axis
    if np.ptp(inlet_projection) > 1e-10 or np.ptp(outlet_projection) > 1e-10 or not math.isclose(float(inlet_projection.mean()), x_min, abs_tol=1e-10) or not math.isclose(float(outlet_projection.mean()), x_max, abs_tol=1e-10):
        raise CFDBenchmarkError("BENCHMARK_PLANE_MISMATCH", "Inlet/outlet facets are not the opposite duct planes")
    wall_ids = set(mappings["walls"].boundary_facet_ids)
    if wall_ids.intersection(mappings["low_end"].boundary_facet_ids) or wall_ids.intersection(mappings["high_end"].boundary_facet_ids):
        raise CFDBenchmarkError("BENCHMARK_BOUNDARY_MISMATCH", "Wall facets overlap inlet/outlet")
    if definition and (definition.inlet_velocity_m_s != INLET_VELOCITY_M_S or definition.density_kg_m3 != density or definition.dynamic_viscosity_pa_s != viscosity):
        raise CFDBenchmarkError("BENCHMARK_CASE_MISMATCH", "Generated case does not preserve benchmark science")
    area = width * height; hydraulic = 4.0 * area / (2.0 * (width + height))
    mean_velocity = abs(float(np.dot(inlet.velocity_m_s, axis)))
    reynolds = density * mean_velocity * hydraulic / viscosity
    entrance = 0.05 * reynolds * hydraulic; fit_start = FIT_WINDOW[0] * (x_max - x_min)
    if fit_start < 15.0 * hydraulic or fit_start < 2.0 * entrance:
        raise CFDBenchmarkError("BENCHMARK_CONFIGURATION_INVALID", "Fixed pressure-fit window fails the developed-length screen")
    return BenchmarkGeometry(axis, x_min, x_max, x_max - x_min, width, height, area,
        hydraulic, reynolds, entrance, fit_start)


def volume_weighted_pressure_fit(mesh: GeneratedMesh, pressure_pa: Sequence[float], geometry: BenchmarkGeometry) -> PressureFitV1:
    values = np.asarray(pressure_pa, dtype=float)
    if values.shape != (len(mesh.tetrahedra),) or not np.isfinite(values).all():
        raise CFDBenchmarkError("MALFORMED_PRESSURE_FIELD", "Pressure must be one finite cell-centered value per tetrahedron")
    points = np.asarray(mesh.nodes_m, dtype=float); centroids=[]; volumes=[]
    for cell in mesh.tetrahedra:
        vertices = points[list(cell)]; centroids.append(float(vertices.mean(axis=0) @ geometry.axis))
        volumes.append(abs(float(np.linalg.det(np.stack((vertices[1]-vertices[0], vertices[2]-vertices[0], vertices[3]-vertices[0]))))) / 6.0)
    x = np.asarray(centroids); weight = np.asarray(volumes); normalized = (x - geometry.x_min_m) / geometry.length_m
    selected = (normalized >= FIT_WINDOW[0]) & (normalized <= FIT_WINDOW[1]); x = x[selected]; y = values[selected]; weight = weight[selected]
    distinct = len(np.unique(np.round(x, 14)))
    if len(x) < 20 or distinct < 4 or not np.all(weight > 0):
        raise CFDBenchmarkError("INSUFFICIENT_PRESSURE_FIT", "Developed-flow window has insufficient distinct cells")
    design = np.column_stack((np.ones(len(x)), x)); weighted = design * np.sqrt(weight)[:, None]
    coefficients, _, rank, _ = np.linalg.lstsq(weighted, y * np.sqrt(weight), rcond=None)
    if rank != 2: raise CFDBenchmarkError("INSUFFICIENT_PRESSURE_FIT", "Pressure fit is rank deficient")
    predicted = design @ coefficients; mean = float(np.average(y, weights=weight))
    residual = float(np.sum(weight * (y - predicted) ** 2)); total = float(np.sum(weight * (y - mean) ** 2))
    if total <= 0: raise CFDBenchmarkError("NONLINEAR_PRESSURE_FIT", "Pressure fit has zero variance")
    r_squared = 1.0 - residual / total
    if not math.isfinite(r_squared) or r_squared < FIT_R2_MINIMUM:
        raise CFDBenchmarkError("NONLINEAR_PRESSURE_FIT", f"Pressure-fit R2 {r_squared} is below {FIT_R2_MINIMUM}")
    slope = float(coefficients[1]); gradient = abs(slope)
    if not math.isfinite(gradient) or gradient <= 0: raise CFDBenchmarkError("NONLINEAR_PRESSURE_FIT", "Pressure gradient is nonphysical")
    return PressureFitV1(slope_pa_m=slope, intercept_pa=float(coefficients[0]), gradient_pa_m=gradient,
        r_squared=r_squared, cell_count=len(x), distinct_x_count=distinct, x_min_m=float(x.min()), x_max_m=float(x.max()))


def evaluate_benchmark_level(level: Literal["coarse", "medium", "fine"], mesh: GeneratedMesh, model: PhysicsModelV1, poly: PolyMeshExport, definition: CFDCaseDefinition, solution: CFDSolutionV1) -> tuple[CFDBenchmarkLevelV1, RectangularDuctSeriesV1, float, float]:
    geometry = validate_benchmark_identity(mesh, model, definition)
    quantity = mesh.metadata.specification.target_size
    target_m = quantity.value * ({"m": 1.0, "mm": 1e-3, "cm": 1e-2, "um": 1e-6, "in": 0.0254}[quantity.unit.value])
    if not math.isclose(target_m, MESH_TARGETS_M[level], rel_tol=0, abs_tol=1e-14):
        raise CFDBenchmarkError("BENCHMARK_MESH_SEQUENCE_MISMATCH", "Mesh target is not the fixed benchmark refinement level")
    if solution.solver_id != SOLVER_ID or solution.solver_version != SOLVER_VERSION or solution.backend_id != BACKEND_ID or solution.backend_version != BACKEND_VERSION:
        raise CFDBenchmarkError("BENCHMARK_SOLVER_MISMATCH", "Result solver/backend identity is not the reviewed CFD implementation")
    if solution.mesh_id != mesh.metadata.mesh_id or solution.mesh_hash != mesh.metadata.mesh_hash or solution.poly_mesh_hash != poly.poly_mesh_hash or solution.case_fingerprint != definition.case_fingerprint:
        raise CFDBenchmarkError("BENCHMARK_SOURCE_MISMATCH", "Result is not bound to this benchmark mesh/case")
    density = definition.density_kg_m3; pressure_pa = density * np.asarray(solution.fields["p"].values, dtype=float)
    fit = volume_weighted_pressure_fit(mesh, pressure_pa, geometry)
    flow = abs(float(np.dot(definition.inlet_velocity_m_s, geometry.axis))) * geometry.inlet_area_m2
    analytical, series = analytical_pressure_gradient(geometry.width_m, geometry.height_m, definition.dynamic_viscosity_pa_s, flow)
    error = abs(fit.gradient_pa_m - analytical) / abs(analytical)
    secondary = bool(solution.converged and solution.diagnostics.normalized_mass_imbalance <= MASS_IMBALANCE_LIMIT and fit.r_squared >= FIT_R2_MINIMUM and solution.fields["U"].finite and solution.fields["p"].finite and solution.flux.finite)
    if not secondary: raise CFDBenchmarkError("BENCHMARK_SECONDARY_CHECK_FAILED", "Solver, fields, pressure fit, or mass conservation failed")
    result = CFDBenchmarkLevelV1(level=level, target_size_m=MESH_TARGETS_M[level], mesh_id=mesh.metadata.mesh_id,
        mesh_hash=mesh.metadata.mesh_hash, poly_mesh_hash=poly.poly_mesh_hash, case_fingerprint=definition.case_fingerprint,
        cell_count=poly.cell_count, boundary_face_count=poly.boundary_face_count, analytical_gradient_pa_m=analytical,
        numerical_gradient_pa_m=fit.gradient_pa_m, normalized_pressure_gradient_error=error, pressure_fit=fit,
        normalized_mass_imbalance=solution.diagnostics.normalized_mass_imbalance,
        final_u_residual=solution.diagnostics.final_u_residual, final_p_residual=solution.diagnostics.final_p_residual,
        solver_converged=solution.converged, passed_secondary_checks=secondary)
    return result, series, flow, geometry.reynolds_number


def assert_identical_science(models: Sequence[PhysicsModelV1], definitions: Sequence[CFDCaseDefinition]) -> None:
    if len(models) != 3 or len(definitions) != 3: raise CFDBenchmarkError("REFINEMENT_LEVEL_MISMATCH", "Exactly three levels are required")
    identities = [{"design_hash": model.design_hash, "geometry": model.geometry_fingerprint,
        "materials": [item.model_dump(mode="json") for item in model.materials],
        "assignments": [item.model_dump(mode="json") for item in model.material_assignments],
        "boundaries": [item.model_dump(mode="json") for item in model.boundary_conditions],
        "settings": model.numerical_settings.model_dump(mode="json"), "outputs": model.expected_outputs,
        "solver": (SOLVER_ID, SOLVER_VERSION, BACKEND_ID, BACKEND_VERSION),
        "benchmark": (BENCHMARK_ID, BENCHMARK_VERSION, ANALYTICAL_METHOD_VERSION, FIT_WINDOW)} for model in models]
    if any(identity != identities[0] for identity in identities[1:]):
        raise CFDBenchmarkError("REFINEMENT_SCIENCE_MISMATCH", "Only mesh discretization may change across refinement levels")
    case_science = [(item.density_kg_m3, item.dynamic_viscosity_pa_s, item.inlet_velocity_m_s, item.outlet_pressure_pa) for item in definitions]
    if any(identity != case_science[0] for identity in case_science[1:]):
        raise CFDBenchmarkError("REFINEMENT_SCIENCE_MISMATCH", "Generated case science differs across levels")


def assemble_benchmark_result(levels: Sequence[CFDBenchmarkLevelV1], models: Sequence[PhysicsModelV1], definitions: Sequence[CFDCaseDefinition], series: RectangularDuctSeriesV1, flow: float, reynolds: float) -> CFDBenchmarkResultV1:
    if [item.level for item in levels] != ["coarse", "medium", "fine"]: raise CFDBenchmarkError("REFINEMENT_LEVEL_MISMATCH", "Levels must be coarse, medium, fine")
    assert_identical_science(models, definitions)
    if reynolds >= 2000: raise CFDBenchmarkError("LAMINAR_VALIDITY_FAILED", "Hydraulic-diameter Reynolds number is outside the bounded laminar regime")
    errors = tuple(item.normalized_pressure_gradient_error for item in levels); monotonic = errors[0] > errors[1] > errors[2]
    fine_pass = errors[2] <= FINE_ERROR_LIMIT and errors[2] < errors[0] and all(item.passed_secondary_checks for item in levels)
    order = None
    h = [item.target_size_m for item in levels]
    if monotonic and all(value > 0 for value in errors):
        ratios = [h[0] / h[1], h[1] / h[2]]
        orders = [math.log(errors[0] / errors[1]) / math.log(ratios[0]), math.log(errors[1] / errors[2]) / math.log(ratios[1])]
        if all(math.isfinite(value) and value > 0 for value in orders): order = float(sum(orders) / 2.0)
    material_hash = models[0].materials[0].snapshot_hash
    boundary_identity = hashlib.sha256(json.dumps([item.model_dump(mode="json") for item in models[0].boundary_conditions],
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    geometry = validate_benchmark_identity_from_level(levels[0], definitions[0], reynolds)
    diagnostics = ["REFINEMENT_MONOTONIC" if monotonic else f"NONMONOTONIC_ERRORS:{errors}", "FINE_BENCHMARK_PASS" if fine_pass else "FINE_BENCHMARK_FAIL"]
    return CFDBenchmarkResultV1(material_snapshot_hash=material_hash, boundary_identity=boundary_identity,
        series=series, volumetric_flow_m3_s=flow, hydraulic_diameter_m=geometry[0], reynolds_number=reynolds,
        entrance_length_screen_m=geometry[1], fit_start_from_inlet_m=geometry[2],
        fit_start_over_hydraulic_diameter=geometry[2] / geometry[0], fit_start_over_entrance_screen=geometry[2] / geometry[1],
        laminar_valid=True, levels=tuple(levels), errors=errors, monotonic_error_reduction=monotonic,
        observed_order=order, passed=fine_pass, diagnostics=diagnostics,
        limitations=["Pure-TET CFD analytical validation failed and this result is not production authority", "Validation is restricted to the server-owned square duct and declared developed-flow fit window", "Evidence/trust persistence is intentionally deferred to Phase 3C-2B.2"])


def validate_benchmark_identity_from_level(level: CFDBenchmarkLevelV1, definition: CFDCaseDefinition, reynolds: float) -> tuple[float, float, float]:
    """Recover only immutable server-owned screening values for typed aggregation."""
    if level.target_size_m not in MESH_TARGETS_M.values() or definition.inlet_velocity_m_s != INLET_VELOCITY_M_S:
        raise CFDBenchmarkError("BENCHMARK_CONFIGURATION_INVALID", "Benchmark level/case identity changed")
    hydraulic = 4.0 * (DUCT_WIDTH_M * DUCT_HEIGHT_M) / (2.0 * (DUCT_WIDTH_M + DUCT_HEIGHT_M))
    entrance = 0.05 * reynolds * hydraulic; fit_start = FIT_WINDOW[0] * DUCT_LENGTH_M
    if fit_start < 15 * hydraulic or fit_start < 2 * entrance:
        raise CFDBenchmarkError("BENCHMARK_CONFIGURATION_INVALID", "Fixed pressure-fit window fails the developed-length screen")
    return hydraulic, entrance, fit_start


def evaluate_certified_fv_accuracy_gate(case, mesh, definition: CFDCaseDefinition, solution: CFDSolutionV1) -> CFDFVAccuracyGateV1:
    """Evaluate the fixed one-mesh analytical gate on a certified FV polyMesh."""
    from app.module2_simulation.openfoam_fv_mesh import CFDGeneratedMeshV1, read_fv_cell_geometry

    if not isinstance(mesh, CFDGeneratedMeshV1) or solution.mesh_id != mesh.mesh_id or solution.mesh_hash != mesh.mesh_hash:
        raise CFDBenchmarkError("BENCHMARK_SOURCE_MISMATCH", "Result is not bound to the certified benchmark FV mesh")
    centers, volumes = read_fv_cell_geometry(case)
    x = centers[:, 0]; normalized = (x + DUCT_LENGTH_M / 2.0) / DUCT_LENGTH_M
    selected = (normalized >= FIT_WINDOW[0]) & (normalized <= FIT_WINDOW[1])
    if int(selected.sum()) < 20:
        raise CFDBenchmarkError("INSUFFICIENT_PRESSURE_FIT", "Certified FV fit window has insufficient cells")
    pressure = definition.density_kg_m3 * np.asarray(solution.fields["p"].values, dtype=float)
    design = np.column_stack((np.ones(int(selected.sum())), x[selected])); weight = volumes[selected]
    coefficients, _, rank, _ = np.linalg.lstsq(design * np.sqrt(weight)[:, None], pressure[selected] * np.sqrt(weight), rcond=None)
    if rank != 2: raise CFDBenchmarkError("INSUFFICIENT_PRESSURE_FIT", "Certified FV pressure fit is rank deficient")
    predicted = design @ coefficients; mean_p = float(np.average(pressure[selected], weights=weight))
    total = float(np.sum(weight * (pressure[selected] - mean_p) ** 2))
    r_squared = 1.0 - float(np.sum(weight * (pressure[selected] - predicted) ** 2)) / total
    numerical = abs(float(coefficients[1])); flow = abs(definition.inlet_velocity_m_s[0]) * DUCT_WIDTH_M * DUCT_HEIGHT_M
    analytical, _ = analytical_pressure_gradient(DUCT_WIDTH_M, DUCT_HEIGHT_M, definition.dynamic_viscosity_pa_s, flow)
    error = abs(numerical - analytical) / analytical
    profile = (normalized >= 0.79) & (normalized <= 0.81)
    velocity = np.linalg.norm(np.asarray(solution.fields["U"].values, dtype=float)[profile], axis=1)
    mean_velocity = float(np.average(velocity, weights=volumes[profile])); maximum_velocity = float(velocity.max())
    passed = bool(solution.converged and error <= FINE_ERROR_LIMIT and r_squared >= FIT_R2_MINIMUM and solution.diagnostics.normalized_mass_imbalance <= MASS_IMBALANCE_LIMIT)
    return CFDFVAccuracyGateV1(
        mesh_id=mesh.mesh_id, mesh_hash=mesh.mesh_hash, cell_count=mesh.cell_count,
        analytical_gradient_pa_m=analytical, numerical_gradient_pa_m=numerical,
        normalized_pressure_gradient_error=error, pressure_r_squared=r_squared, fit_cell_count=int(selected.sum()),
        mean_velocity_m_s=mean_velocity, maximum_velocity_m_s=maximum_velocity,
        maximum_over_mean_velocity=maximum_velocity / mean_velocity,
        normalized_mass_imbalance=solution.diagnostics.normalized_mass_imbalance,
        final_u_residual=solution.diagnostics.final_u_residual, final_p_residual=solution.diagnostics.final_p_residual,
        passed=passed,
    )
