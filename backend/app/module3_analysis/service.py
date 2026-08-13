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
from app.v2.repository import EvidenceRepository


class AnalysisNotFoundError(LookupError):
    pass


def _analysis_evidence_id(record: AnalysisRecord, repository) -> str | None:
    evidence = EvidenceRepository(repository=repository)
    matches = [item for item in evidence.list(
        record.user_id, "scientific_analysis", experiment_id=record.experiment_id,
    ) if item.get("payload", {}).get("analysis_id") == record.id]
    return matches[-1]["id"] if matches else None


def _response(record: AnalysisRecord, repository=None) -> AnalysisResponse:
    return AnalysisResponse(
        id=record.id, experiment_id=record.experiment_id, analysis_type=record.analysis_type,
        status=record.status, dataset_hash=record.dataset_hash,
        configuration=record.configuration, result=record.result,
        warnings=record.warnings, source_design_ids=record.source_design_ids,
        source_simulation_ids=record.source_simulation_ids, data_quality=record.data_quality,
        engine_version=record.engine_version, reproducibility_hash=record.reproducibility_hash,
        created_at=record.created_at, updated_at=record.updated_at,
        analysis_evidence_id=_analysis_evidence_id(record, repository) if repository else None,
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
        require_authoritative_evidence=True,
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
    source_evidence_ids = sorted({item for row in dataset.rows for item in row.evidence_ids})
    analysis_evidence = EvidenceRepository(repository=repository).create_scientific_evidence(user_id, {
        "evidence_type":"analysis","schema_version":"2.0",
        "experiment_id":experiment_id,"design_id":None,"simulation_id":None,
        "solver_id":None,"solver_version":None,"input_fingerprint":None,"result_hash":None,
        "source_ids":source_evidence_ids,"status":"completed",
        "analysis_id":record.id,"dataset_hash":dataset.dataset_hash,
        "analysis_type":record.analysis_type,
        "source_simulation_ids":record.source_simulation_ids,
        "provenance":{
            "source_design_ids":record.source_design_ids,
            "configuration":configuration,"engine_version":engine_version,
            "reproducibility_hash":reproducibility_hash,
        },
        "warnings":warnings,
        "limitations":["Statistical associations do not establish causation."],
    })
    response=_response(record,repository)
    return response.model_copy(update={"analysis_evidence_id":analysis_evidence["id"]})


def get_analysis_for_user(
    analysis_id: str, user_id: str, repository: PersistenceRepository | None = None,
) -> AnalysisResponse:
    repository = repository or get_repository()
    record = repository.get_analysis(analysis_id)
    if record is None or record.user_id != user_id:
        raise AnalysisNotFoundError("Analysis not found")
    return _response(record,repository)


def list_analyses_for_user(
    experiment_id: str, user_id: str, repository: PersistenceRepository | None = None,
) -> list[AnalysisResponse]:
    repository = repository or get_repository()
    experiment = repository.get_experiment(experiment_id)
    if experiment is None or experiment.user_id != user_id:
        raise AnalysisNotFoundError("Experiment not found")
    return [_response(record,repository) for record in repository.list_analyses_for_experiment(experiment_id)]
