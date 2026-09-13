"""Bounded complex frequency-domain Helmholtz FEM on authoritative TET4 meshes."""
from __future__ import annotations

import itertools
import math
import warnings
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import sparse
from scipy.sparse import linalg as sparse_linalg

from app.module2_simulation.cad_fem_solvers import _facet_opposites, _facets_by_semantic
from app.module2_simulation.fem_core import FEMError, tet4_geometry, triangle_area_and_outward_normal, validate_mesh_arrays
from app.module2_simulation.geometry_physics_schemas import (
    AcousticPressureBC,
    AcousticWallBC,
    AnalysisFamilyV1,
    PhysicsModelV1,
)
from app.module2_simulation.meshing import GeneratedMesh


SOLVER_ID = "acoustic_helmholtz_fem_3d_v1"
SOLVER_VERSION = "1.0.0"
SPL_REFERENCE_PRESSURE_PA = 20e-6
MAX_K_H = 0.5
RESIDUAL_TOLERANCE = 1e-8


class AcousticFEMError(FEMError):
    """Typed fail-closed errors for the bounded acoustic FEM scope."""


class AcousticFEMSolutionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solver_id: Literal["acoustic_helmholtz_fem_3d_v1"] = SOLVER_ID
    solver_version: Literal["1.0.0"] = SOLVER_VERSION
    mesh_id: str
    mesh_hash: str
    physics_model_id: str
    physics_hash: str
    frequency_hz: float = Field(gt=0)
    omega_rad_s: float = Field(gt=0)
    wave_number_rad_m: float = Field(gt=0)
    wavelength_m: float = Field(gt=0)
    maximum_edge_length_m: float = Field(gt=0)
    k_h_max: float = Field(ge=0)
    converged: bool
    normalized_algebraic_residual: float = Field(ge=0)
    pressure_real_pa: list[float]
    pressure_imag_pa: list[float]
    pressure_amplitude_pa: list[float]
    pressure_phase_rad: list[float]
    spl_db: list[float | None]
    summary_metrics: dict[str, float | None]
    diagnostics: dict[str, float | str | int | bool]
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str]


def _property(model: PhysicsModelV1, name: str) -> float:
    if len(model.materials) != 1:
        raise AcousticFEMError("ACOUSTIC_MATERIAL_SCOPE", "Acoustic FEM requires exactly one material snapshot")
    property_ = next((item for item in model.materials[0].properties if item.name == name), None)
    if property_ is None or not math.isfinite(property_.value) or property_.value <= 0:
        raise AcousticFEMError("MATERIAL_PROPERTY_MISSING", f"Acoustic FEM requires positive finite '{name}'")
    return property_.value


def _verify(mesh: GeneratedMesh, model: PhysicsModelV1) -> None:
    if model.analysis_family != AnalysisFamilyV1.ACOUSTICS:
        raise AcousticFEMError("SOLVER_FAMILY_MISMATCH", "PhysicsModel is incompatible with acoustic FEM")
    domain_kind = getattr(model.domains[0].domain_kind, "value", model.domains[0].domain_kind) if model.domains else None
    if len(model.domains) != 1 or domain_kind != "acoustic":
        raise AcousticFEMError("ACOUSTIC_DOMAIN_REQUIRED", "Acoustic FEM requires exactly one explicit acoustic domain")
    if (model.mesh_id != mesh.metadata.mesh_id or model.mesh_hash != mesh.metadata.mesh_hash
            or model.design_hash != mesh.metadata.design_hash
            or model.geometry_fingerprint != mesh.metadata.geometry_fingerprint):
        raise AcousticFEMError("MESH_PHYSICS_MISMATCH", "PhysicsModel identity does not match the authoritative mesh")
    validate_mesh_arrays(np.asarray(mesh.nodes_m, dtype=float), mesh.tetrahedra)
    if "tetra4" not in mesh.metadata.element_types:
        raise AcousticFEMError("TET4_MESH_REQUIRED", "Acoustic FEM requires an authoritative TET4 mesh")
    mesh_regions = {item.semantic_region: item.boundary_facet_ids for item in mesh.metadata.semantic_mappings}
    model_regions = {item.semantic_region: item.boundary_facet_ids for item in model.semantic_mappings if hasattr(item, "boundary_facet_ids")}
    if set(model_regions) != set(mesh_regions) or any(model_regions[name] != mesh_regions[name] for name in mesh_regions):
        raise AcousticFEMError("INVALID_SEMANTIC_MAPPING", "PhysicsModel semantic mappings do not match the authoritative mesh")
    _property(model, "density")
    _property(model, "speed_of_sound")


def _assemble(size: int, entries: list[tuple[tuple[int, ...], np.ndarray]]) -> sparse.csr_matrix:
    rows: list[int] = []
    cols: list[int] = []
    values: list[complex] = []
    for dofs, local in entries:
        for row, global_row in enumerate(dofs):
            for column, global_column in enumerate(dofs):
                value = complex(local[row, column])
                if value:
                    rows.append(global_row)
                    cols.append(global_column)
                    values.append(value)
    return sparse.coo_matrix((values, (rows, cols)), shape=(size, size), dtype=np.complex128).tocsr()


