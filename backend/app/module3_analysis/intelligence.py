"""Deterministic engineering analytics; no LLM and no causal claims."""
from __future__ import annotations

import numpy as np
from scipy import stats

from app.module3_analysis.schemas import ExperimentDataset, ObjectiveSpec, SensitivitySpec


class AnalysisInputError(ValueError):
    pass


def _finite_pairs(dataset: ExperimentDataset, first: str, second: str) -> tuple[np.ndarray, np.ndarray, list]:
    evidence = [row for row in dataset.rows if first in row.values and second in row.values]
    return (
        np.asarray([row.values[first] for row in evidence], dtype=float),
        np.asarray([row.values[second] for row in evidence], dtype=float),
        evidence,
    )


def descriptive_statistics(dataset: ExperimentDataset) -> dict:
    output: dict[str, dict] = {}
    for column in dataset.columns:
        values = np.asarray([row.values[column] for row in dataset.rows if column in row.values], dtype=float)
        if values.size == 0:
            continue
        output[column] = {
            "count": int(values.size),
            "missing_count": len(dataset.rows) - int(values.size),
            "mean": float(values.mean()),
            "median": float(np.median(values)),
            "standard_deviation": float(values.std(ddof=1)) if values.size > 1 else 0.0,
            "variance": float(values.var(ddof=1)) if values.size > 1 else 0.0,
            "minimum": float(values.min()),
            "maximum": float(values.max()),
            "quantiles": {
                "q25": float(np.quantile(values, 0.25)),
                "q50": float(np.quantile(values, 0.50)),
                "q75": float(np.quantile(values, 0.75)),
            },
            "interquartile_range": float(np.quantile(values, 0.75) - np.quantile(values, 0.25)),
            "coefficient_of_variation": (
                float(values.std(ddof=1) / abs(values.mean()))
                if values.size > 1 and abs(float(values.mean())) > 1e-15 else None
            ),
            "unit": dataset.units.get(column, "unspecified"),
            "warnings": ["Small sample: descriptive values may be unstable."] if values.size < 5 else [],
        }
    return output


def correlations(dataset: ExperimentDataset, method: str = "both", fdr_alpha: float = 0.05) -> dict:
    if method not in {"pearson", "spearman", "both"}:
        raise AnalysisInputError("Unsupported correlation method")
    if not 0 < fdr_alpha <= 0.25:
        raise AnalysisInputError("FDR alpha must be in (0, 0.25]")
    usable = [
        column for column in dataset.columns
        if column not in dataset.quality.constant_columns and dataset.units.get(column) != "incompatible"
    ]
    relationships: list[dict] = []
    for index, first in enumerate(usable):
        for second in usable[index + 1:]:
            x, y, evidence = _finite_pairs(dataset, first, second)
            if x.size < 3 or np.unique(x).size < 2 or np.unique(y).size < 2:
                continue
            item = {
                "first": first, "second": second, "sample_count": int(x.size),
                "dataset_hash": dataset.dataset_hash,
                "evidence_simulation_ids": [row.simulation_id for row in evidence],
                "evidence_ids": sorted({item for row in evidence for item in row.evidence_ids}),
                "observations": [
                    {"simulation_id": row.simulation_id, first: row.values[first], second: row.values[second]}
                    for row in evidence
                ],
                "warning": "Statistical association only; correlation does not establish causation.",
            }
            if method in {"pearson", "both"}:
                coefficient, p_value = stats.pearsonr(x, y)
                item["pearson"] = {"coefficient": float(coefficient), "p_value": float(p_value)}
            if method in {"spearman", "both"}:
                coefficient, p_value = stats.spearmanr(x, y)
                item["spearman"] = {"coefficient": float(coefficient), "p_value": float(p_value)}
            magnitude = max(
                abs(item.get("pearson", {}).get("coefficient", 0.0)),
                abs(item.get("spearman", {}).get("coefficient", 0.0)),
            )
            item["effect_size_interpretation"] = (
                "negligible" if magnitude < 0.1 else "small" if magnitude < 0.3
                else "moderate" if magnitude < 0.5 else "large" if magnitude < 0.7
                else "very_large"
            )
            item["interpretation_warning"] = (
                "The label describes association magnitude only, not physical importance or causation."
            )
            relationships.append(item)
    # Benjamini-Hochberg correction is applied separately to each declared
    # method across the complete family of tested column pairs.  It controls
    # discovery-rate interpretation without changing the underlying effects.
    for key in ("pearson", "spearman"):
        tested = [(index, item[key]["p_value"]) for index, item in enumerate(relationships) if key in item]
        total = len(tested)
        if not total:
            continue
        ordered = sorted(tested, key=lambda item: (item[1], item[0]))
        adjusted = [0.0] * total
        running = 1.0
        for position in range(total - 1, -1, -1):
            _, p_value = ordered[position]
            running = min(running, p_value * total / (position + 1))
            adjusted[position] = float(min(1.0, running))
        for (relationship_index, _), q_value in zip(ordered, adjusted):
            relationships[relationship_index][key].update({
                "q_value": q_value, "fdr_alpha": fdr_alpha,
                "significant_after_fdr": bool(q_value <= fdr_alpha),
            })
    relationships.sort(
        key=lambda item: (
            -max(abs(item.get("pearson", {}).get("coefficient", 0)),
                 abs(item.get("spearman", {}).get("coefficient", 0))),
            item["first"], item["second"],
        )
    )
    warnings = [
        "Correlation does not establish causation.",
        "Benjamini-Hochberg FDR correction is reported per correlation method; correlation does not establish causation.",
    ]
    if len(dataset.rows) < 3:
        warnings.append("At least three pairwise-valid observations are required for correlation.")
    return {
        "dataset_hash": dataset.dataset_hash, "method": method, "fdr_alpha": fdr_alpha, "relationships": relationships,
        "warnings": warnings,
    }


