"""
Module 2 — Solver validation registry.

This is the single source of truth for which analysis types are backed by
a real, numerically-solved engineering model versus which are closed-form
placeholder formulas. It exists so the API never silently returns a
fabricated "simulation result" for an analysis that has not actually been
solved with a validated numerical method.

Status values:
- "validated_prototype": a real numerical solver (iterative/mesh-based) that
  has passed at least a trivial analytical-limit check and a grid-convergence
  check (see tests/integration/test_thermal_solver_benchmark.py). Not yet
  validated against a full published multi-dimensional benchmark case.
- "unsupported": no real solver exists yet — only a simple closed-form
  formula (e.g. stress = load / area, or a drag-equation estimate). Serving
  this as if it were a genuine FEA/CFD result would misrepresent the
  engineering fidelity of the platform, so the API rejects these requests
  with HTTP 501 instead of fabricating a result.
"""

SOLVER_VALIDATION_STATUS: dict[str, str] = {
    "thermal": "validated_prototype",
    "structural": "unsupported",
    "wind_load": "unsupported",
}


def is_supported(analysis_type: str) -> bool:
    return SOLVER_VALIDATION_STATUS.get(analysis_type) == "validated_prototype"


class UnsupportedAnalysisError(Exception):
    """Raised when a client requests an analysis type with no validated solver."""

    def __init__(self, analysis_type: str) -> None:
        self.analysis_type = analysis_type
        super().__init__(
            f"Analysis type '{analysis_type}' has no validated numerical solver in this "
            "build. It is implemented only as a simplified closed-form placeholder formula "
            "(not a real FEA/CFD solution), so this API refuses to return it as a simulation "
            "result. See /api/simulate/advisor for planned capabilities."
        )