def _triangle_mass(area_m2: float) -> np.ndarray:
    return area_m2 / 12.0 * np.array(((2.0, 1.0, 1.0), (1.0, 2.0, 1.0), (1.0, 1.0, 2.0)), dtype=float)


def impedance_boundary_matrix(area_m2: float, omega_rad_s: float, density_kg_m3: float, impedance_pa_s_m: float) -> np.ndarray:
    """Return the +i omega rho/Z weak-form term for exp(+i omega t)."""
    if not all(math.isfinite(value) and value > 0 for value in (area_m2, omega_rad_s, density_kg_m3, impedance_pa_s_m)):
        raise AcousticFEMError("INVALID_IMPEDANCE", "Impedance boundary inputs must be finite and positive")
    return 1j * omega_rad_s * density_kg_m3 / impedance_pa_s_m * _triangle_mass(area_m2)


def _apply_dirichlet(matrix: sparse.csr_matrix, prescribed: dict[int, complex]) -> tuple[sparse.csr_matrix, np.ndarray, np.ndarray]:
    if not prescribed:
        raise AcousticFEMError("ACOUSTIC_SYSTEM_SINGULAR", "A pressure reference or pressure-release boundary is required")
    constrained = np.asarray(sorted(prescribed), dtype=int)
    if np.any(constrained < 0) or np.any(constrained >= matrix.shape[0]):
        raise AcousticFEMError("INVALID_CONSTRAINT", "Acoustic pressure constraint references an invalid node")
    free = np.setdiff1d(np.arange(matrix.shape[0]), constrained)
    if not len(free):
        raise AcousticFEMError("OVERCONSTRAINED", "All acoustic pressure nodes are prescribed")
    values = np.asarray([prescribed[index] for index in constrained], dtype=np.complex128)
    reduced = matrix[free][:, free].tocsr()
    rhs = -(matrix[free][:, constrained] @ values)
    return reduced, np.asarray(rhs, dtype=np.complex128), free


def _set_pressure(prescribed: dict[int, complex], facet: tuple[int, int, int], value: complex) -> None:
    for node in facet:
        previous = prescribed.get(node)
        if previous is not None and not np.isclose(previous, value, rtol=0, atol=1e-12):
            raise AcousticFEMError("INCONSISTENT_PRESSURE_CONSTRAINT", "Overlapping acoustic pressure boundaries prescribe different values")
        prescribed[node] = value