def regression_sensitivity(dataset: ExperimentDataset, spec: SensitivitySpec) -> dict:
    features = list(dict.fromkeys(spec.features))
    if spec.target in features:
        raise AnalysisInputError("Sensitivity target cannot also be a feature")
    requested = features + [spec.target]
    missing_columns = [name for name in requested if name not in dataset.columns]
    if missing_columns:
        raise AnalysisInputError(f"Unknown dataset columns: {', '.join(missing_columns)}")
    incompatible = [name for name in requested if dataset.units.get(name) == "incompatible"]
    if incompatible:
        raise AnalysisInputError(f"Incompatible units: {', '.join(incompatible)}")
    constant = [name for name in requested if name in dataset.quality.constant_columns]
    if constant:
        raise AnalysisInputError(f"Constant columns cannot be used: {', '.join(constant)}")

    evidence = [row for row in dataset.rows if all(name in row.values for name in requested)]
    minimum = max(5, len(features) + 2)
    if len(evidence) < minimum:
        raise AnalysisInputError(f"Sensitivity requires at least {minimum} complete rows")
    x = np.asarray([[row.values[name] for name in features] for row in evidence], dtype=float)
    y = np.asarray([row.values[spec.target] for row in evidence], dtype=float)
    x_mean, x_std = x.mean(axis=0), x.std(axis=0, ddof=1)
    y_mean, y_std = y.mean(), y.std(ddof=1)
    if np.any(x_std == 0) or y_std == 0:
        raise AnalysisInputError("Sensitivity inputs and target must vary")
    x_scaled = (x - x_mean) / x_std
    y_scaled = (y - y_mean) / y_std
    design = np.column_stack([np.ones(len(evidence)), x_scaled])
    coefficients, _, rank, _ = np.linalg.lstsq(design, y_scaled, rcond=None)
    prediction = design @ coefficients
    residual_sum = float(np.sum((y_scaled - prediction) ** 2))
    total_sum = float(np.sum((y_scaled - y_scaled.mean()) ** 2))
    r_squared = 1.0 - residual_sum / total_sum if total_sum else 0.0
    condition_number = float(np.linalg.cond(x_scaled))
    items = [
        {"feature": name, "standardized_coefficient": float(value), "absolute_importance": abs(float(value))}
        for name, value in zip(features, coefficients[1:])
    ]
    items.sort(key=lambda item: (-item["absolute_importance"], item["feature"]))
    degrees_of_freedom = len(evidence) - design.shape[1]
    adjusted_r_squared = (
        float(1.0 - (1.0 - r_squared) * (len(evidence) - 1) / degrees_of_freedom)
        if degrees_of_freedom > 0 else None
    )
    mse = residual_sum / degrees_of_freedom if degrees_of_freedom > 0 else None
    covariance = np.linalg.pinv(design.T @ design) * mse if mse is not None else None
    standard_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0)) if covariance is not None else None
    hat = design @ np.linalg.pinv(design.T @ design) @ design.T
    leverage = np.diag(hat)
    # VIF is defined on the standardized feature matrix.  Singular designs
    # deliberately report infinity rather than manufacturing a finite value.
    vifs = {}
    for index, name in enumerate(features):
        others = np.delete(x_scaled, index, axis=1)
        if others.shape[1] == 0:
            vifs[name] = 1.0
        else:
            beta, _, rank_other, _ = np.linalg.lstsq(np.column_stack([np.ones(len(x_scaled)), others]), x_scaled[:, index], rcond=None)
            fitted = np.column_stack([np.ones(len(x_scaled)), others]) @ beta
            sst = float(np.sum((x_scaled[:, index] - x_scaled[:, index].mean()) ** 2))
            r2_feature = 1.0 - float(np.sum((x_scaled[:, index] - fitted) ** 2)) / sst if sst else 1.0
            vifs[name] = float("inf") if rank_other < others.shape[1] + 1 or r2_feature >= 1 - 1e-12 else float(1 / (1 - r2_feature))
    bootstrap_seed, bootstrap_count, confidence_level = 20260401, 500, 0.95
    bootstrap_intervals = None
    if len(evidence) >= minimum + 2 and rank == design.shape[1]:
        rng = np.random.default_rng(bootstrap_seed)
        samples = []
        for _ in range(bootstrap_count):
            indexes = rng.integers(0, len(evidence), len(evidence))
            fit, _, fit_rank, _ = np.linalg.lstsq(design[indexes], y_scaled[indexes], rcond=None)
            if fit_rank == design.shape[1] and np.isfinite(fit).all():
                samples.append(fit[1:])
        if samples:
            sample_array = np.asarray(samples)
            alpha = (1 - confidence_level) / 2
            bootstrap_intervals = {
                name: {"lower": float(np.quantile(sample_array[:, index], alpha)), "upper": float(np.quantile(sample_array[:, index], 1 - alpha))}
                for index, name in enumerate(features)
            }
    warnings = ["Regression association is not proof of causation."]
    if rank < design.shape[1] or condition_number > 30:
        warnings.append("Multicollinearity detected; individual coefficients may be unstable.")
    if len(evidence) < 20:
        warnings.append("Small sample: sensitivity estimates may be unstable.")
    if r_squared < 0.5:
        warnings.append("Poor linear fit: first-order coefficients are not a reliable summary of this dataset.")
    return {
        "target": spec.target, "features": items, "sample_count": len(evidence),
        "dataset_hash": dataset.dataset_hash,
        "r_squared": float(r_squared), "adjusted_r_squared": adjusted_r_squared,
        "rank": int(rank), "condition_number": condition_number,
        "variance_inflation_factors": vifs,
        "coefficient_uncertainty": ({name: float(standard_errors[index + 1]) for index, name in enumerate(features)} if standard_errors is not None else None),
        "bootstrap": {
            "seed": bootstrap_seed, "count": bootstrap_count, "confidence_level": confidence_level,
            "coefficient_intervals": bootstrap_intervals,
            "limitation": "Intervals quantify fitted-data sampling uncertainty, not physical-model uncertainty.",
        },
        "residual_diagnostics": {
            "root_mean_squared_standardized_residual": float(np.sqrt(np.mean((y_scaled - prediction) ** 2))),
            "maximum_absolute_standardized_residual": float(np.max(np.abs(y_scaled - prediction))),
            "mean_standardized_residual": float(np.mean(y_scaled - prediction)),
            "rmse": float(np.sqrt(np.mean((y_scaled - prediction) ** 2))),
            "maximum_absolute_residual": float(np.max(np.abs(y_scaled - prediction))),
            "maximum_leverage": float(np.max(leverage)),
            "leverage": [float(value) for value in leverage],
        },
        "evidence_simulation_ids": [row.simulation_id for row in evidence],
        "evidence_ids": sorted({item for row in evidence for item in row.evidence_ids}),
        "observations": [{
            "simulation_id": row.simulation_id,
            "features": {name: row.values[name] for name in features},
            "target": row.values[spec.target],
        } for row in evidence],
        "warnings": warnings,
    }


