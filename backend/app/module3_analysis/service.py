"""Owner-scoped orchestration and persistence for deterministic Module 3 analyses."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from app.core.repository import AnalysisRecord, PersistenceRepository, get_repository
from app.module3_analysis.dataset import build_experiment_dataset
from app.module3_analysis.intelligence import (
    AnalysisInputError,
    correlations,
    descriptive_statistics,
    grounded_recommendations,
    pareto_front,
    regression_sensitivity,
    weighted_ranking,
)
from app.module3_analysis.schemas import AnalysisCreateRequest, AnalysisResponse


class AnalysisNotFoundError(LookupError):
    pass


def _response(record: AnalysisRecord) -> AnalysisResponse:
    return AnalysisResponse(
        id=record.id, experiment_id=record.experiment_id, analysis_type=record.analysis_type,
        status=record.status, dataset_hash=record.dataset_hash,
        configuration=record.configuration, result=record.result,
        warnings=record.warnings, source_design_ids=record.source_design_ids,
        source_simulation_ids=record.source_simulation_ids, data_quality=record.data_quality,
        engine_version=record.engine_version, reproducibility_hash=record.reproducibility_hash,
        created_at=record.created_at, updated_at=record.updated_at,
    )


def run_experiment_analysis(
    experiment_id: str,
    user_id: str,
    request: AnalysisCreateRequest,
    repository: PersistenceRepository | None = None,
) -> AnalysisResponse:
    repository = repository or get_repository()
    dataset = build_experiment_dataset(
        repository, experiment_id, user_id,
        include_nonconverged=request.include_nonconverged,
    )
    if not dataset.rows:
        raise AnalysisInputError("No valid persisted simulation results are available for analysis")

    correlation_result = correlations(dataset, request.correlation_method)
    result: dict = {
        "dataset": dataset.model_dump(),
        "descriptive_statistics": descriptive_statistics(dataset),
        "correlations": correlation_result,
    }
    sensitivity_result = None
    if request.sensitivity is not None:
        sensitivity_result = regression_sensitivity(dataset, request.sensitivity)
        result["sensitivity"] = sensitivity_result
    if request.objectives:
        pareto = pareto_front(dataset, request.objectives)
        ranking = weighted_ranking(dataset, request.objectives)
        result["pareto"] = pareto
        result["ranking"] = ranking
        result["recommendations"] = grounded_recommendations(
            ranking, pareto, correlation_result, sensitivity_result,
        )
    else:
        result["recommendations"] = []

    created_at = datetime.now(timezone.utc).isoformat()
    warnings = list(dataset.quality.warnings)
    configuration = request.model_dump(mode="json")
    engine_version = "1.0"
    reproducibility_hash = hashlib.sha256(json.dumps(
        {"dataset_hash": dataset.dataset_hash, "configuration": configuration, "engine_version": engine_version},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    record = AnalysisRecord(
        id=str(uuid.uuid4()), experiment_id=experiment_id, user_id=user_id,
        analysis_type="engineering_intelligence", status="completed",
        dataset_hash=dataset.dataset_hash, configuration=configuration,
        result=result, warnings=warnings,
        source_design_ids=sorted({row.design_id for row in dataset.rows if row.design_id}),
        source_simulation_ids=sorted(row.simulation_id for row in dataset.rows),
        data_quality=dataset.quality.model_dump(mode="json"), engine_version=engine_version,
        reproducibility_hash=reproducibility_hash, created_at=created_at, updated_at=created_at,
    )
    repository.create_analysis(record)
    return _response(record)


def get_analysis_for_user(
    analysis_id: str, user_id: str, repository: PersistenceRepository | None = None,
) -> AnalysisResponse:
    repository = repository or get_repository()
    record = repository.get_analysis(analysis_id)
    if record is None or record.user_id != user_id:
        raise AnalysisNotFoundError("Analysis not found")
    return _response(record)


def list_analyses_for_user(
    experiment_id: str, user_id: str, repository: PersistenceRepository | None = None,
) -> list[AnalysisResponse]:
    repository = repository or get_repository()
    experiment = repository.get_experiment(experiment_id)
    if experiment is None or experiment.user_id != user_id:
        raise AnalysisNotFoundError("Experiment not found")
    return [_response(record) for record in repository.list_analyses_for_experiment(experiment_id)]