def solve_acoustic_fem_3d(mesh: GeneratedMesh, model: PhysicsModelV1) -> AcousticFEMSolutionV1:
    """Solve the lossless complex Helmholtz equation with first-order TET4 FEM."""
    _verify(mesh, model)
    frequency = float(model.numerical_settings.frequency_hz)
    if not math.isfinite(frequency) or frequency <= 0:
        raise AcousticFEMError("INVALID_FREQUENCY", "Acoustic frequency must be finite and positive")
    density = _property(model, "density")
    speed = _property(model, "speed_of_sound")
    omega = 2.0 * math.pi * frequency
    wave_number = omega / speed
    nodes = np.asarray(mesh.nodes_m, dtype=float)
    domains = {element_id - 1: mapping.domain_id for mapping in mesh.metadata.domains for element_id in mapping.volume_element_ids}
    entries: list[tuple[tuple[int, ...], np.ndarray]] = []
    maximum_edge = 0.0
    for element_id, tet in enumerate(mesh.tetrahedra):
        geometry = tet4_geometry(nodes, tet)
        maximum_edge = max(maximum_edge, *(float(np.linalg.norm(nodes[a] - nodes[b])) for a, b in itertools.combinations(tet, 2)))
        stiffness = geometry.volume_m3 * (geometry.gradients_m_inv @ geometry.gradients_m_inv.T)
        mass = geometry.volume_m3 / 20.0 * np.array(((2.0, 1.0, 1.0, 1.0), (1.0, 2.0, 1.0, 1.0),
            (1.0, 1.0, 2.0, 1.0), (1.0, 1.0, 1.0, 2.0)))
        if domains.get(element_id) != model.domains[0].domain_id:
            raise AcousticFEMError("MESH_DOMAIN_MISMATCH", "Every acoustic element must belong to the acoustic domain")
        entries.append((tuple(tet), stiffness - wave_number ** 2 * mass))
    if not math.isfinite(maximum_edge) or maximum_edge <= 0:
        raise AcousticFEMError("INVALID_MESH", "Acoustic mesh has no finite positive edge length")
    k_h_max = wave_number * maximum_edge
    if k_h_max > MAX_K_H:
        raise AcousticFEMError("ACOUSTIC_MESH_TOO_COARSE", "Authoritative acoustic mesh violates k*h_max <= 0.5")
    matrix = _assemble(len(nodes), entries).tolil()
    prescribed: dict[int, complex] = {}
    facets = _facets_by_semantic(mesh)
    opposites = _facet_opposites(mesh)
    for boundary_condition in model.boundary_conditions:
        if getattr(boundary_condition, "bc_type", None) not in {"acoustic_pressure", "acoustic_wall"}:
            raise AcousticFEMError("UNSUPPORTED_ACOUSTIC_BC", "Unsupported acoustic boundary condition")
        target = facets.get(boundary_condition.semantic_region)
        if not target:
            raise AcousticFEMError("INVALID_SEMANTIC_MAPPING", "Acoustic boundary references an empty semantic surface")
        for facet_id in target:
            if facet_id < 1 or facet_id > len(mesh.boundary_facets):
                raise AcousticFEMError("INVALID_SEMANTIC_MAPPING", "Acoustic boundary references an invalid facet")
            facet = mesh.boundary_facets[facet_id - 1]
            if isinstance(boundary_condition, AcousticPressureBC):
                _set_pressure(prescribed, facet, complex(boundary_condition.pressure_pa, 0.0))
            elif isinstance(boundary_condition, AcousticWallBC):
                if boundary_condition.condition == "pressure_release":
                    _set_pressure(prescribed, facet, 0j)
                elif boundary_condition.condition == "impedance":
                    impedance = boundary_condition.impedance_pa_s_m
                    if impedance is None or not math.isfinite(impedance) or impedance <= 0:
                        raise AcousticFEMError("INVALID_IMPEDANCE", "Acoustic impedance must be finite and positive")
                    area, _ = triangle_area_and_outward_normal(nodes, facet, opposites[facet_id])
                    matrix[np.ix_(facet, facet)] += impedance_boundary_matrix(area, omega, density, impedance)
                elif boundary_condition.condition != "rigid":
                    raise AcousticFEMError("UNSUPPORTED_ACOUSTIC_BC", "Unsupported acoustic wall condition")
            else:
                raise AcousticFEMError("UNSUPPORTED_ACOUSTIC_BC", "Unsupported acoustic boundary condition")
    reduced, rhs, free = _apply_dirichlet(matrix.tocsr(), prescribed)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", sparse_linalg.MatrixRankWarning)
            solution = sparse_linalg.spsolve(reduced, rhs)
    except (sparse_linalg.MatrixRankWarning, np.linalg.LinAlgError, RuntimeError) as exc:
        raise AcousticFEMError("ACOUSTIC_SYSTEM_SINGULAR", "Complex acoustic FEM system is singular or unusable") from exc
    if not np.isfinite(solution.real).all() or not np.isfinite(solution.imag).all():
        raise AcousticFEMError("NONFINITE_RESULT", "Acoustic FEM returned a non-finite complex pressure field")
    residual_vector = reduced @ solution - rhs
    normalized_residual = float(np.linalg.norm(residual_vector) / max(np.linalg.norm(rhs), np.linalg.norm(reduced @ solution), 1e-30))
    if not math.isfinite(normalized_residual) or normalized_residual > RESIDUAL_TOLERANCE:
        raise AcousticFEMError("ACOUSTIC_RESIDUAL_FAILED", "Acoustic FEM algebraic residual exceeds 1e-8")
    pressure = np.zeros(len(nodes), dtype=np.complex128)
    pressure[free] = solution
    for node, value in prescribed.items():
        pressure[node] = value
    amplitude = np.abs(pressure)
    phase = np.angle(pressure)
    spl = [20.0 * math.log10(float(value) / SPL_REFERENCE_PRESSURE_PA) if value > 0 else None for value in amplitude]
    defined_spl = [value for value in spl if value is not None]
    return AcousticFEMSolutionV1(
        mesh_id=mesh.metadata.mesh_id, mesh_hash=mesh.metadata.mesh_hash,
        physics_model_id=model.physics_model_id, physics_hash=model.physics_hash,
        frequency_hz=frequency, omega_rad_s=omega, wave_number_rad_m=wave_number,
        wavelength_m=2.0 * math.pi / wave_number, maximum_edge_length_m=maximum_edge,
        k_h_max=k_h_max, converged=True, normalized_algebraic_residual=normalized_residual,
        pressure_real_pa=pressure.real.tolist(), pressure_imag_pa=pressure.imag.tolist(),
        pressure_amplitude_pa=amplitude.tolist(), pressure_phase_rad=phase.tolist(), spl_db=spl,
        summary_metrics={"max_pressure_amplitude_pa": float(amplitude.max()),
                         "mean_pressure_amplitude_pa": float(amplitude.mean()),
                         "max_spl_db": max(defined_spl) if defined_spl else None,
                         "mean_spl_db": float(np.mean(defined_spl)) if defined_spl else None},
        diagnostics={"node_count": len(nodes), "tetrahedron_count": len(mesh.tetrahedra),
                     "nonzero_count": int(matrix.nnz), "solver_method": "scipy.sparse.linalg.spsolve",
                     "time_convention": "p(x,t) = Re{P exp(i omega t)}", "spl_zero_amplitude_nodes": len(spl) - len(defined_spl)},
        limitations=["Lossless stationary homogeneous isotropic fluid with no mean flow.",
                     "No radiation, PML, coupling, transient, thermoviscous, porous, nonlinear, or volumetric source model."],
    )