def _objective_matrix(dataset: ExperimentDataset, objectives: list[ObjectiveSpec]):
    if not objectives:
        return np.empty((0, 0)), []
    names = [item.column for item in objectives]
    unknown = [name for name in names if name not in dataset.columns]
    if unknown:
        raise AnalysisInputError(f"Unknown objective columns: {', '.join(unknown)}")
    incompatible = [name for name in names if dataset.units.get(name) == "incompatible"]
    if incompatible:
        raise AnalysisInputError(f"Incompatible objective units: {', '.join(incompatible)}")
    rows = [row for row in dataset.rows if all(name in row.values for name in names)]
    matrix = np.asarray([[row.values[name] for name in names] for row in rows], dtype=float)
    return matrix, rows


def pareto_front(dataset: ExperimentDataset, objectives: list[ObjectiveSpec]) -> dict:
    matrix, rows = _objective_matrix(dataset, objectives)
    if matrix.size == 0:
        return {
            "objectives": [item.model_dump() for item in objectives],
            "dataset_hash": dataset.dataset_hash,
            "source_simulation_ids": [], "source_design_ids": [],
            "pareto_optimal": [], "dominated": [],
            "warnings": ["No complete objective rows."],
        }
    adjusted = matrix.copy()
    for index, objective in enumerate(objectives):
        if objective.direction == "maximize":
            adjusted[:, index] *= -1
    efficient = np.ones(len(rows), dtype=bool)
    for index in range(len(rows)):
        dominated = np.any(
            np.all(adjusted <= adjusted[index], axis=1)
            & np.any(adjusted < adjusted[index], axis=1)
        )
        efficient[index] = not dominated
    observations = [
        {
            "design_id": row.design_id, "simulation_id": row.simulation_id,
            "objectives": {item.column: row.values[item.column] for item in objectives},
            "evidence_ids": row.evidence_ids,
            "is_pareto_optimal": bool(keep),
            "dominated_by_simulation_ids": [
                other.simulation_id for other, other_values in zip(rows, adjusted)
                if np.all(other_values <= values) and np.any(other_values < values)
            ],
        }
        for row, values, keep in zip(rows, adjusted, efficient)
    ]
    observations.sort(key=lambda item: (item["design_id"] or "", item["simulation_id"]))
    return {
        "objectives": [item.model_dump() for item in objectives],
        "objective_directions": {item.column:item.direction for item in objectives},
        "dataset_hash": dataset.dataset_hash,
        "source_simulation_ids": sorted(row.simulation_id for row in rows),
        "source_design_ids": sorted({row.design_id for row in rows if row.design_id}),
        "pareto_optimal": [item for item in observations if item["is_pareto_optimal"]],
        "dominated": [item for item in observations if not item["is_pareto_optimal"]],
        "warnings": ["Pareto membership describes the selected objectives only; it is not a universal optimum."],
    }


