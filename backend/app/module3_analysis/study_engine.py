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
from app.module2_simulation.source_resolution import (
    SimulationSourceError, resolve_simulation_source,
)
from app.module3_analysis.dataset import build_experiment_dataset, scientific_dataset_hash
from app.module3_analysis.intelligence import (
    AnalysisInputError, correlations, descriptive_statistics, pareto_front,
    regression_sensitivity, weighted_ranking,
)
from app.module3_analysis.schemas import (
    ConstraintSpec, DOEConfig, ExperimentDataset, NextExperimentRequest,
    StudyAnalysisRequest, StudyFactor, StudyResponse,
)
from app.v2.claim_integrity import is_authoritative_evidence
from app.v2.evidence_integrity import records_by_type
from app.v2.evidence_models import EvidenceType
from app.v2.repository import EvidenceRepository
from app.v2.scientific_trust import compatible_benchmarks


ENGINE_VERSION = "4.0"
MAX_FACTORS, MAX_RESPONSES, MAX_LINKED_RUNS = 16, 32, 5000
MAX_FACTORIAL, MAX_DOE_POINTS, MAX_CANDIDATES = 500, 500, 8192

# These limits are server-owned prerequisites for using a sampled-region
# surrogate to place local-resolution proposals.  They are deliberately not
# fitted or relaxed from a Study's observed results.
MAX_RESPONSE_SURFACE_CONDITION_NUMBER = 1.0e8
MIN_REFINE_CV_R_SQUARED = -0.25
REFINE_CURVATURE_WEIGHT = 0.35
REFINE_INTERACTION_WEIGHT = 0.25
REFINE_DISTANCE_WEIGHT = 0.40
NEXT_EXPERIMENT_ALGORITHM_VERSION = "refine-explore-v1"


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


