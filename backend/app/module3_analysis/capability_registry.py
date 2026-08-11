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
    "descriptive_statistics": _method("descriptive_statistics", ["one or more numeric dataset columns"], ["count", "missing_count", "mean", "median", "standard_deviation", "variance", "minimum", "maximum", "quantiles", "interquartile_range", "coefficient_of_variation", "unit", "warnings"], "At least 1 available numeric value per reported column.", ["Describes supplied data only."]),
    "pearson_correlation": _method("pearson_correlation", ["two non-constant, unit-compatible numeric columns"], ["coefficient", "p_value", "sample_count", "evidence_simulation_ids", "effect_size_interpretation", "warnings"], "At least 3 pairwise-valid observations, with at least 2 distinct values in each column.", ["Linear association only; undefined for constant series; p-values are uncorrected for multiple comparisons."]),
    "spearman_correlation": _method("spearman_correlation", ["two non-constant, unit-compatible numeric columns"], ["coefficient", "p_value", "sample_count", "evidence_simulation_ids", "effect_size_interpretation", "warnings"], "At least 3 pairwise-valid observations, with at least 2 distinct values in each column.", ["Rank association only; ties reduce interpretability; p-values are uncorrected for multiple comparisons."]),
    "standardized_linear_regression_sensitivity": _method("standardized_linear_regression_sensitivity", ["non-constant, unit-compatible numeric features and target"], ["standardized_coefficients", "absolute_importance", "sample_count", "r_squared", "condition_number", "residual_diagnostics", "evidence_simulation_ids", "warnings"], "At least max(5, number_of_features + 2) complete rows.", ["First-order linear fit; not global sensitivity or uncertainty quantification."]),
    "pareto_frontier": _method("pareto_frontier", ["one or more unit-compatible objectives with directions"], ["pareto_optimal", "dominated", "objectives", "warnings"], "At least 1 complete objective row; no result is returned for no complete rows.", ["Depends on chosen objectives and optimization directions."]),
    "weighted_ranking": _method("weighted_ranking", ["one or more unit-compatible objectives with positive weights and directions"], ["ranking", "normalized_weights", "warnings"], "At least 1 complete objective row; no ranking is returned for no complete rows.", ["Ranking reflects user-supplied weights; it is not optimization."]),
    "grounded_deterministic_recommendations": _method("grounded_deterministic_recommendations", ["non-empty deterministic weighted ranking; optional Pareto, correlation, or sensitivity evidence"], ["type", "statement", "evidence", "confidence", "warnings"], "At least one ranked candidate; otherwise returns no recommendations.", ["Recommendations are reviewable decision support, not validated design approval."]),
}


def list_analysis_capabilities() -> list[dict[str, Any]]:
    return list(ANALYSIS_CAPABILITY_REGISTRY.values())