def weighted_ranking(dataset: ExperimentDataset, objectives: list[ObjectiveSpec]) -> dict:
    matrix, rows = _objective_matrix(dataset, objectives)
    if matrix.size == 0:
        return {"dataset_hash":dataset.dataset_hash,"objectives":[item.model_dump() for item in objectives],
                "normalization_method":"min_max","ranking": [], "warnings": ["No complete objective rows."]}
    weights = np.asarray([item.weight for item in objectives], dtype=float)
    weights /= weights.sum()
    normalized = np.zeros_like(matrix, dtype=float)
    warnings: list[str] = []
    for index, objective in enumerate(objectives):
        minimum, maximum = matrix[:, index].min(), matrix[:, index].max()
        if maximum == minimum:
            normalized[:, index] = 0.5
            warnings.append(f"Objective {objective.column} is constant and does not distinguish designs.")
        elif objective.direction == "maximize":
            normalized[:, index] = (matrix[:, index] - minimum) / (maximum - minimum)
        else:
            normalized[:, index] = (maximum - matrix[:, index]) / (maximum - minimum)
    contributions = normalized * weights
    scores = contributions.sum(axis=1)
    ranking = []
    for row, score, parts in zip(rows, scores, contributions):
        ranking.append({
            "design_id": row.design_id, "simulation_id": row.simulation_id,
            "score": float(score),
            "contributions": {item.column: float(value) for item, value in zip(objectives, parts)},
            "objective_values": {item.column: row.values[item.column] for item in objectives},
            "evidence_ids": row.evidence_ids,
        })
    ranking.sort(key=lambda item: (-item["score"], item["design_id"] or "", item["simulation_id"]))
    for index, item in enumerate(ranking, start=1):
        item["rank"] = index
    return {
        "dataset_hash":dataset.dataset_hash,
        "source_simulation_ids":sorted(row.simulation_id for row in rows),
        "source_design_ids":sorted({row.design_id for row in rows if row.design_id}),
        "objectives":[item.model_dump() for item in objectives],
        "objective_directions":{item.column:item.direction for item in objectives},
        "normalization_method":"min_max",
        "ranking": ranking,
        "normalized_weights": dict(zip([o.column for o in objectives], weights.tolist())),
        "warnings": warnings,
    }


