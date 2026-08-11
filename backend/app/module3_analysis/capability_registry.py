"""Authoritative contract for the deterministic Module 3 research path."""
from __future__ import annotations

from typing import Any


def _method(method_id: str, inputs: list[str], outputs: list[str], minimum: str, limitations: list[str]) -> dict[str, Any]:
    return {"method_id": method_id, "implementation_status": "real", "input_requirements": inputs,
            "minimum_sample_rules": minimum, "assumptions": ["observational experiment data"], "outputs": outputs,
            "warnings": ["Correlation and fitted coefficients do not establish causation."],
            "known_limitations": limitations, "causal_interpretation_allowed": False,
            "implementation_reference": "app.module3_analysis.intelligence"}


ANALYSIS_CAPABILITY_REGISTRY: dict[str, dict[str, Any]] = {
    "descriptive_statistics": _method("descriptive_statistics", ["numeric dataset columns"], ["count", "mean", "standard deviation", "range"], "At least 1 finite observation.", ["Describes supplied data only."]),
    "pearson_correlation": _method("pearson_correlation", ["two numeric columns"], ["coefficient", "sample count"], "At least 2 paired finite observations.", ["Linear association only; undefined for constant series."]),
    "spearman_correlation": _method("spearman_correlation", ["two numeric columns"], ["rank coefficient", "sample count"], "At least 2 paired finite observations.", ["Rank association only; ties reduce interpretability."]),
    "standardized_linear_regression_sensitivity": _method("standardized_linear_regression_sensitivity", ["numeric features", "numeric target"], ["standardized coefficients", "R squared", "diagnostics"], "Requires enough rows for the fitted feature set.", ["First-order linear fit; not global sensitivity or uncertainty quantification."]),
    "pareto_frontier": _method("pareto_frontier", ["two or more objective columns"], ["non-dominated design IDs"], "At least 1 candidate design.", ["Depends on chosen objectives and optimization directions."]),
    "weighted_ranking": _method("weighted_ranking", ["candidate metrics", "explicit weights"], ["scores", "ranked candidates"], "At least 1 candidate design.", ["Ranking reflects user-supplied weights; it is not optimization."]),
    "grounded_deterministic_recommendations": _method("grounded_deterministic_recommendations", ["completed deterministic analysis"], ["evidence-linked recommendations"], "Only available when its required analysis evidence exists.", ["Recommendations are reviewable decision support, not validated design approval."]),
}


def list_analysis_capabilities() -> list[dict[str, Any]]:
    return list(ANALYSIS_CAPABILITY_REGISTRY.values())
