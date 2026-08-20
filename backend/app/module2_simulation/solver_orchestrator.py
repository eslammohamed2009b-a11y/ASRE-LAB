"""Deterministic, fail-closed orchestration for authoritative CAD FEM.

This module plans and dispatches only fixed, reviewed solver adapters. It is
not a second numerical implementation; its CFD path is restricted to the
reviewed OpenFOAM 14 steady laminar adapter.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator

from app.module2_simulation.cad_fem_solvers import (
    FEMSolution,
    solve_modal_fem_3d,
    solve_structural_fem_3d,
    solve_thermal_fem_3d,
)
from app.module2_simulation.fem_core import MAX_ELEMENTS, MAX_NODES, MAX_STRUCTURAL_DOFS
from app.module2_simulation.geometry_physics_schemas import PhysicsModelV1
from app.module2_simulation.meshing import GeneratedMesh
from app.module2_simulation.openfoam_case import CFDSolutionV1, parse_cfd_solution, prepare_laminar_case
from app.module2_simulation.schemas import ImplementationStatus
from app.module2_simulation.solver_registry import SOLVER_REGISTRY


class PreflightStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class BackendAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    AVAILABLE_BUT_NOT_DEPLOYMENT_CERTIFIED = "AVAILABLE_BUT_NOT_DEPLOYMENT_CERTIFIED"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ExecutionResourceLimits:
    maximum_nodes: int = MAX_NODES
    maximum_elements: int = MAX_ELEMENTS
    maximum_degrees_of_freedom: int = MAX_STRUCTURAL_DOFS
    maximum_requested_iterations: int = 100_000
    maximum_requested_modes: int = 500


@dataclass(frozen=True)
class BackendCapability:
    backend_id: str
    status: BackendAvailability
    detected_version: str | None
    detection_method: str
    deployment_support_state: str
    reason: str | None = None


@dataclass(frozen=True)
class SolverExecutionPlanV1:
    solver_id: str
    solver_version: str
    analysis_family: str
    backend_id: str
    backend_version: str
    backend_availability: BackendAvailability
    execution_mode: str
    mesh_id: str
    mesh_hash: str
    physics_model_id: str
    physics_hash: str
    request_fingerprint: str
    preflight_status: PreflightStatus
    resource_limits: ExecutionResourceLimits
    required_capabilities: tuple[str, ...]
    authoritative: bool
    diagnostics: tuple[str, ...]
    limitations: tuple[str, ...]


class SolverOrchestrationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


SolverCallable = Callable[[GeneratedMesh, PhysicsModelV1], FEMSolution | CFDSolutionV1]


@dataclass(frozen=True)
class FixedSolverAdapter:
    adapter_id: str
    solver_id: str
    backend_id: str
    callable: SolverCallable


# This is intentionally a literal mapping, never derived from user input or a
# module string.  Adding an adapter requires a code review and a source change.
FIXED_SOLVER_ADAPTERS: dict[str, FixedSolverAdapter] = {
    "thermal_fem_3d_v1": FixedSolverAdapter("cad_fem_thermal_v1", "thermal_fem_3d_v1", "python-scipy", solve_thermal_fem_3d),
    "structural_linear_elasticity_3d_v1": FixedSolverAdapter("cad_fem_structural_v1", "structural_linear_elasticity_3d_v1", "python-scipy", solve_structural_fem_3d),
    "modal_fem_3d_v1": FixedSolverAdapter("cad_fem_modal_v1", "modal_fem_3d_v1", "python-scipy", solve_modal_fem_3d),
}


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _backend_version() -> str:
    try:
        return importlib.metadata.version("scipy")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - backend cannot execute without it
        return "unavailable"


def local_fem_backend() -> BackendCapability:
    version = _backend_version()
    return BackendCapability(
        backend_id="python-scipy",
        status=BackendAvailability.AVAILABLE if version != "unavailable" else BackendAvailability.UNAVAILABLE,
        detected_version=version if version != "unavailable" else None,
        detection_method="python package metadata",
        deployment_support_state="current backend image requirements.txt" if version != "unavailable" else "not installed",
        reason=None if version != "unavailable" else "SciPy is required by the existing authoritative FEM adapters",
    )


def _external_binary_capability(backend_id: str, executable: str, *, deployment_state: str) -> BackendCapability:
    path = shutil.which(executable)
    if path is None:
        return BackendCapability(backend_id, BackendAvailability.UNAVAILABLE, None, f"which {executable}", deployment_state, f"{executable} was not found on PATH")
    try:
        completed = subprocess.run([path, "--version"], shell=False, capture_output=True, text=True, timeout=5, check=False)
        version = (completed.stdout or completed.stderr).strip().splitlines()[0][:200] or "detected (version output empty)"
        status = BackendAvailability.AVAILABLE_BUT_NOT_DEPLOYMENT_CERTIFIED
        reason = None if completed.returncode == 0 else f"{executable} --version exited {completed.returncode}"
        return BackendCapability(backend_id, status, version, f"which {executable}; {executable} --version", deployment_state, reason)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return BackendCapability(backend_id, BackendAvailability.UNAVAILABLE, None, f"which {executable}; {executable} --version", deployment_state, str(exc))


def detect_external_backends() -> dict[str, BackendCapability]:
    """Run bounded, real local-runtime probes; no package is installed here."""
    def python_package(backend_id: str, package: str) -> BackendCapability:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            return BackendCapability(backend_id, BackendAvailability.UNAVAILABLE, None, f"python package metadata: {package}", "not included in backend image", f"{package} is not installed")
        return BackendCapability(backend_id, BackendAvailability.AVAILABLE_BUT_NOT_DEPLOYMENT_CERTIFIED, version, f"python package metadata: {package}", "not included in backend image", "installed locally but not certified in deployment image")

    return {
        "gmsh": _external_binary_capability("gmsh", "gmsh", deployment_state="not included in backend image"),
        "fenicsx": python_package("fenicsx", "fenics-dolfinx"),
        "petsc": python_package("petsc", "petsc4py"),
        "openfoam": detect_openfoam14_backend(),
    }


def detect_openfoam14_backend() -> BackendCapability:
    """Probe the exact reviewed OpenFOAM Foundation v14 runtime."""
    executable = shutil.which("foamRun")
    if executable is None:
        return BackendCapability("openfoam-foundation-14", BackendAvailability.UNAVAILABLE, None,
            "which foamRun", "dedicated CFD image only", "foamRun was not found on PATH")
    try:
        completed = subprocess.run([executable, "-help"], shell=False, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return BackendCapability("openfoam-foundation-14", BackendAvailability.UNAVAILABLE, None,
            "foamRun -help", "dedicated CFD image", str(exc))
    output = completed.stdout + completed.stderr
    if completed.returncode != 0 or "OpenFOAM-14" not in output or "-solver <name>" not in output:
        return BackendCapability("openfoam-foundation-14", BackendAvailability.UNSUPPORTED, None,
            "foamRun -help", "dedicated CFD image", "foamRun is not the required OpenFOAM Foundation 14 executable")
    package = "14"
    dpkg = shutil.which("dpkg-query")
    if dpkg:
        result = subprocess.run([dpkg, "-W", "-f=${Version}", "openfoam14"], shell=False,
            capture_output=True, text=True, timeout=10, check=False)
        if result.returncode == 0: package = result.stdout.strip()
    status = BackendAvailability.AVAILABLE if package == "20260724" else BackendAvailability.AVAILABLE_BUT_NOT_DEPLOYMENT_CERTIFIED
    return BackendCapability("openfoam-foundation-14", status, package, "foamRun -help; dpkg-query openfoam14",
        "dedicated Ubuntu 24.04 CFD image", None if package == "20260724" else "OpenFOAM package is not pinned subversion 20260724")


def _material_snapshot(model: PhysicsModelV1) -> list[dict]:
    return [item.model_dump(mode="json") for item in model.materials]


def scientific_execution_fingerprint(
    solver_id: str,
    backend_id: str,
    backend_version: str | None,
    mesh: GeneratedMesh,
    model: PhysicsModelV1,
) -> str:
    """Hash the complete scientific state consumed by a fixed execution."""
    entry = SOLVER_REGISTRY.get(solver_id)
    if entry is None:
        raise SolverOrchestrationError("UNKNOWN_SOLVER", f"Solver '{solver_id}' is not registered")
    return _canonical_hash({
        "solver_id": solver_id, "solver_version": entry.version, "backend_id": backend_id,
        "backend_version": backend_version, "physics_hash": model.physics_hash,
        "design_hash": model.design_hash, "geometry_fingerprint": model.geometry_fingerprint,
        "mesh_id": mesh.metadata.mesh_id, "mesh_hash": mesh.metadata.mesh_hash,
        "material_snapshots": _material_snapshot(model),
        "domains": [item.model_dump(mode="json") for item in model.domains],
        "material_assignments": [item.model_dump(mode="json") for item in model.material_assignments],
        "boundary_conditions": [item.model_dump(mode="json") for item in model.boundary_conditions],
        "numerical_settings": model.numerical_settings.model_dump(mode="json"),
    })


def _preflight_errors(solver_id: str, mesh: GeneratedMesh, model: PhysicsModelV1, limits: ExecutionResourceLimits, backend: BackendCapability) -> list[str]:
    entry = SOLVER_REGISTRY.get(solver_id)
    if entry is None or solver_id not in FIXED_SOLVER_ADAPTERS:
        return ["UNKNOWN_SOLVER"]
    errors: list[str] = []
    if backend.backend_id != FIXED_SOLVER_ADAPTERS[solver_id].backend_id:
        errors.append("SOLVER_BACKEND_MISMATCH")
    if entry.implementation_status != ImplementationStatus.REAL:
        errors.append("SOLVER_NOT_IMPLEMENTED")
    if entry.family.value != model.analysis_family.value:
        errors.append("SOLVER_FAMILY_MISMATCH")
    if not entry.consumes_authoritative_cad or model.mesh_hash != mesh.metadata.mesh_hash or model.mesh_id != mesh.metadata.mesh_id:
        errors.append("AUTHORITATIVE_MESH_REQUIRED")
    if entry.required_mesh_dimension != mesh.metadata.dimension:
        errors.append("UNSUPPORTED_MESH_DIMENSION")
    if not set(mesh.metadata.element_types).intersection(entry.accepted_element_types):
        errors.append("UNSUPPORTED_ELEMENT_TYPE")
    if any(domain.domain_kind.value not in entry.supported_domain_types for domain in model.domains):
        errors.append("UNSUPPORTED_SOLVER_DOMAIN")
    unsupported_bcs = sorted({bc.bc_type for bc in model.boundary_conditions} - set(entry.supported_boundary_conditions))
    if unsupported_bcs:
        errors.append("UNSUPPORTED_BOUNDARY_CONDITION")
    names = {property_.name for material in model.materials for property_ in material.properties}
    required_by_family = {"thermal": {"thermal_conductivity"}, "structural": {"elastic_modulus", "poisson_ratio", "density"}, "modal": {"elastic_modulus", "poisson_ratio", "density"}, "cfd": {"density", "dynamic_viscosity"}}
    if not required_by_family.get(model.analysis_family.value, set()).issubset(names):
        errors.append("MATERIAL_PROPERTY_MISSING")
    if len(mesh.nodes_m) > limits.maximum_nodes or len(mesh.tetrahedra) > limits.maximum_elements:
        errors.append("RESOURCE_LIMIT")
    requested_iterations = getattr(model.numerical_settings, "maximum_iterations", 0)
    requested_modes = getattr(model.numerical_settings, "requested_modes", 0)
    if requested_iterations > limits.maximum_requested_iterations or requested_modes > limits.maximum_requested_modes:
        errors.append("RESOURCE_LIMIT")
    dofs = len(mesh.nodes_m) * (3 if solver_id != "thermal_fem_3d_v1" else 1)
    if dofs > limits.maximum_degrees_of_freedom:
        errors.append("RESOURCE_LIMIT")
    if backend.status not in {BackendAvailability.AVAILABLE, BackendAvailability.AVAILABLE_BUT_NOT_DEPLOYMENT_CERTIFIED}:
        errors.append("SOLVER_BACKEND_UNAVAILABLE")
    return errors


def create_execution_plan(solver_id: str, mesh: GeneratedMesh, model: PhysicsModelV1, *, limits: ExecutionResourceLimits = ExecutionResourceLimits(), backend: BackendCapability | None = None) -> SolverExecutionPlanV1:
    adapter = FIXED_SOLVER_ADAPTERS.get(solver_id)
    entry = SOLVER_REGISTRY.get(solver_id)
    active_backend = backend or (detect_openfoam14_backend() if solver_id == "cfd_openfoam_laminar_internal_3d_v1" else local_fem_backend())
    errors = _preflight_errors(solver_id, mesh, model, limits, active_backend)
    if adapter is None or entry is None:
        raise SolverOrchestrationError("UNKNOWN_SOLVER", f"Solver '{solver_id}' has no fixed authoritative adapter")
    fingerprint = scientific_execution_fingerprint(
        solver_id, active_backend.backend_id, active_backend.detected_version, mesh, model
    )
    return SolverExecutionPlanV1(
        solver_id=solver_id, solver_version=entry.version, analysis_family=model.analysis_family.value,
        backend_id=active_backend.backend_id, backend_version=active_backend.detected_version or "unavailable",
        backend_availability=active_backend.status,
        execution_mode="fixed_external_openfoam" if adapter.backend_id == "openfoam-foundation-14" else "fixed_python_callable", mesh_id=mesh.metadata.mesh_id, mesh_hash=mesh.metadata.mesh_hash,
        physics_model_id=model.physics_model_id, physics_hash=model.physics_hash, request_fingerprint=fingerprint,
        preflight_status=PreflightStatus.PASS if not errors else PreflightStatus.FAIL, resource_limits=limits,
        required_capabilities=tuple(model.solver_requirements), authoritative=bool(entry.consumes_authoritative_cad),
        diagnostics=tuple(errors) if errors else ("PREFLIGHT_PASS",), limitations=tuple(entry.known_limitations),
    )


def dispatch(plan: SolverExecutionPlanV1, mesh: GeneratedMesh, model: PhysicsModelV1) -> FEMSolution | CFDSolutionV1:
    """Dispatch only a preflight-passing plan to its fixed in-process callable."""
    if plan.preflight_status != PreflightStatus.PASS:
        raise SolverOrchestrationError(plan.diagnostics[0], "Solver preflight failed; no fallback solver was selected")
    adapter = FIXED_SOLVER_ADAPTERS.get(plan.solver_id)
    if adapter is None or adapter.backend_id != plan.backend_id:
        raise SolverOrchestrationError("SOLVER_BACKEND_UNAVAILABLE", "Fixed adapter is unavailable; no fallback solver was selected")
    active_backend = detect_openfoam14_backend() if adapter.backend_id == "openfoam-foundation-14" else local_fem_backend()
    if active_backend.backend_id != plan.backend_id or active_backend.detected_version != plan.backend_version:
        raise SolverOrchestrationError("SOLVER_BACKEND_MISMATCH", "The active fixed backend no longer matches the execution plan")
    recomputed = scientific_execution_fingerprint(
        plan.solver_id, active_backend.backend_id, active_backend.detected_version, mesh, model
    )
    if recomputed != plan.request_fingerprint:
        raise SolverOrchestrationError("PLAN_INPUT_MISMATCH", "Plan does not match the supplied complete scientific inputs")
    return adapter.callable(mesh, model)


@dataclass(frozen=True)
class OpenFOAMExecutionConfig:
    timeout_seconds: int = 300


class OpenFOAMAdapterFoundation:
    """Safe fixed OpenFOAM process boundary used by the reviewed CFD solver."""
    executable = "foamRun"
    solver_module = "incompressibleFluid"

    def __init__(self, config: OpenFOAMExecutionConfig = OpenFOAMExecutionConfig()):
        self.config = config
        self._authorized_workspaces: set[Path] = set()

    @contextmanager
    def case_workspace(self) -> Iterator[Path]:
        """Create the only case directory this adapter will execute."""
        with tempfile.TemporaryDirectory(prefix="asre-openfoam-") as directory:
            workspace = Path(directory).resolve()
            self._authorized_workspaces.add(workspace)
            try:
                yield workspace
            finally:
                self._authorized_workspaces.discard(workspace)

    def run_fixed_case(self, case_directory: Path) -> subprocess.CompletedProcess[str]:
        executable = shutil.which(self.executable)
        if executable is None:
            raise SolverOrchestrationError("SOLVER_BACKEND_UNAVAILABLE", "OpenFOAM Foundation foamRun is not installed")
        case = case_directory.resolve()
        if not case.is_dir() or case not in self._authorized_workspaces:
            raise SolverOrchestrationError("INVALID_EXTERNAL_WORKDIR", "OpenFOAM case directory is not an adapter-controlled workspace")
        # The only command is a literal executable plus the generated case path.
        try:
            completed = subprocess.run([executable, "-solver", self.solver_module, "-case", str(case)], shell=False, capture_output=True, text=True, timeout=self.config.timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise SolverOrchestrationError("SOLVER_TIMEOUT", "OpenFOAM exceeded the configured execution timeout") from exc
        if completed.returncode != 0:
            raise SolverOrchestrationError("SOLVER_EXTERNAL_FAILED", f"OpenFOAM exited with code {completed.returncode}")
        return completed

    def isolated_workdir(self) -> tempfile.TemporaryDirectory[str]:
        """Deprecated helper; use ``case_workspace`` for executable cases."""
        return tempfile.TemporaryDirectory(prefix="asre-openfoam-")


def solve_openfoam_cfd_3d(mesh: GeneratedMesh, model: PhysicsModelV1) -> CFDSolutionV1:
    """Execute only the reviewed steady laminar OpenFOAM case in an isolated workspace."""
    adapter = OpenFOAMAdapterFoundation()
    with adapter.case_workspace() as case:
        poly, definition = prepare_laminar_case(mesh, model, case)
        completed = adapter.run_fixed_case(case)
        solution = parse_cfd_solution(mesh, model, poly, definition, case, completed.stdout + completed.stderr)
        if not solution.converged:
            raise SolverOrchestrationError("CFD_NOT_CONVERGED", "OpenFOAM output failed residual or mass-conservation acceptance")
        return solution


# Registered only after the concrete bounded adapter is defined; no dynamic module or user solver lookup exists.
FIXED_SOLVER_ADAPTERS["cfd_openfoam_laminar_internal_3d_v1"] = FixedSolverAdapter(
    "openfoam_laminar_internal_3d_v1", "cfd_openfoam_laminar_internal_3d_v1",
    "openfoam-foundation-14", solve_openfoam_cfd_3d,
)
