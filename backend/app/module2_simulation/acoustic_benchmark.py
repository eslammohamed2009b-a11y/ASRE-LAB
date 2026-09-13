"""Server-owned analytical gate for geometry-aware 3D acoustic FEM."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.module1_design.cad_v2_compiler import CompiledDesign, compile_design
from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2
from app.module2_simulation.acoustic_fem_solvers import (
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


class AcousticBenchmarkError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AcousticBenchmarkResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_id: str = BENCHMARK_ID
    benchmark_version: str = BENCHMARK_VERSION
    solver_id: str = SOLVER_ID
    mesh_id: str
    mesh_hash: str
    nodes: int = Field(gt=0)
    tetrahedra: int = Field(gt=0)
    frequency_hz: float = FREQUENCY_HZ
    wavelength_m: float = Field(gt=0)
    h_max: float = Field(gt=0)
    k_h_max: float = Field(ge=0)
    normalized_algebraic_residual: float = Field(ge=0)
    normalized_complex_l2_error: float = Field(ge=0)
    max_pressure_amplitude_pa: float = Field(ge=0)
    mean_pressure_amplitude_pa: float = Field(ge=0)
    max_spl_db: float | None
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


def benchmark_mesh_specification() -> MeshSpecification:
    return MeshSpecification(target_size=_q(MESH_TARGET_SIZE_MM), refinement_level="custom")


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


def benchmark_mesh(compiled: CompiledDesign | None = None) -> GeneratedMesh:
    compiled = compiled or compile_design(benchmark_design())
    return generate_mesh(compiled, [benchmark_domain()], benchmark_mesh_specification())


def benchmark_model(mesh: GeneratedMesh) -> PhysicsModelV1:
    return build_physics_model(mesh, benchmark_physics_request())


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
    if model.physics_hash != build_physics_model(mesh, benchmark_physics_request()).physics_hash:
        raise AcousticBenchmarkError("BENCHMARK_PHYSICS_MISMATCH", "Benchmark PhysicsModel identity changed")
    if model.numerical_settings.frequency_hz != FREQUENCY_HZ:
        raise AcousticBenchmarkError("BENCHMARK_FREQUENCY_MISMATCH", "Benchmark frequency changed")
    expected = benchmark_physics_request().boundary_conditions
    if [item.model_dump(mode="json") for item in model.boundary_conditions] != [item.model_dump(mode="json") for item in expected]:
        raise AcousticBenchmarkError("BENCHMARK_BOUNDARY_MISMATCH", "Benchmark boundary semantics changed")


def analytical_plane_wave_pressure(x_m: np.ndarray | float, speed_of_sound_m_s: float = 343.0) -> np.ndarray:
    wave_number = 2.0 * math.pi * FREQUENCY_HZ / speed_of_sound_m_s
    denominator = math.sin(wave_number * LENGTH_M)
    if abs(denominator) <= 1e-8:
        raise AcousticBenchmarkError("ANALYTICAL_REFERENCE_SINGULAR", "Server-owned analytical denominator is too close to zero")
    # CadQuery centers the rectangular sketch on x=0, so the inlet is at
    # -LENGTH_M/2 rather than at x=0.  The analytical solution is expressed in
    # axial distance from that inlet and must use the same CAD coordinate frame.
    distance_from_inlet_m = np.asarray(x_m, dtype=float) + LENGTH_M / 2.0
    return PRESSURE_PA * np.sin(wave_number * (LENGTH_M - distance_from_inlet_m)) / denominator


def run_server_owned_benchmark() -> tuple[AcousticBenchmarkResultV1, AcousticFEMSolutionV1]:
    compiled = compile_design(benchmark_design())
    mesh = benchmark_mesh(compiled)
    model = benchmark_model(mesh)
    validate_benchmark_identity(compiled, mesh, model)
    solution = solve_acoustic_fem_3d(mesh, model)
    numerical = np.asarray(solution.pressure_real_pa) + 1j * np.asarray(solution.pressure_imag_pa)
    exact = analytical_plane_wave_pressure(np.asarray(mesh.nodes_m)[:, 0])
    error = float(np.linalg.norm(numerical - exact) / max(np.linalg.norm(exact), 1e-30))
    passed = error <= COMPLEX_L2_ERROR_LIMIT and solution.converged and solution.k_h_max <= 0.5
    return AcousticBenchmarkResultV1(
        mesh_id=mesh.metadata.mesh_id, mesh_hash=mesh.metadata.mesh_hash,
        nodes=len(mesh.nodes_m), tetrahedra=len(mesh.tetrahedra), wavelength_m=solution.wavelength_m,
        h_max=solution.maximum_edge_length_m, k_h_max=solution.k_h_max,
        normalized_algebraic_residual=solution.normalized_algebraic_residual,
        normalized_complex_l2_error=error,
        max_pressure_amplitude_pa=solution.summary_metrics["max_pressure_amplitude_pa"],
        mean_pressure_amplitude_pa=solution.summary_metrics["mean_pressure_amplitude_pa"],
        max_spl_db=solution.summary_metrics["max_spl_db"], passed=passed,
    ), solution
