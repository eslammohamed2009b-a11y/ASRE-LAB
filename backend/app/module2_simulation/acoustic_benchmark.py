"""Server-owned analytical gate for geometry-aware 3D acoustic FEM."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.module1_design.cad_v2_compiler import CompiledDesign, compile_design
from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2
from app.module2_simulation.acoustic_fem_solvers import (
    MIN_RECIPROCAL_CONDITION_ESTIMATE,
    SOLVER_ID,
    AcousticFEMSolutionV1,
    solve_acoustic_fem_3d,
)
from app.module2_simulation.geometry_physics_schemas import (
    PhysicsDomain,
    PhysicsModelRequest,
    PhysicsModelV1,
    MeshSpecification,
)
from app.module2_simulation.meshing import GeneratedMesh, generate_mesh
from app.module2_simulation.physics_model import build_physics_model


BENCHMARK_ID = "acoustic_rectangular_duct_plane_wave_v1"
BENCHMARK_VERSION = "1.0.0"
LENGTH_M = 1.0
WIDTH_M = 0.05
HEIGHT_M = 0.05
FREQUENCY_HZ = 100.0
PRESSURE_PA = 1.0
MESH_TARGET_SIZE_MM = 10.0
COMPLEX_L2_ERROR_LIMIT = 0.05
REFINEMENT_TARGETS_MM = {"coarse": 20.0, "medium": 15.0, "fine": 10.0}
MIN_OBSERVED_ORDER = 0.5


class AcousticBenchmarkError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AcousticBenchmarkResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_id: Literal["acoustic_rectangular_duct_plane_wave_v1"] = BENCHMARK_ID
    benchmark_version: Literal["1.0.0"] = BENCHMARK_VERSION
    solver_id: Literal["acoustic_helmholtz_fem_3d_v1"] = SOLVER_ID
    design_hash: str
    geometry_fingerprint: str
    mesh_id: str
    mesh_hash: str
    physics_model_id: str
    physics_hash: str
    material_name: str
    material_snapshot_hash: str
    speed_of_sound_m_s: float = Field(gt=0)
    nodes: int = Field(gt=0)
    tetrahedra: int = Field(gt=0)
    frequency_hz: float = FREQUENCY_HZ
    wavelength_m: float = Field(gt=0)
    h_max: float = Field(gt=0)
    k_h_max: float = Field(ge=0)
    normalized_algebraic_residual: float = Field(ge=0)
    reciprocal_condition_estimate: float = Field(gt=0, le=1)
    conditioning_passed: bool
    normalized_complex_l2_error: float = Field(ge=0)
    max_pressure_amplitude_pa: float = Field(ge=0)
    mean_pressure_amplitude_pa: float = Field(ge=0)
    max_spl_db: float | None
    passed: bool


class AcousticRefinementLevelV1(AcousticBenchmarkResultV1):
    level: Literal["coarse", "medium", "fine"]
    target_size_mm: float = Field(gt=0)
    finite_fields: bool


class AcousticRefinementResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_id: Literal["acoustic_rectangular_duct_plane_wave_v1"] = BENCHMARK_ID
    benchmark_version: Literal["1.0.0"] = BENCHMARK_VERSION
    solver_id: Literal["acoustic_helmholtz_fem_3d_v1"] = SOLVER_ID
    solver_version: Literal["1.0.0"] = "1.0.0"
    design_hash: str
    geometry_fingerprint: str
    material_snapshot_hash: str
    boundary_identity: str
    frequency_hz: float = FREQUENCY_HZ
    levels: tuple[AcousticRefinementLevelV1, AcousticRefinementLevelV1, AcousticRefinementLevelV1]
    refinement_monotonic: bool
    observed_order: float | None
    minimum_observed_order: float = MIN_OBSERVED_ORDER
    coarse_to_fine_improvement_ratio: float = Field(gt=0)
    fine_error_limit: float = COMPLEX_L2_ERROR_LIMIT
    passed: bool


def _q(value: float) -> dict[str, float | str]:
    return {"value": value, "unit": "mm"}


def benchmark_design() -> EngineeringDesignDocumentV2:
    return EngineeringDesignDocumentV2.model_validate({
        "document_id": BENCHMARK_ID,
        "bodies": [{"body_id": "acoustic_duct", "material": "air"}],
        "sketches": [{"sketch_id": "duct_section", "entities": [{
            "entity_type": "rectangle", "entity_id": "section", "width": _q(1000), "height": _q(50),
        }]}],
        "features": [{"operation": "extrude", "feature_id": "duct_volume", "sketch_id": "duct_section",
                       "output_body": "acoustic_duct", "distance": _q(50)}],
        "output_body_ids": ["acoustic_duct"],
        "semantic_regions": [
            {"tag": "inlet", "body_id": "acoustic_duct", "selector": {"selector_type": "extreme_face", "axis": "x", "extreme": "minimum"}},
            {"tag": "outlet", "body_id": "acoustic_duct", "selector": {"selector_type": "extreme_face", "axis": "x", "extreme": "maximum"}},
            {"tag": "walls", "body_id": "acoustic_duct", "selector": "side_faces"},
        ],
    })


def benchmark_domain() -> PhysicsDomain:
    return PhysicsDomain(domain_id="acoustic_domain", source_body_id="acoustic_duct", domain_kind="acoustic")


def benchmark_mesh_specification(target_size_mm: float = MESH_TARGET_SIZE_MM) -> MeshSpecification:
    return MeshSpecification(target_size=_q(target_size_mm), refinement_level="custom")


def benchmark_physics_request() -> PhysicsModelRequest:
    domain = benchmark_domain()
    return PhysicsModelRequest.model_validate({
        "analysis_family": "acoustics",
        "domains": [domain.model_dump(mode="json")],
        "material_assignments": [{"domain_id": domain.domain_id, "material_name": "air"}],
        "boundary_conditions": [
            {"bc_type": "acoustic_pressure", "bc_id": "benchmark_inlet", "semantic_region": "inlet", "pressure_pa": PRESSURE_PA},
            {"bc_type": "acoustic_wall", "bc_id": "benchmark_walls", "semantic_region": "walls", "condition": "rigid"},
            {"bc_type": "acoustic_wall", "bc_id": "benchmark_outlet", "semantic_region": "outlet", "condition": "pressure_release"},
        ],
        "numerical_settings": {"settings_type": "harmonic_acoustic", "frequency_hz": FREQUENCY_HZ},
        "expected_outputs": ["acoustic_pressure", "sound_pressure_level"],
    })


def benchmark_mesh(compiled: CompiledDesign | None = None, target_size_mm: float = MESH_TARGET_SIZE_MM) -> GeneratedMesh:
    compiled = compiled or compile_design(benchmark_design())
    return generate_mesh(compiled, [benchmark_domain()], benchmark_mesh_specification(target_size_mm))


def benchmark_model(mesh: GeneratedMesh) -> PhysicsModelV1:
    return build_physics_model(mesh, benchmark_physics_request())


def authoritative_speed_of_sound(model: PhysicsModelV1) -> float:
    if len(model.materials) != 1 or model.materials[0].material_name.lower() != "air":
        raise AcousticBenchmarkError("BENCHMARK_MATERIAL_MISMATCH", "Benchmark requires the authoritative air snapshot")
    item = next((value for value in model.materials[0].properties if value.name == "speed_of_sound"), None)
    if item is None or not math.isfinite(item.value) or item.value <= 0:
        raise AcousticBenchmarkError("BENCHMARK_MATERIAL_MISMATCH", "Authoritative benchmark material lacks speed_of_sound")
    return float(item.value)


def validate_benchmark_identity(compiled: CompiledDesign, mesh: GeneratedMesh, model: PhysicsModelV1) -> None:
    if compiled.document.document_id != BENCHMARK_ID:
        raise AcousticBenchmarkError("BENCHMARK_GEOMETRY_MISMATCH", "Benchmark must use the server-owned CAD document")
    if mesh.metadata.design_hash != compiled.design_hash or mesh.metadata.geometry_fingerprint != compiled.geometry_fingerprint:
        raise AcousticBenchmarkError("BENCHMARK_IDENTITY_MISMATCH", "Benchmark mesh is not bound to the server-owned CAD identity")
    nodes = np.asarray(mesh.nodes_m, dtype=float)
    extents = nodes.max(axis=0) - nodes.min(axis=0)
    if not np.allclose(extents, (LENGTH_M, WIDTH_M, HEIGHT_M), rtol=0, atol=1e-9):
        raise AcousticBenchmarkError("BENCHMARK_GEOMETRY_MISMATCH", "Benchmark CAD dimensions changed")
    if mesh.metadata.element_types != ["tetra4", "triangle3"]:
        raise AcousticBenchmarkError("BENCHMARK_MESH_MISMATCH", "Benchmark requires the authoritative TET4 mesh family")
    if model.analysis_family.value != "acoustics" or model.domains != [benchmark_domain()]:
        raise AcousticBenchmarkError("BENCHMARK_PHYSICS_MISMATCH", "Benchmark acoustic domain identity changed")
    expected_model = build_physics_model(mesh, benchmark_physics_request())
    if model.physics_hash != expected_model.physics_hash:
        raise AcousticBenchmarkError("BENCHMARK_PHYSICS_MISMATCH", "Benchmark PhysicsModel identity changed")
    if model.materials != expected_model.materials:
        raise AcousticBenchmarkError("BENCHMARK_MATERIAL_MISMATCH", "Benchmark material identity or properties changed")
    authoritative_speed_of_sound(model)
    if model.numerical_settings.frequency_hz != FREQUENCY_HZ:
        raise AcousticBenchmarkError("BENCHMARK_FREQUENCY_MISMATCH", "Benchmark frequency changed")
    expected = benchmark_physics_request().boundary_conditions
    if [item.model_dump(mode="json") for item in model.boundary_conditions] != [item.model_dump(mode="json") for item in expected]:
        raise AcousticBenchmarkError("BENCHMARK_BOUNDARY_MISMATCH", "Benchmark boundary semantics changed")


def analytical_plane_wave_pressure(x_m: np.ndarray | float, speed_of_sound_m_s: float) -> np.ndarray:
    wave_number = 2.0 * math.pi * FREQUENCY_HZ / speed_of_sound_m_s
    denominator = math.sin(wave_number * LENGTH_M)
    if abs(denominator) <= 1e-8:
        raise AcousticBenchmarkError("ANALYTICAL_REFERENCE_SINGULAR", "Server-owned analytical denominator is too close to zero")
    # CadQuery centers the rectangular sketch on x=0, so the inlet is at
    # -LENGTH_M/2 rather than at x=0.  The analytical solution is expressed in
    # axial distance from that inlet and must use the same CAD coordinate frame.
    distance_from_inlet_m = np.asarray(x_m, dtype=float) + LENGTH_M / 2.0
    return PRESSURE_PA * np.sin(wave_number * (LENGTH_M - distance_from_inlet_m)) / denominator


def _finite_solution(solution: AcousticFEMSolutionV1) -> bool:
    fields = (
        solution.pressure_real_pa, solution.pressure_imag_pa,
        solution.pressure_amplitude_pa, solution.pressure_phase_rad,
    )
    return all(np.isfinite(np.asarray(values, dtype=float)).all() for values in fields)


def evaluate_benchmark_level(
    compiled: CompiledDesign, mesh: GeneratedMesh, model: PhysicsModelV1,
    solution: AcousticFEMSolutionV1,
) -> AcousticBenchmarkResultV1:
    validate_benchmark_identity(compiled, mesh, model)
    speed = authoritative_speed_of_sound(model)
    numerical = np.asarray(solution.pressure_real_pa) + 1j * np.asarray(solution.pressure_imag_pa)
    exact = analytical_plane_wave_pressure(np.asarray(mesh.nodes_m)[:, 0], speed)
    error = float(np.linalg.norm(numerical - exact) / max(np.linalg.norm(exact), 1e-30))
    conditioning_passed = solution.reciprocal_condition_estimate >= MIN_RECIPROCAL_CONDITION_ESTIMATE
    passed = bool(error <= COMPLEX_L2_ERROR_LIMIT and solution.converged
                  and solution.k_h_max <= 0.5 and conditioning_passed and _finite_solution(solution))
    material = model.materials[0]
    return AcousticBenchmarkResultV1(
        design_hash=model.design_hash, geometry_fingerprint=model.geometry_fingerprint,
        mesh_id=mesh.metadata.mesh_id, mesh_hash=mesh.metadata.mesh_hash,
        physics_model_id=model.physics_model_id, physics_hash=model.physics_hash,
        material_name=material.material_name, material_snapshot_hash=material.snapshot_hash,
        speed_of_sound_m_s=speed, nodes=len(mesh.nodes_m), tetrahedra=len(mesh.tetrahedra),
        wavelength_m=solution.wavelength_m, h_max=solution.maximum_edge_length_m,
        k_h_max=solution.k_h_max,
        normalized_algebraic_residual=solution.normalized_algebraic_residual,
        reciprocal_condition_estimate=solution.reciprocal_condition_estimate,
        conditioning_passed=conditioning_passed, normalized_complex_l2_error=error,
        max_pressure_amplitude_pa=solution.summary_metrics["max_pressure_amplitude_pa"],
        mean_pressure_amplitude_pa=solution.summary_metrics["mean_pressure_amplitude_pa"],
        max_spl_db=solution.summary_metrics["max_spl_db"], passed=passed,
    )


def run_server_owned_benchmark() -> tuple[AcousticBenchmarkResultV1, AcousticFEMSolutionV1]:
    compiled = compile_design(benchmark_design())
    mesh = benchmark_mesh(compiled)
    model = benchmark_model(mesh)
    solution = solve_acoustic_fem_3d(mesh, model)
    return evaluate_benchmark_level(compiled, mesh, model, solution), solution


def run_server_owned_refinement() -> AcousticRefinementResultV1:
    compiled = compile_design(benchmark_design())
    levels: list[AcousticRefinementLevelV1] = []
    models: list[PhysicsModelV1] = []
    for level, target_size_mm in REFINEMENT_TARGETS_MM.items():
        mesh = benchmark_mesh(compiled, target_size_mm)
        model = benchmark_model(mesh)
        solution = solve_acoustic_fem_3d(mesh, model)
        result = evaluate_benchmark_level(compiled, mesh, model, solution)
        levels.append(AcousticRefinementLevelV1(
            **result.model_dump(), level=level, target_size_mm=target_size_mm,
            finite_fields=_finite_solution(solution),
        ))
        models.append(model)
    errors = np.asarray([item.normalized_complex_l2_error for item in levels], dtype=float)
    lengths = np.asarray([item.h_max for item in levels], dtype=float)
    monotonic = bool(errors[0] > errors[1] > errors[2])
    observed_order = None
    if monotonic and np.all(errors > 0) and np.all(lengths > 0):
        observed_order = float(np.polyfit(np.log(lengths), np.log(errors), 1)[0])
    boundary_identity = hashlib.sha256(json.dumps(
        [item.model_dump(mode="json") for item in models[0].boundary_conditions],
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    same_science = all(
        model.design_hash == models[0].design_hash
        and model.geometry_fingerprint == models[0].geometry_fingerprint
        and model.materials == models[0].materials
        and model.boundary_conditions == models[0].boundary_conditions
        and model.numerical_settings == models[0].numerical_settings
        for model in models[1:]
    )
    passed = bool(
        same_science and len({item.mesh_id for item in levels}) == 3
        and len({item.mesh_hash for item in levels}) == 3
        and all(item.passed and item.finite_fields for item in levels)
        and monotonic and observed_order is not None and observed_order > MIN_OBSERVED_ORDER
        and errors[2] <= COMPLEX_L2_ERROR_LIMIT and errors[2] < errors[0]
    )
    return AcousticRefinementResultV1(
        design_hash=models[0].design_hash,
        geometry_fingerprint=models[0].geometry_fingerprint,
        material_snapshot_hash=models[0].materials[0].snapshot_hash,
        boundary_identity=boundary_identity, levels=tuple(levels),
        refinement_monotonic=monotonic, observed_order=observed_order,
        coarse_to_fine_improvement_ratio=float(errors[0] / errors[2]), passed=passed,
    )
