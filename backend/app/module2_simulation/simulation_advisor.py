from app.module2_simulation.solver_registry import SOLVER_VALIDATION_STATUS, is_supported


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