def grounded_recommendations(
    ranking: dict, pareto: dict, correlation_result: dict | None = None,
    sensitivity_result: dict | None = None,
) -> list[dict]:
    if not ranking.get("ranking"):
        return []
    best = ranking["ranking"][0]
    pareto_ids = {item["simulation_id"] for item in pareto.get("pareto_optimal", [])}
    recommendations = [{
        "type": "ranked_candidate",
        "design_id": best["design_id"],
        "simulation_id": best["simulation_id"],
        "statement": "This candidate has the highest deterministic weighted score for the declared objectives.",
        "evidence": {
            "score": best["score"], "rank": best["rank"],
            "objective_values": best["objective_values"],
            "contributions": best["contributions"],
            "pareto_member": best["simulation_id"] in pareto_ids,
            "source_ids": best["evidence_ids"],
        },
        "confidence": "bounded_by_dataset_quality",
        "warnings": [
            "Recommendation depends on user-supplied objective directions and weights.",
            "Observed associations do not establish physical causation.",
        ],
    }]
    for relationship in (correlation_result or {}).get("relationships", [])[:3]:
        method = "spearman" if "spearman" in relationship else "pearson"
        coefficient = relationship[method]["coefficient"]
        recommendations.append({
            "type": "observed_association",
            "design_id": None,
            "simulation_id": None,
            "statement": (
                f"Within the analyzed dataset, {relationship['first']} was "
                f"{'positively' if coefficient >= 0 else 'negatively'} associated with "
                f"{relationship['second']}."
            ),
            "evidence": {
                "method": method, "coefficient": coefficient,
                "sample_count": relationship["sample_count"],
                "source_simulation_ids": relationship["evidence_simulation_ids"],
                "source_ids": relationship.get("evidence_ids", []),
                "dataset_hash": relationship.get("dataset_hash"),
            },
            "confidence": "not_quantified",
            "warnings": ["This association is not evidence of causation."],
        })
    if sensitivity_result:
        strongest = sensitivity_result["features"][0]
        recommendations.append({
            "type": "first_order_sensitivity_estimate",
            "design_id": None,
            "simulation_id": None,
            "statement": (
                f"{strongest['feature']} had the largest absolute standardized coefficient "
                f"for {sensitivity_result['target']} in the fitted linear model."
            ),
            "evidence": {
                "method": "standardized_linear_regression", "sample_count": sensitivity_result["sample_count"],
                "standardized_coefficient": strongest["standardized_coefficient"],
                "r_squared": sensitivity_result["r_squared"],
                "source_simulation_ids": sensitivity_result["evidence_simulation_ids"],
                "source_ids": sensitivity_result.get("evidence_ids", []),
                "dataset_hash": sensitivity_result.get("dataset_hash"),
            },
            "confidence": "bounded_by_model_fit_and_diagnostics",
            "warnings": list(sensitivity_result["warnings"]),
        })
    return recommendations[:8]