def _screen_study_run(repository: PersistenceRepository, job, result, user_id: str) -> dict:
    """Apply the existing evidence lifecycle as the Study inclusion gate.

    Only typed, provenance-checked evidence can make a row eligible.  Raw
    records are retained solely to explain why malformed/tampered evidence did
    not satisfy the authoritative contract.
    """
    simulation_id = job.id
    evidence = EvidenceRepository(repository=repository)
    raw_records = evidence.list_scientific_for_simulation(user_id, simulation_id)
    reasons: list[str] = []
    if job.user_id != user_id:
        return {"simulation_id": simulation_id, "status": "scientifically_invalid", "included": False,
                "reasons": ["Simulation is not owned by the Study user."], "evidence_ids": []}
    if job.status not in {"completed", "partial_failure"} or result is None or result.status != "completed":
        return {"simulation_id": simulation_id, "status": "numerically_failed", "included": False,
                "reasons": ["Simulation has no completed persisted result."], "evidence_ids": []}
    if not result.converged:
        return {"simulation_id": simulation_id, "status": "non_converged", "included": False,
                "reasons": ["Persisted solver result is not converged."], "evidence_ids": []}
    try:
        source = resolve_simulation_source(
            simulation_id, user_id, require_completed_result=False, repository=repository,
        )
        grouped = records_by_type(evidence, user_id, source)
    except SimulationSourceError:
        return {"simulation_id": simulation_id, "status": "scientifically_invalid", "included": False,
                "reasons": ["Simulation source identity cannot be verified."], "evidence_ids": []}

    numerical = [item for item in grouped.get(EvidenceType.NUMERICAL_RESULT, []) if item[1].status.value != "fail"]
    if not numerical:
        detail = "Numerical evidence provenance is invalid." if any(
            item.get("record_type") == "scientific_numerical_result" for item in raw_records
        ) else "Required authoritative numerical evidence is missing."
        return {"simulation_id": simulation_id, "status": "evidence_incomplete", "included": False,
                "reasons": [detail], "evidence_ids": []}

    for evidence_type in (EvidenceType.VALIDITY, EvidenceType.RUN_CONVERGENCE):
        record_name = f"scientific_{evidence_type.value}"
        if any(item.get("record_type") == record_name for item in raw_records) and not grouped.get(evidence_type):
            return {"simulation_id": simulation_id, "status": "evidence_incomplete", "included": False,
                    "reasons": [f"{record_name} evidence integrity cannot be verified."], "evidence_ids": []}

    # Persisted field values are scientific inputs when a Study binds field
    # columns, so their authoritative checksums must be present too.
    field_checksums = {model.checksum_sha256 for _, model in grouped.get(EvidenceType.FIELD_RESULT, [])
                       if model.status.value != "fail"}
    missing_fields = [field.id for field in repository.list_field_results(simulation_id)
                      if field.checksum_sha256 not in field_checksums]
    if missing_fields:
        return {"simulation_id": simulation_id, "status": "evidence_incomplete", "included": False,
                "reasons": ["Persisted field data lacks authoritative field-evidence integrity."], "evidence_ids": []}

    validity = grouped.get(EvidenceType.VALIDITY, [])
    if any(model.status.value == "invalid" for _, model in validity):
        return {"simulation_id": simulation_id, "status": "scientifically_invalid", "included": False,
                "reasons": ["Authoritative scientific validity evidence is invalid."], "evidence_ids": []}
    convergence = grouped.get(EvidenceType.RUN_CONVERGENCE, [])
    if any(model.status.value == "not_converged" for _, model in convergence):
        return {"simulation_id": simulation_id, "status": "non_converged", "included": False,
                "reasons": ["Authoritative run-convergence evidence reports non-convergence."], "evidence_ids": []}

    benchmarks = compatible_benchmarks(source.solver_id)
    failed_benchmarks = [model for _, model in grouped.get(EvidenceType.BENCHMARK, []) if (
        model.benchmark_id in benchmarks
        and benchmarks[model.benchmark_id] == (model.metric_name, model.tolerance)
        and not model.passed
    )]
    if failed_benchmarks:
        return {"simulation_id": simulation_id, "status": "scientifically_invalid", "included": False,
                "reasons": ["Applicable authoritative benchmark evidence failed."], "evidence_ids": []}

    # Trust is a separate evidence record, but it remains authoritative only
    # when its hash, dependencies, and source identity resolve through the
    # existing claim-integrity helper.
    trust_records = [item for item in raw_records if item.get("record_type") == "scientific_trust"]
    for trust in trust_records:
        payload = trust.get("payload", {})
        identity_matches = all(payload.get(key) == expected for key, expected in {
            "simulation_id": simulation_id, "experiment_id": source.experiment_id,
            "design_id": source.design_id, "solver_id": source.solver_id,
            "solver_version": source.solver_version,
            "input_fingerprint": result.validation_metadata.get("input_fingerprint"),
            "result_hash": result.reproducibility_hash,
        }.items())
        if not identity_matches or not is_authoritative_evidence(evidence, user_id, trust):
            return {"simulation_id": simulation_id, "status": "scientifically_invalid", "included": False,
                    "reasons": ["Scientific trust evidence provenance cannot be verified."], "evidence_ids": []}
        if str(payload.get("overall_trust", "")).upper() == "INVALID":
            return {"simulation_id": simulation_id, "status": "scientifically_invalid", "included": False,
                    "reasons": ["Authoritative scientific trust is invalid."], "evidence_ids": []}

    evidence_ids = sorted({record["id"] for items in grouped.values() for record, _ in items})
    has_warning = bool(result.warnings) or any(model.status.value == "valid_with_warnings" for _, model in validity)
    has_warning = has_warning or any(model.status.value in {"not_run", "not_applicable"} for _, model in convergence)
    has_warning = has_warning or any(str(item.get("payload", {}).get("overall_trust", "")).upper() in {"LOW", "MODERATE"}
                                     for item in trust_records)
    if has_warning:
        reasons.append("Scientifically usable evidence carries bounded warnings or trust limitations.")
    return {"simulation_id": simulation_id, "status": "valid_with_warnings" if has_warning else "valid",
            "included": True, "reasons": reasons, "evidence_ids": evidence_ids}


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
        if job is None or job.user_id != user_id:
            screen = {
                "simulation_id": simulation_id, "status": "scientifically_invalid", "included": False,
                "reasons": ["Simulation is not an owned member of this Study experiment."],
                "result_reproducibility_hash": None, "evidence_ids": [],
            }
        else:
            screen = _screen_study_run(repository, job, result, user_id)
            screen["result_reproducibility_hash"] = result.reproducibility_hash if result else None
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
    condition_number = float(np.linalg.cond(design))
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
    diagnostics = {
        "rank": int(rank), "required_rank": design.shape[1], "condition_number": condition_number,
        "condition_number_limit": MAX_RESPONSE_SURFACE_CONDITION_NUMBER,
        "cross_validation": {"status": "completed" if cv_ok else "not_applicable", "folds": folds,
                             "partition": "index_modulo", "rmse": cv_rmse, "r_squared": cv_r2},
    }
    if rank != design.shape[1]:
        return {"status": "diagnostically_invalid", "reason": "Second-order design matrix is rank deficient.",
                "response": response_column, "factor_columns": names, "term_names": term_names,
                "diagnostics": diagnostics, "warnings": ["Rank-deficient response surfaces cannot support REFINE proposals."]}
    if not math.isfinite(condition_number) or condition_number > MAX_RESPONSE_SURFACE_CONDITION_NUMBER:
        return {"status": "diagnostically_invalid", "reason": "Second-order design matrix conditioning exceeds the declared limit.",
                "response": response_column, "factor_columns": names, "term_names": term_names,
                "diagnostics": diagnostics, "warnings": ["Ill-conditioned response surfaces cannot support REFINE proposals."]}
    return {"status": "completed", "response": response_column, "factor_columns": names, "term_names": term_names,
            "coefficients": {name: float(value) for name, value in zip(term_names, coefficient)},
            "training_r_squared": float(r2), "adjusted_r_squared": adjusted, "rank": int(rank),
            "condition_number": condition_number, "residual_rmse": float(np.sqrt(np.mean((y-predicted)**2))),
            "maximum_absolute_residual": float(np.max(np.abs(y-predicted))), "cross_validation": {"status": "completed" if cv_ok else "not_applicable", "folds": folds, "partition": "index_modulo", "rmse": cv_rmse, "r_squared": cv_r2},
            "curvature_indicators": curvature, "interaction_indicators": interactions,
            "factor_standardization": {"means": {name: float(value) for name, value in zip(names, means)},
                                         "scales": {name: float(value) for name, value in zip(names, scales)}},
            "diagnostics": diagnostics,
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


def _project_candidate(point: np.ndarray, specs: list[StudyFactor]) -> tuple[dict[str, float], np.ndarray]:
    """Map Sobol coordinates to bounds and deterministically snap discrete factors."""
    coordinates: dict[str, float] = {}
    projected = []
    for axis, spec in enumerate(specs):
        value = float(spec.minimum + point[axis] * (spec.maximum - spec.minimum))
        if spec.allowed_values:
            values = sorted(float(item) for item in set(spec.allowed_values))
            value = min(values, key=lambda item: (abs(item - value), item))
        coordinates[spec.name] = value
        projected.append((value - spec.minimum) / (spec.maximum - spec.minimum))
    return coordinates, np.asarray(projected, dtype=float)


def _candidate_pool(specs: list[StudyFactor], request: NextExperimentRequest) -> list[tuple[int, dict[str, float], np.ndarray]]:
    sampler = qmc.Sobol(d=len(specs), scramble=True, seed=request.seed)
    power = int(math.ceil(math.log2(request.candidate_count)))
    raw = sampler.random_base2(power)[:request.candidate_count]
    output = []
    seen: set[tuple[float, ...]] = set()
    for index, point in enumerate(raw):
        coordinates, normalized = _project_candidate(point, specs)
        identity = tuple(normalized.tolist())
        if identity not in seen:
            seen.add(identity); output.append((index, coordinates, normalized))
    return output


def _refine_eligibility(surface: dict | None, specs: list[StudyFactor], dataset: ExperimentDataset,
                        declared_response_columns: set[str]) -> tuple[bool, str, dict]:
    if not surface:
        return False, "REFINE_NOT_APPLICABLE", {"reason": "No latest response-surface result is available."}
    diagnostics = surface.get("diagnostics", {})
    if surface.get("status") != "completed":
        return False, "REFINE_NOT_APPLICABLE", {"reason": surface.get("reason", "Response surface is not completed."), "diagnostics": diagnostics}
    names = [item.source_path for item in specs]
    if surface.get("factor_columns") != names:
        return False, "REFINE_NOT_APPLICABLE", {"reason": "Response-surface factors do not match the current Study revision.", "diagnostics": diagnostics}
    if surface.get("response") not in declared_response_columns:
        return False, "REFINE_NOT_APPLICABLE", {"reason": "Response surface response is not declared by the current Study.", "diagnostics": diagnostics}
    coefficient = surface.get("coefficients", {})
    required_rank = diagnostics.get("required_rank", len(surface.get("term_names", [])))
    condition = surface.get("condition_number", diagnostics.get("condition_number"))
    cv = surface.get("cross_validation", {})
    finite = bool(coefficient) and all(math.isfinite(float(value)) for value in coefficient.values())
    finite = finite and all(math.isfinite(float(value)) for value in (condition, surface.get("residual_rmse")) if value is not None)
    if (len(dataset.rows) < required_rank + 2 or diagnostics.get("rank", surface.get("rank")) != required_rank
            or not finite or cv.get("status") != "completed" or cv.get("r_squared") is None
            or not math.isfinite(float(cv["r_squared"])) or float(cv["r_squared"]) < MIN_REFINE_CV_R_SQUARED
            or float(condition) > MAX_RESPONSE_SURFACE_CONDITION_NUMBER):
        return False, "REFINE_NOT_APPLICABLE", {"reason": "Response-surface diagnostics do not justify local refinement.", "diagnostics": diagnostics}
    return True, "REFINE_ELIGIBLE", {"diagnostics": diagnostics}


def _minimum_distance(point: np.ndarray, comparisons: np.ndarray) -> float:
    return 1.0 if comparisons.size == 0 else float(np.min(np.linalg.norm(comparisons - point, axis=1)))


def next_experiments(
    dataset: ExperimentDataset, factors: list[dict], request: NextExperimentRequest, *,
    source_analysis_id: str | None = None, response_surface_result: dict | None = None,
    declared_response_columns: set[str] | None = None, study_definition_hash: str | None = None,
    study_revision: int | None = None, analysis_reproducibility_hash: str | None = None,
) -> dict:
    """Return bounded REFINE plus EXPLORE proposals from one Sobol candidate pool.

    REFINE receives up to ceil(N/2) slots only when the persisted second-order
    surrogate is diagnostically usable; all other slots preserve maximin global
    coverage.  Scores are unitless: 0.35 normalized curvature, 0.25 normalized
    interaction magnitude, and 0.40 normalized distance to sampled points.
    """
    specs = [StudyFactor.model_validate(item) for item in factors]
    if not specs:
        return {"proposals": [], "warnings": ["No factors are declared for deterministic exploration."]}
    pool = _candidate_pool(specs, request)
    existing = np.asarray([[(row.values[spec.source_path] - spec.minimum) / (spec.maximum - spec.minimum)
                            for spec in specs] for row in dataset.rows
                           if all(spec.source_path in row.values for spec in specs)], dtype=float)
    existing_ids = {tuple(item.tolist()) for item in existing} if existing.size else set()
    pool = [item for item in pool if tuple(item[2].tolist()) not in existing_ids]
    declared = declared_response_columns or set()
    refine_ok, refine_status, refine_detail = _refine_eligibility(response_surface_result, specs, dataset, declared)
    selected_normalized: list[np.ndarray] = []
    proposals: list[dict] = []
    source_ids = [source_analysis_id] if source_analysis_id else []
    simulation_ids = [row.simulation_id for row in dataset.rows]

    if refine_ok:
        surface = response_surface_result or {}
        curvature = math.sqrt(sum((2 * float(value)) ** 2 for value in surface.get("curvature_indicators", {}).values()))
        interaction = math.sqrt(sum(float(value) ** 2 for value in surface.get("interaction_indicators", {}).values()))
        curvature_component = curvature / (1.0 + curvature)
        interaction_component = interaction / (1.0 + interaction)
        candidates = []
        for index, coordinates, point in pool:
            distance = _minimum_distance(point, existing)
            distance_component = min(1.0, distance / math.sqrt(len(specs)))
            score = (REFINE_CURVATURE_WEIGHT * curvature_component + REFINE_INTERACTION_WEIGHT * interaction_component
                     + REFINE_DISTANCE_WEIGHT * distance_component)
            candidates.append((score, index, coordinates, point, distance_component))
        for score, index, coordinates, point, distance_component in sorted(candidates, key=lambda item: (-item[0], item[1]))[:math.ceil(request.count / 2)]:
            selected_normalized.append(point)
            proposals.append({"proposal_type": "REFINE", "factor_coordinates": coordinates,
                "score_components": {"normalized_local_curvature": curvature_component,
                                     "normalized_interaction_magnitude": interaction_component,
                                     "normalized_distance_to_existing": distance_component,
                                     "weights": {"curvature": REFINE_CURVATURE_WEIGHT, "interaction": REFINE_INTERACTION_WEIGHT, "distance": REFINE_DISTANCE_WEIGHT}},
                "total_score": score, "reason": "Deterministic local-resolution proposal from a diagnostically valid sampled-region response surface.",
                "response_model": surface.get("response"), "source_analysis_ids": source_ids,
                "source_simulation_ids": simulation_ids, "model_diagnostics": refine_detail["diagnostics"],
                "bounds_verified": True, "duplicate_check": "passed",
                "warnings": ["REFINE is experiment planning only; it does not claim a physical or global optimum."],
                "limitations": ["The response surface is limited to the sampled Study region and cannot establish causation."]})

    remaining = [item for item in pool if not any(np.linalg.norm(item[2] - selected) <= 1e-12 for selected in selected_normalized)]
    while remaining and len(proposals) < request.count:
        scored = []
        for index, coordinates, point in remaining:
            comparisons = existing
            if selected_normalized:
                comparisons = np.vstack([comparisons, selected_normalized]) if comparisons.size else np.asarray(selected_normalized)
            score = _minimum_distance(point, comparisons)
            scored.append((score, index, coordinates, point))
        score, _, coordinates, point = max(scored, key=lambda item: (item[0], -item[1]))
        selected_normalized.append(point)
        remaining = [item for item in remaining if not np.array_equal(item[2], point)]
        proposals.append({"proposal_type": "EXPLORE", "factor_coordinates": coordinates,
            "score_components": {"minimum_normalized_distance_to_existing": _minimum_distance(point, existing)},
            "total_score": score, "reason": "Deterministic maximin coverage proposal within declared Study bounds.",
            "source_analysis_ids": source_ids, "source_simulation_ids": simulation_ids,
            "bounds_verified": True, "duplicate_check": "passed",
            "warnings": ["Proposal improves sampled coverage; it is not claimed physically optimal."],
            "limitations": ["Sobol candidate generation is a bounded search pool, not a Sobol sensitivity analysis."]})
    configuration = {"study_definition_hash": study_definition_hash or dataset.dataset_hash,
                     "study_revision": study_revision, "dataset_hash": dataset.dataset_hash,
                     "analysis_reproducibility_hash": analysis_reproducibility_hash,
                     "candidate_pool_method": "scrambled_sobol", "seed": request.seed,
                     "candidate_count": request.candidate_count, "algorithm_version": NEXT_EXPERIMENT_ALGORITHM_VERSION,
                     "refine_eligibility": {"status": refine_status, **refine_detail}, "selected_proposals": proposals}
    return {"seed": request.seed, "candidate_count": request.candidate_count, "algorithm_version": NEXT_EXPERIMENT_ALGORITHM_VERSION,
            "refine_status": refine_status, "proposals": proposals, "proposal_hash": _hash(configuration),
            "limitations": ["REFINE and EXPLORE are bounded experiment-planning proposals, not physical-optimum claims."]}


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
