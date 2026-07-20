from app.module2_simulation.schemas import (
    ImplementationStatus,
    Recommendation,
    RecommendRequest,
    RecommendResponse,
    RecommendationStatus,
)
from app.module2_simulation.solver_registry import SOLVER_REGISTRY, SOLVER_VALIDATION_STATUS, is_supported


def recommend_analyses(model_type: str) -> list[str]:
    """Engineering-rules-of-thumb recommendations. These are advisory only —
    not every recommended analysis type has a validated numerical solver
    behind it yet. See `supported_analyses`/`SOLVER_VALIDATION_STATUS` for
    which ones can actually be executed via /api/simulate/run today.
    """
    normalized = model_type.lower()
    if "bridge" in normalized:
        return ["structural", "vibration", "thermal"]
    if "pyramid" in normalized:
        return ["thermal", "wind_load"]
    return ["thermal", "structural", "cfd"]


def supported_analyses(recommended: list[str]) -> list[str]:
    """Subset of `recommended` that currently has a validated numerical solver."""
    return [name for name in recommended if is_supported(name)]


# -- new unified registry-backed advisor (Phase C8) ------------------------------------------------
_GEOMETRY_FAMILY_HINTS: dict[str, list[str]] = {
    "bridge": ["structural_linear_1d_v1", "modal_eigen_1d_v1", "thermal_conduction_v1"],
    "tower": ["structural_linear_1d_v1", "modal_eigen_1d_v1", "thermal_conduction_v1", "cfd_wind_drag_v1"],
    "pyramid": ["thermal_conduction_v1", "cfd_wind_drag_v1"],
    "arch": ["structural_linear_1d_v1", "thermal_conduction_v1"],
    "dome": ["structural_linear_1d_v1", "thermal_conduction_v1", "cfd_wind_drag_v1"],
}
_DEFAULT_SOLVER_HINTS = ["thermal_conduction_v1", "structural_linear_1d_v1", "modal_eigen_1d_v1"]

_IMPLEMENTATION_TO_RECOMMENDATION = {
    ImplementationStatus.REAL: RecommendationStatus.AVAILABLE,
    ImplementationStatus.PROTOTYPE: RecommendationStatus.EXPERIMENTAL,
    ImplementationStatus.PLANNED: RecommendationStatus.PLANNED,
}


def recommend_from_registry(request: RecommendRequest) -> RecommendResponse:
    """Registry-backed recommendations for the new `/api/simulations/recommend`
    endpoint. Every recommendation's `status` is derived directly from
    `solver_registry.SOLVER_REGISTRY[...].implementation_status` - never
    hand-picked - so this can never claim a solver is more capable than the
    registry says it is."""
    normalized = request.geometry_category.lower()
    solver_ids: list[str] = []
    for keyword, hints in _GEOMETRY_FAMILY_HINTS.items():
        if keyword in normalized:
            solver_ids = hints
            break
    if not solver_ids:
        solver_ids = _DEFAULT_SOLVER_HINTS

    recommendations = []
    for solver_id in solver_ids:
        entry = SOLVER_REGISTRY.get(solver_id)
        if entry is None:
            continue
        status = _IMPLEMENTATION_TO_RECOMMENDATION[entry.implementation_status]
        if status == RecommendationStatus.AVAILABLE:
            rationale = f"{entry.family.value} solver '{solver_id}' is real and validated for this geometry class."
        elif status == RecommendationStatus.EXPERIMENTAL:
            rationale = (
                f"{entry.family.value} solver '{solver_id}' exists only as an unvalidated prototype; "
                "treat its output as a rough estimate, not an engineering result."
            )
        else:
            rationale = f"{entry.family.value} analysis is planned but has no implementation yet."
        recommendations.append(
            Recommendation(solver_id=solver_id, family=entry.family, status=status, rationale=rationale)
        )
    return RecommendResponse(recommendations=recommendations)
