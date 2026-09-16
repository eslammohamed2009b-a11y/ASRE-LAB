"""Bounded deterministic Research Study engine built on Module 3 records.

The engine consumes persisted simulation/evidence records only.  It does not
execute simulations, evaluate client formulas, or make causal claims.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import uuid
from datetime import datetime, timezone

import numpy as np
from scipy import stats
from scipy.stats import qmc

from app.core.repository import AnalysisRecord, PersistenceRepository
from app.module3_analysis.dataset import build_experiment_dataset, scientific_dataset_hash
from app.module3_analysis.intelligence import (
    AnalysisInputError, correlations, descriptive_statistics, pareto_front,
    regression_sensitivity, weighted_ranking,
)
from app.module3_analysis.schemas import (
    ConstraintSpec, DOEConfig, ExperimentDataset, NextExperimentRequest,
    StudyAnalysisRequest, StudyFactor, StudyResponse,
)
from app.v2.repository import EvidenceRepository


ENGINE_VERSION = "4.0"
MAX_FACTORS, MAX_RESPONSES, MAX_LINKED_RUNS = 16, 32, 5000
MAX_FACTORIAL, MAX_DOE_POINTS, MAX_CANDIDATES = 500, 500, 8192


class StudyError(ValueError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


SCIENCE_FIELDS = (
    "research_question", "hypothesis", "factors", "responses", "constraints",
    "objectives", "doe", "linked_simulation_ids", "baseline_simulation_id",
)


def definition_hash(metadata: dict) -> str:
    """Stable identity for science-changing Study fields only."""
    return _hash({key: metadata.get(key) for key in SCIENCE_FIELDS})


def normalized_metadata(metadata: dict, *, study_id: str, owner_id: str, experiment_id: str) -> dict:
    data = dict(metadata)
    data.setdefault("schema_version", "research-study-v1")
    data.setdefault("revision", 1)
    data.setdefault("status", "draft")
    data.setdefault("hypothesis", None)
    data.setdefault("factors", [])
    data.setdefault("responses", [])
    data.setdefault("constraints", [])
    data.setdefault("objectives", [])
    data.setdefault("doe", None)
    data.setdefault("linked_simulation_ids", [])
    data.setdefault("analysis_ids", [])
    data.setdefault("latest_analysis_id", None)
    data.setdefault("warnings", [])
    data.setdefault("limitations", [
        "Study findings are bounded to authoritative linked simulations and sampled factor ranges.",
        "Statistical association and fitted-model results do not establish causation.",
    ])
    data["study_id"], data["owner_id"], data["experiment_id"] = study_id, owner_id, experiment_id
    data["definition_hash"] = definition_hash(data)
    return data


def assert_definition(metadata: dict) -> None:
    factors = [StudyFactor.model_validate(item) for item in metadata.get("factors", [])]
    responses = [StudyResponse.model_validate(item) for item in metadata.get("responses", [])]
    constraints = [ConstraintSpec.model_validate(item) for item in metadata.get("constraints", [])]
    if len(factors) > MAX_FACTORS or len(responses) > MAX_RESPONSES:
        raise StudyError("Study exceeds bounded factor or response limit")
    if len({item.name for item in factors}) != len(factors):
        raise StudyError("Study factor names must be unique")
    if len({item.name for item in responses}) != len(responses):
        raise StudyError("Study response names must be unique")
    columns = [item.source_path for item in factors] + [item.column for item in responses]
    if len(columns) != len(set(columns)):
        raise StudyError("Study factors and responses must bind unique source columns")
    for constraint in constraints:
        if constraint.column not in {item.column for item in responses}:
            raise StudyError("Constraints must bind a declared Study response")


def _evidence_ids(repository, user_id: str, simulation_id: str) -> list[str]:
    records = EvidenceRepository(repository=repository).list_scientific_for_simulation(user_id, simulation_id)
    return sorted(record["id"] for record in records)


def build_study_dataset(
    repository: PersistenceRepository, experiment_id: str, user_id: str, metadata: dict,
    *, include_nonconverged: bool = False,
) -> tuple[ExperimentDataset, list[dict]]:
    """Scope a dataset to explicit Study links and report every screening result."""
    linked = list(metadata.get("linked_simulation_ids", []))
    if not linked:
        raise StudyError("Study has no linked simulations")
    if len(linked) > MAX_LINKED_RUNS or len(linked) != len(set(linked)):
        raise StudyError("Study linked simulations must be distinct and within the bounded limit")
    all_jobs = {job.id: job for job in repository.list_simulation_jobs_for_experiment(experiment_id)}
    base = build_experiment_dataset(
        repository, experiment_id, user_id, include_nonconverged=True,
        require_authoritative_evidence=False,
    )
    row_by_id = {row.simulation_id: row for row in base.rows}
    screens: list[dict] = []
    included = []
    for simulation_id in linked:
        job = all_jobs.get(simulation_id)
        result = repository.get_simulation_result(simulation_id) if job else None
        reasons: list[str] = []
        evidence_ids: list[str] = []
        status = "valid"
        if job is None or job.user_id != user_id:
            status, reasons = "scientifically_invalid", ["Simulation is not an owned member of this Study experiment."]
        elif job.status not in {"completed", "partial_failure"} or result is None:
            status, reasons = "numerically_failed", ["Simulation has no completed persisted result."]
        elif not result.converged:
            status, reasons = "non_converged", ["Persisted solver result is not converged."]
        else:
            evidence_records = EvidenceRepository(repository=repository).list_scientific_for_simulation(user_id, simulation_id)
            evidence_ids = sorted(item["id"] for item in evidence_records)
            if not any(item["record_type"] == "scientific_numerical_result" for item in evidence_records):
                status, reasons = "evidence_incomplete", ["Authoritative simulation evidence is missing."]
            elif result.warnings:
                status, reasons = "valid_with_warnings", ["Persisted solver warnings require review."]
        screen = {
            "simulation_id": simulation_id, "status": status, "included": status in {"valid", "valid_with_warnings"},
            "reasons": reasons, "result_reproducibility_hash": result.reproducibility_hash if result else None,
            "evidence_ids": evidence_ids if job and result is not None and result.converged else [],
        }
        screens.append(screen)
        if screen["included"] and simulation_id in row_by_id:
            row = row_by_id[simulation_id].model_copy(update={"evidence_ids": screen["evidence_ids"]})
            included.append(row)
        elif status == "non_converged" and include_nonconverged:
            # Non-converged rows remain visible in screening, never silently
            # enter authoritative fitted analyses.
            screen["reasons"].append("Excluded from authoritative numerical analysis.")
    columns = sorted({name for row in included for name in row.values})
    units = {name: base.units[name] for name in columns}
    missing = {name: sum(name not in row.values for row in included) for name in columns if any(name not in row.values for row in included)}
    constants = [name for name in columns if len({row.values[name] for row in included if name in row.values}) <= 1]
    quality = base.quality.model_copy(update={
        "source_simulation_count": len(linked), "valid_row_count": len(included),
        "excluded_row_count": len(linked) - len(included), "missing_value_counts": missing,
        "constant_columns": constants,
        "warnings": list(base.quality.warnings) + (["Study dataset excludes invalid, failed, non-converged, or evidence-incomplete runs."] if len(included) != len(linked) else []),
    })
    dataset = ExperimentDataset(
        experiment_id=experiment_id, version="4.0", rows=included, columns=columns, units=units,
        quality=quality, dataset_hash=_hash({
            "study_definition_hash": metadata["definition_hash"],
            "base_dataset_hash": scientific_dataset_hash(experiment_id, included, columns, units),
            "linked_simulation_ids": linked,
            "source_result_hashes": [item["result_reproducibility_hash"] for item in screens],
            "source_evidence_ids": [item["evidence_ids"] for item in screens],
        }),
    )
    return dataset, screens


def evaluate_constraints(dataset: ExperimentDataset, constraints: list[dict]) -> dict:
    specs = [ConstraintSpec.model_validate(item) for item in constraints]
    rows = []
    for row in dataset.rows:
        evaluated = []
        for spec in specs:
            if spec.column not in row.values:
                state, reason = "unknown_due_to_missing_data", "Response value is unavailable."
            else:
                value = row.values[spec.column]
                passed = (value <= spec.value if spec.operator == "<=" else value >= spec.value if spec.operator == ">=" else spec.lower <= value <= spec.upper)
                state, reason = ("feasible", "Constraint satisfied.") if passed else ("infeasible", "Constraint violated.")
            evaluated.append({"name": spec.name, "column": spec.column, "state": state, "reason": reason})
        state = "unknown_due_to_missing_data" if any(item["state"] == "unknown_due_to_missing_data" for item in evaluated) else ("infeasible" if any(item["state"] == "infeasible" for item in evaluated) else "feasible")
        rows.append({"simulation_id": row.simulation_id, "design_id": row.design_id, "state": state, "constraints": evaluated, "evidence_ids": row.evidence_ids})
    return {"constraints": [item.model_dump(mode="json") for item in specs], "candidates": rows,
            "feasible_simulation_ids": sorted(item["simulation_id"] for item in rows if item["state"] == "feasible")}


def _subset(dataset: ExperimentDataset, ids: set[str]) -> ExperimentDataset:
    rows = [row for row in dataset.rows if row.simulation_id in ids]
    return dataset.model_copy(update={"rows": rows, "dataset_hash": _hash({"parent": dataset.dataset_hash, "ids": sorted(ids)})})


def anomaly_diagnostics(dataset: ExperimentDataset, response_columns: list[str]) -> dict:
    flags = []
    for column in response_columns:
        values = [(row, row.values[column]) for row in dataset.rows if column in row.values]
        if len(values) < 3:
            continue
        array = np.asarray([item[1] for item in values], dtype=float)
        median = float(np.median(array)); mad = float(np.median(np.abs(array - median)))
        if mad <= 1e-15:
            continue
        scores = 0.67448975 * (array - median) / mad
        for (row, _), score in zip(values, scores):
            if abs(float(score)) > 3.5:
                flags.append({"simulation_id": row.simulation_id, "affected_metric": column,
                              "diagnostic": "median_absolute_deviation_robust_z", "score": float(score),
                              "threshold": 3.5, "warning": "Potential anomaly retained unless an explicit quality rule excludes it."})
    return {"flags": flags, "limitations": ["Flags are diagnostics, not automatic data deletion or fraud findings."]}


def response_surface(dataset: ExperimentDataset, factors: list[dict], response_column: str | None) -> dict:
    if response_column is None:
        return {"status": "not_applicable", "reason": "No response-surface response was requested."}
    factor_specs = [StudyFactor.model_validate(item) for item in factors]
    names = [item.source_path for item in factor_specs]
    if response_column not in dataset.columns or any(name not in dataset.columns for name in names):
        raise StudyError("Response surface columns are unavailable in the Study dataset")
    rows = [row for row in dataset.rows if response_column in row.values and all(name in row.values for name in names)]
    term_names = ["intercept", *names, *[f"{name}^2" for name in names], *[f"{names[i]}*{names[j]}" for i in range(len(names)) for j in range(i + 1, len(names))]]
    minimum = len(term_names) + 2
    if len(rows) < minimum:
        return {"status": "not_applicable", "reason": f"Second-order response surface requires at least {minimum} complete rows.", "term_count": len(term_names)}
    raw = np.asarray([[row.values[name] for name in names] for row in rows], dtype=float)
    means, scales = raw.mean(axis=0), raw.std(axis=0, ddof=1)
    if np.any(scales <= 0):
        return {"status": "not_applicable", "reason": "Response surface factors must vary."}
    x = (raw - means) / scales
    columns = [np.ones(len(rows)), *[x[:, i] for i in range(len(names))], *[x[:, i] ** 2 for i in range(len(names))], *[x[:, i] * x[:, j] for i in range(len(names)) for j in range(i + 1, len(names))]]
    design = np.column_stack(columns); y = np.asarray([row.values[response_column] for row in rows], dtype=float)
    coefficient, _, rank, _ = np.linalg.lstsq(design, y, rcond=None); predicted = design @ coefficient
    sse = float(np.sum((y - predicted) ** 2)); sst = float(np.sum((y - y.mean()) ** 2)); r2 = 1 - sse / sst if sst else 0.0
    adjusted = 1 - (1-r2)*(len(rows)-1)/(len(rows)-design.shape[1]) if len(rows) > design.shape[1] else None
    folds = min(5, len(rows))
    cv_predictions = np.full(len(rows), np.nan)
    if folds >= 3 and len(rows) >= design.shape[1] + folds:
        for fold in range(folds):
            train = np.asarray([index for index in range(len(rows)) if index % folds != fold])
            test = np.asarray([index for index in range(len(rows)) if index % folds == fold])
            fit, _, fit_rank, _ = np.linalg.lstsq(design[train], y[train], rcond=None)
            if fit_rank == design.shape[1]: cv_predictions[test] = design[test] @ fit
    cv_ok = np.isfinite(cv_predictions).all()
    cv_rmse = float(np.sqrt(np.mean((y - cv_predictions) ** 2))) if cv_ok else None
    cv_r2 = float(1 - np.sum((y-cv_predictions)**2)/sst) if cv_ok and sst else None
    interactions = {name: float(value) for name, value in zip(term_names, coefficient) if "*" in name}
    curvature = {name: float(value) for name, value in zip(term_names, coefficient) if name.endswith("^2")}
    return {"status": "completed", "response": response_column, "factor_columns": names, "term_names": term_names,
            "coefficients": {name: float(value) for name, value in zip(term_names, coefficient)},
            "training_r_squared": float(r2), "adjusted_r_squared": adjusted, "rank": int(rank),
            "condition_number": float(np.linalg.cond(design)), "residual_rmse": float(np.sqrt(np.mean((y-predicted)**2))),
            "maximum_absolute_residual": float(np.max(np.abs(y-predicted))), "cross_validation": {"status": "completed" if cv_ok else "not_applicable", "folds": folds, "partition": "index_modulo", "rmse": cv_rmse, "r_squared": cv_r2},
            "curvature_indicators": curvature, "interaction_indicators": interactions,
            "evidence_simulation_ids": [row.simulation_id for row in rows],
            "warnings": ["Response surface is a surrogate limited to the sampled Study region; it is not a physical solver."]}


def robustness(dataset: ExperimentDataset, factors: list[dict], responses: list[dict]) -> dict:
    factor_specs = [StudyFactor.model_validate(item) for item in factors]
    response_specs = [StudyResponse.model_validate(item) for item in responses]
    if not factor_specs:
        return {"status": "not_applicable", "reason": "No Study factors are declared."}
    groups: dict[tuple, list] = {}
    for row in dataset.rows:
        if not all(item.source_path in row.values for item in factor_specs):
            continue
        key = tuple(round((row.values[item.source_path] - item.minimum) / (item.maximum - item.minimum), 10) for item in factor_specs)
        groups.setdefault(key, []).append(row)
    reported = []
    for key, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        metrics = {}
        for response in response_specs:
            values = np.asarray([row.values[response.column] for row in rows if response.column in row.values], dtype=float)
            if len(values) < 2: continue
            mean, std = float(values.mean()), float(values.std(ddof=1))
            metrics[response.column] = {"replicate_count": len(values), "mean": mean, "standard_deviation": std,
                                        "coefficient_of_variation": std/abs(mean) if abs(mean)>1e-15 else None,
                                        "range": float(values.max()-values.min()),
                                        "confidence_interval_95": [float(mean-1.96*std/math.sqrt(len(values))), float(mean+1.96*std/math.sqrt(len(values)))] if len(values)>=3 else None}
        if metrics: reported.append({"factor_coordinate": dict(zip([item.source_path for item in factor_specs], key)), "metrics": metrics, "simulation_ids": [row.simulation_id for row in rows]})
    return {"status": "completed" if reported else "not_applicable", "groups": reported,
            "limitation": "Empirical repeatability is observed robustness, not physical-model uncertainty quantification."}


def generate_doe(factors: list[dict], config: DOEConfig) -> dict:
    specs = [StudyFactor.model_validate(item) for item in factors]
    if not specs: raise StudyError("DOE requires at least one varied numeric factor")
    if config.method == "full_factorial":
        levels = []
        for factor in specs:
            values = config.levels.get(factor.name) or factor.allowed_values
            if not values or len(set(values)) < 2: raise StudyError("Full factorial requires explicit levels for every factor")
            if any(value < factor.minimum or value > factor.maximum for value in values): raise StudyError("DOE level lies outside factor bounds")
            levels.append(sorted(set(float(value) for value in values)))
        count = math.prod(len(item) for item in levels)
        if count > MAX_FACTORIAL: raise StudyError(f"Full factorial exceeds the {MAX_FACTORIAL}-candidate limit")
        points = [dict(zip([factor.name for factor in specs], values)) for values in itertools.product(*levels)]
        seed = None
    else:
        if config.count is None or config.seed is None: raise StudyError("Latin Hypercube requires explicit count and seed")
        if config.count > MAX_DOE_POINTS: raise StudyError(f"Latin Hypercube exceeds the {MAX_DOE_POINTS}-candidate limit")
        sample = qmc.LatinHypercube(d=len(specs), seed=config.seed).random(config.count)
        points = [{factor.name: float(factor.minimum + sample[index, column]*(factor.maximum-factor.minimum)) for column, factor in enumerate(specs)} for index in range(config.count)]
        seed = config.seed
    normalized = np.asarray([[(point[factor.name]-factor.minimum)/(factor.maximum-factor.minimum) for factor in specs] for point in points])
    distances = np.sqrt(((normalized[:, None, :] - normalized[None, :, :]) ** 2).sum(axis=2)) if len(points)>1 else np.asarray([])
    if len(points) > 1:
        nearest_matrix = distances.copy(); np.fill_diagonal(nearest_matrix, np.inf)
        nearest = np.min(nearest_matrix, axis=1)
    else:
        nearest = np.asarray([])
    payload = {"method": config.method, "seed": seed, "factors": [item.model_dump(mode="json") for item in specs], "requested_count": len(points), "coordinates": points}
    return {**payload, "doe_hash": _hash(payload), "quality": {"minimum_normalized_pairwise_distance": float(np.min(distances[distances>0])) if distances.size and np.any(distances>0) else None, "mean_nearest_neighbor_distance": float(nearest.mean()) if nearest.size else None, "per_factor_coverage": {factor.name: [min(point[factor.name] for point in points), max(point[factor.name] for point in points)] for factor in specs}, "duplicate_count": len(points)-len({tuple(point.items()) for point in points}), "statement": "Space-filling diagnostics; no global-optimality claim is made."}, "warnings": []}


def next_experiments(dataset: ExperimentDataset, factors: list[dict], request: NextExperimentRequest, *, source_analysis_id: str | None = None) -> dict:
    specs = [StudyFactor.model_validate(item) for item in factors]
    if not specs: return {"proposals": [], "warnings": ["No factors are declared for deterministic exploration."]}
    sampler = qmc.Sobol(d=len(specs), scramble=True, seed=request.seed)
    power = int(math.ceil(math.log2(request.candidate_count)))
    normalized = sampler.random_base2(power)[:request.candidate_count]
    existing = np.asarray([[(row.values[spec.source_path]-spec.minimum)/(spec.maximum-spec.minimum) for spec in specs] for row in dataset.rows if all(spec.source_path in row.values for spec in specs)], dtype=float)
    selected = []
    # A proposal that repeats an existing factor coordinate carries no new
    # coverage information and is therefore rejected deterministically.
    remaining = [index for index in range(len(normalized)) if not existing.size or float(np.min(np.linalg.norm(existing-normalized[index], axis=1))) > 1e-12]
    while remaining and len(selected) < request.count:
        scores = []
        for index in remaining:
            comparisons = existing if not selected else np.vstack([existing, normalized[selected]]) if existing.size else normalized[selected]
            score = 1.0 if comparisons.size == 0 else float(np.min(np.linalg.norm(comparisons-normalized[index], axis=1)))
            scores.append((score, index))
        _, chosen = max(scores, key=lambda item: (item[0], -item[1])); selected.append(chosen); remaining.remove(chosen)
    proposals = [{"proposal_type": "EXPLORE", "factor_coordinates": {spec.name: float(spec.minimum + normalized[index, axis]*(spec.maximum-spec.minimum)) for axis, spec in enumerate(specs)}, "score_components": {"minimum_normalized_distance_to_existing": (1.0 if existing.size == 0 else float(np.min(np.linalg.norm(existing-normalized[index], axis=1))) )}, "reason": "Deterministic maximin coverage proposal within declared Study bounds.", "source_analysis_ids": [source_analysis_id] if source_analysis_id else [], "source_simulation_ids": [row.simulation_id for row in dataset.rows], "warnings": ["Proposal improves sampled coverage; it is not claimed physically optimal."]} for index in selected]
    payload = {"seed": request.seed, "candidate_count": request.candidate_count, "proposals": proposals}
    return {**payload, "proposal_hash": _hash(payload), "limitations": ["Sobol candidate generation is a bounded search pool, not a Sobol sensitivity analysis."]}


def _rank_stability(dataset: ExperimentDataset, objectives: list) -> dict:
    if not objectives: return {"status": "not_applicable"}
    base = weighted_ranking(dataset, objectives).get("ranking", [])
    if not base: return {"status": "not_applicable"}
    variations = []
    for index in range(len(objectives)):
        for sign in (-1, 1):
            changed = [item.model_copy(update={"weight": item.weight * (1 + sign*0.1)}) if pos == index else item for pos, item in enumerate(objectives)]
            variations.append(weighted_ranking(dataset, changed).get("ranking", []))
    ranks = {item["simulation_id"]: [item["rank"]] for item in base}
    for ranking in variations:
        for item in ranking: ranks.setdefault(item["simulation_id"], []).append(item["rank"])
    winner = base[0]["simulation_id"]
    return {"status": "completed", "perturbation_envelope": "±10% per objective weight before renormalization", "rank_1_stability_fraction": sum(ranking and ranking[0]["simulation_id"] == winner for ranking in variations) / len(variations) if variations else 1.0, "candidate_rank_ranges": {key: {"minimum": min(value), "maximum": max(value)} for key, value in ranks.items()}}


def hypothesis_result(metadata: dict, dataset: ExperimentDataset) -> dict:
    hypothesis = metadata.get("hypothesis")
    if not isinstance(hypothesis, dict) or not hypothesis.get("response_column") or hypothesis.get("threshold") is None:
        return {"status": "inconclusive", "reason": "No mathematically evaluable predeclared response criterion."}
    column = hypothesis["response_column"]
    values = np.asarray([row.values[column] for row in dataset.rows if column in row.values], dtype=float)
    if values.size == 0: return {"status": "inconclusive", "reason": "Predeclared response is unavailable."}
    direction, threshold = hypothesis.get("expected_direction"), float(hypothesis["threshold"])
    met = bool(values.mean() >= threshold) if direction == "at_least" else bool(values.mean() <= threshold) if direction == "at_most" else None
    return {"status": "evidence_consistent" if met else "evidence_not_consistent" if met is not None else "inconclusive", "criterion_met": met, "response_column": column, "observed_mean": float(values.mean()), "threshold": threshold, "limitation": "This evaluates only a predeclared numeric criterion; it does not prove or reject a scientific hypothesis."}


def run_study_analysis(repository: PersistenceRepository, record, user_id: str, metadata: dict, request: StudyAnalysisRequest) -> AnalysisRecord:
    assert_definition(metadata)
    dataset, screening = build_study_dataset(repository, record.id, user_id, metadata, include_nonconverged=request.include_nonconverged)
    if not dataset.rows: raise StudyError("No scientifically valid linked simulation rows are available")
    responses = [StudyResponse.model_validate(item) for item in metadata.get("responses", [])]
    factors = [StudyFactor.model_validate(item) for item in metadata.get("factors", [])]
    constraint_result = evaluate_constraints(dataset, metadata.get("constraints", []))
    active = dataset if request.include_infeasible else _subset(dataset, set(constraint_result["feasible_simulation_ids"]))
    if not active.rows and request.objectives: raise StudyError("No feasible candidates are available for objective analysis")
    correlation_result = correlations(dataset, request.correlation_method, request.fdr_alpha)
    sensitivity = regression_sensitivity(dataset, request.sensitivity) if request.sensitivity else None
    surface = response_surface(dataset, metadata.get("factors", []), request.response_surface_response)
    pareto = pareto_front(active, request.objectives) if request.objectives else {"status": "not_applicable"}
    ranking = weighted_ranking(active, request.objectives) if request.objectives else {"status": "not_applicable"}
    configuration = request.model_dump(mode="json")
    reproducibility = _hash({"study_definition_hash": metadata["definition_hash"], "revision": metadata["revision"], "dataset_hash": dataset.dataset_hash, "linked_simulation_ids": metadata["linked_simulation_ids"], "source_evidence_ids": sorted({item for row in dataset.rows for item in row.evidence_ids}), "configuration": configuration, "algorithm_versions": {"engine": ENGINE_VERSION, "response_surface": "second_order_1", "bootstrap_seed": 20260401}})
    for item in repository.list_analyses_for_experiment(record.id):
        if item.reproducibility_hash == reproducibility and item.user_id == user_id:
            return item
    findings = {"dataset_quality": dataset.quality.model_dump(mode="json"), "run_quality": screening,
                "descriptive_statistics": descriptive_statistics(dataset), "associations": correlation_result,
                "sensitivity": sensitivity, "response_surface": surface,
                "anomalies": anomaly_diagnostics(dataset, [item.column for item in responses]), "constraints": constraint_result,
                "pareto": pareto, "ranking": ranking, "ranking_stability": _rank_stability(active, request.objectives),
                "robustness": robustness(dataset, metadata.get("factors", []), metadata.get("responses", [])),
                "hypothesis_criterion": hypothesis_result(metadata, dataset),
                "limitations": ["No causal conclusion or full physical-model probabilistic uncertainty claim is made."]}
    now = utcnow(); analysis_id = str(uuid.uuid4())
    result = {"dataset": dataset.model_dump(mode="json"), "findings": findings}
    analysis = AnalysisRecord(id=analysis_id, experiment_id=record.id, user_id=user_id, analysis_type="research_study", status="completed", dataset_hash=dataset.dataset_hash, configuration=configuration, result=result, warnings=list(dataset.quality.warnings), source_design_ids=sorted({row.design_id for row in dataset.rows if row.design_id}), source_simulation_ids=sorted(row.simulation_id for row in dataset.rows), data_quality=dataset.quality.model_dump(mode="json"), engine_version=ENGINE_VERSION, reproducibility_hash=reproducibility, created_at=now, updated_at=now)
    repository.create_analysis(analysis)
    EvidenceRepository(repository=repository).create_scientific_evidence(user_id, {"evidence_type":"analysis", "schema_version":"2.0", "experiment_id":record.id, "design_id":None, "simulation_id":None, "solver_id":None, "solver_version":None, "input_fingerprint":None, "result_hash":None, "source_ids":sorted({item for row in dataset.rows for item in row.evidence_ids}), "status":"completed", "analysis_id":analysis.id, "dataset_hash":dataset.dataset_hash, "analysis_type":"research_study", "source_simulation_ids":analysis.source_simulation_ids, "provenance":{"study_id":record.id, "definition_hash":metadata["definition_hash"], "revision":metadata["revision"], "configuration":configuration, "reproducibility_hash":reproducibility, "engine_version":ENGINE_VERSION}, "warnings":analysis.warnings, "limitations":findings["limitations"]})
    return analysis


def manifest(record, metadata: dict, analysis: AnalysisRecord | None) -> dict:
    result = analysis.result if analysis else {}; dataset = result.get("dataset", {})
    return {"study_id": record.id, "experiment_id": record.id, "revision": metadata["revision"], "definition_hash": metadata["definition_hash"], "study_definition": {key: metadata.get(key) for key in SCIENCE_FIELDS}, "doe": metadata.get("doe"), "dataset_hash": dataset.get("dataset_hash"), "analysis_id": analysis.id if analysis else None, "analysis_configuration": analysis.configuration if analysis else None, "analysis_reproducibility_hash": analysis.reproducibility_hash if analysis else None, "source_design_ids": analysis.source_design_ids if analysis else [], "source_simulation_ids": metadata.get("linked_simulation_ids", []), "source_evidence_ids": sorted({item for row in dataset.get("rows", []) for item in row.get("evidence_ids", [])}), "solver_versions": sorted({f"{row.get('solver_id')}@{row.get('solver_version')}" for row in dataset.get("rows", [])}), "warnings": metadata.get("warnings", []), "limitations": metadata.get("limitations", [])}
