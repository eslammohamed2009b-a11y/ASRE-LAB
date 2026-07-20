"""
Module 2 — solver interfaces.

Two interfaces live here on purpose:

- `Mesh` / `SolverResult` / `BaseSolver` (legacy, unchanged): the original
  minimal interface still used by the legacy `/api/simulate/*` router and
  the integrated Module1->2->3 pipeline (`app.pipeline_service`). Kept
  exactly as it was so that existing surface keeps working unmodified.
- `EngineeringSolver` (new, Phase C2 unified architecture): a
  template-method abstraction used by the new `/api/simulations/*` router.
  `run()` orchestrates a fixed pipeline (validate -> prepare -> mesh ->
  solve -> residual/convergence -> extract -> serialize) and each concrete
  solver only implements the family-specific pieces. This is what
  `thermal_solver_engine.py`, `structural_solver.py`, and `modal_solver.py`
  actually implement; the still-unimplemented families (CFD/wave/EM/coupled)
  are represented only as `solver_registry.py` metadata entries with
  `implementation_status != real`, never as a fake subclass returning
  invented numbers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.module2_simulation.schemas import (
    CapabilityEntry,
    ConvergenceStatus,
    SimulationCreateRequest,
    SimulationResultPayload,
)


class Mesh(BaseModel):
    """Minimal FE mesh representation (node coords + element connectivity).
    Legacy interface - see module docstring."""

    nodes: list[tuple[float, float, float]]
    elements: list[tuple[int, ...]]


class SolverResult(BaseModel):
    """Legacy interface - see module docstring."""

    analysis_type: str
    design_id: str
    summary_metrics: dict[str, float]
    field_values: list[float]
    hotspot_node_ids: list[int]


class BaseSolver(ABC):
    """Legacy interface - see module docstring."""

    analysis_type: str = "base"

    @abstractmethod
    def solve(self, mesh: Mesh, material: str, boundary_conditions: dict) -> SolverResult:
        ...


class SolverValidationError(Exception):
    """Raised by any `validate_*` step for a request this solver cannot
    honor (bad geometry, unknown material property, unsupported boundary
    condition, unbounded/out-of-range input, ...). Callers map this to a
    422 HTTP response - never a fabricated result."""


class EngineeringSolver(ABC):
    """Unified multi-physics solver interface (Phase C2). Concrete solvers:
    `thermal_solver_engine.ThermalConductionSolver`,
    `structural_solver.StructuralLinearSolver`, `modal_solver.ModalSolver`."""

    solver_id: str = "base"

    # -- metadata ---------------------------------------------------------
    @property
    @abstractmethod
    def capability_metadata(self) -> CapabilityEntry:
        """Registry entry describing this solver's declared scope."""

    # -- validation ---------------------------------------------------------
    @abstractmethod
    def validate_geometry(self, request: SimulationCreateRequest) -> None:
        """Raise `SolverValidationError` if the geometry is out of this
        solver's declared dimensional/size scope."""

    @abstractmethod
    def validate_material(self, request: SimulationCreateRequest) -> dict[str, Any]:
        """Resolve + validate the requested material against the material
        library, returning the property snapshot used. Raises
        `SolverValidationError` (wrapping `MaterialNotFoundError` /
        `MaterialPropertyNotFoundError`) if a required property is
        missing - never silently substituted."""

    @abstractmethod
    def validate_boundary_conditions(self, request: SimulationCreateRequest) -> None:
        """Raise `SolverValidationError` if the supplied boundary
        conditions do not match what this solver's family actually
        supports (see `capability_metadata.supported_boundary_conditions`)."""

    def validate_inputs(self, request: SimulationCreateRequest) -> dict[str, Any]:
        """Runs all three `validate_*` steps in order and returns the
        resolved material property snapshot. Subclasses generally do not
        need to override this - override the three specific validators
        instead."""
        self.validate_geometry(request)
        self.validate_boundary_conditions(request)
        return self.validate_material(request)

    # -- model/mesh preparation ---------------------------------------------
    @abstractmethod
    def prepare_model(self, request: SimulationCreateRequest, material_properties: dict[str, Any]) -> Any:
        """Build whatever intermediate model representation `solve()` needs
        (e.g. resolved geometry + loads) from the validated request."""

    @abstractmethod
    def generate_or_import_mesh(self, request: SimulationCreateRequest, model: Any) -> Any:
        """Discretize the model (finite-difference grid, 1D element chain,
        ...). Returns a mesh/discretization object passed to `solve()`."""

    # -- solve ---------------------------------------------------------------
    @abstractmethod
    def solve(self, request: SimulationCreateRequest, model: Any, mesh: Any) -> Any:
        """Run the actual numerical solve. Returns solver-internal raw
        results (arrays, iteration counts, ...) consumed by
        `calculate_residual`/`extract_metrics`/`serialize_results`."""

    @abstractmethod
    def calculate_residual(self, raw_result: Any) -> float | None:
        """Return the final numerical residual/error, or None if this
        solver's method does not produce one (e.g. a direct linear solve)."""

    @abstractmethod
    def check_convergence(self, raw_result: Any) -> ConvergenceStatus:
        """Return the convergence status (converged flag, iteration count,
        residual, tolerance) for this run."""

    # -- results ---------------------------------------------------------------
    @abstractmethod
    def extract_metrics(self, raw_result: Any) -> tuple[dict[str, float], list[float], list[int]]:
        """Return (summary_metrics, field_values, hotspot_node_ids)."""

    def return_assumptions(self) -> list[str]:
        """Human-readable list of modeling assumptions this solve relied
        on. Override to add solver-specific assumptions."""
        return []

    def return_warnings(self) -> list[str]:
        """Human-readable list of warnings raised during this solve
        (e.g. inputs clamped to a valid range). Override to add
        solver-specific warnings."""
        return []

    def serialize_results(
        self,
        raw_result: Any,
        convergence: ConvergenceStatus,
    ) -> SimulationResultPayload:
        summary_metrics, field_values, hotspot_node_ids = self.extract_metrics(raw_result)
        return SimulationResultPayload(
            solver_id=self.solver_id,
            solver_version=self.capability_metadata.version,
            governing_equations=self.capability_metadata.governing_equations,
            assumptions=self.return_assumptions(),
            warnings=self.return_warnings(),
            convergence=convergence,
            summary_metrics=summary_metrics,
            field_values=field_values,
            hotspot_node_ids=hotspot_node_ids,
        )

    # -- orchestration ---------------------------------------------------------
    def run(self, request: SimulationCreateRequest) -> SimulationResultPayload:
        """Template method: validate -> prepare -> mesh -> solve ->
        convergence -> serialize. Raises `SolverValidationError` for bad
        inputs before any numerical work is attempted."""
        material_properties = self.validate_inputs(request)
        model = self.prepare_model(request, material_properties)
        mesh = self.generate_or_import_mesh(request, model)
        raw_result = self.solve(request, model, mesh)
        convergence = self.check_convergence(raw_result)
        return self.serialize_results(raw_result, convergence)
