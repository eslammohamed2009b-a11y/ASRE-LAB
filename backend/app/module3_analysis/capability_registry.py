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
    "study_scoped_dataset": _method("study_scoped_dataset", ["explicit owner-scoped linked authoritative simulations"], ["dataset hash", "source hashes", "run-quality screening", "evidence IDs"], "At least one explicitly linked simulation; invalid rows are reported and excluded.", ["Does not include unrelated experiment runs."]),
    "run_quality_screening": _method("run_quality_screening", ["persisted simulation status, result, and scientific evidence"], ["validity category", "inclusion", "exact reasons"], "Every linked simulation is screened.", ["Uses solver-specific persisted contracts; no universal residual threshold is invented."]),
    "robust_outlier_diagnostics": _method("robust_outlier_diagnostics", ["at least 3 numeric observations"], ["MAD robust-z flags"], "MAD must be nonzero.", ["Flags are not automatic deletion or fraud findings."]),
    "fdr_corrected_association_analysis": _method("fdr_corrected_association_analysis", ["pairwise-valid nonconstant numeric columns"], ["raw p-values", "Benjamini-Hochberg q-values", "effect sizes"], "At least 3 pairwise-valid observations.", ["Association does not establish causation."]),
    "enhanced_regression_sensitivity": _method("enhanced_regression_sensitivity", ["complete nonconstant feature and target rows"], ["standardized coefficients", "bootstrap intervals", "VIF", "leverage"], "At least max(5, features + 2) rows.", ["Bootstrap describes fitted-data sampling uncertainty, not physical uncertainty."]),
    "second_order_response_surface": _method("second_order_response_surface", ["declared Study factors and response"], ["quadratic terms", "interactions", "cross-validation diagnostics"], "At least number_of_terms + 2 complete rows.", ["Sampled-region surrogate only; it is not a physical solver and must not be extrapolated."]),
    "full_factorial_doe": _method("full_factorial_doe", ["explicit numeric factor levels"], ["bounded Cartesian coordinates", "DOE hash"], "Cartesian expansion must not exceed 500 candidates.", ["No silent truncation."]),
    "latin_hypercube_doe": _method("latin_hypercube_doe", ["numeric factor bounds, explicit count and seed"], ["deterministic coordinates", "space-filling diagnostics"], "2-500 requested candidates.", ["Diagnostics do not claim global optimality."]),
    "empirical_repeatability": _method("empirical_repeatability", ["at least two equivalent factor-coordinate runs"], ["mean", "standard deviation", "coefficient of variation"], "At least two replicates per reported group.", ["Observed repeatability is not full physical-model uncertainty quantification."]),
    "constraint_evaluation": _method("constraint_evaluation", ["declared persisted response constraints"], ["feasible", "infeasible", "unknown_due_to_missing_data"], "Each candidate is evaluated without silent removal.", ["Only declared response values are evaluated."]),
    "ranking_stability": _method("ranking_stability", ["weighted objectives and feasible candidates"], ["rank ranges", "rank-1 stability fraction"], "At least one complete feasible objective row.", ["Depends on user-declared weights and bounded ±10% perturbations."]),
    "deterministic_next_experiment": _method("deterministic_next_experiment", ["factor bounds and study dataset"], ["bounded maximin EXPLORE proposals", "proposal hash"], "16-8192 deterministic Sobol candidate pool.", ["Proposals improve sampled coverage; they are not physically optimal claims."]),
}


def list_analysis_capabilities() -> list[dict[str, Any]]:
    return list(ANALYSIS_CAPABILITY_REGISTRY.values())
