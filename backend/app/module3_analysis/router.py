from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.module3_analysis.clustering import cluster_designs
from app.module3_analysis.correlation import build_correlation_matrix
from app.module3_analysis.intelligence import AnalysisInputError
from app.module3_analysis.dataset import DatasetBuildError
from app.module3_analysis.schemas import (
    AnalysisCreateRequest,
    AnalysisResponse,
    FullReportRequest,
    FullReportResponse,
)
from app.module3_analysis.service import (
    AnalysisNotFoundError,
    get_analysis_for_user,
    list_analyses_for_user,
    run_experiment_analysis,
)
from app.module3_analysis.synthesis import synthesize_report

router = APIRouter(
    prefix="/api/analyze",
    tags=["Module 3 - Analytical Pattern Discovery"],
    dependencies=[Depends(get_current_user)],
)


@router.post(
    "/full-report",
    response_model=FullReportResponse,
    summary="Generate analytical full report",
    description="Runs clustering, correlation, and synthesis over simulation output data.",
)
def full_report(payload: FullReportRequest) -> FullReportResponse:
    """
    design_results: [{"design_id", "params": {...}, "metrics": {...}}, ...]
    Runs clustering + correlation, then LLM synthesis.
    """
    design_results = [item.model_dump() for item in payload.design_results]
    cluster_output = cluster_designs(design_results, payload.n_clusters)
    correlation_output = build_correlation_matrix(design_results)
    insights = synthesize_report(cluster_output, correlation_output)

    return FullReportResponse(clusters=cluster_output, correlation=correlation_output, insights=insights)


@router.post(
    "/experiments/{experiment_id}",
    response_model=AnalysisResponse,
    status_code=201,
    summary="Run and persist deterministic engineering intelligence",
)
def create_experiment_analysis(
    experiment_id: str,
    payload: AnalysisCreateRequest,
    current_user: dict = Depends(get_current_user),
) -> AnalysisResponse:
    try:
        return run_experiment_analysis(experiment_id, current_user["id"], payload)
    except DatasetBuildError as exc:
        if str(exc) == "Experiment not found":
            raise HTTPException(status_code=404, detail="Experiment not found") from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AnalysisInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/experiments/{experiment_id}",
    response_model=list[AnalysisResponse],
    summary="List persisted analyses for an experiment",
)
def list_experiment_analyses(
    experiment_id: str, current_user: dict = Depends(get_current_user),
) -> list[AnalysisResponse]:
    try:
        return list_analyses_for_user(experiment_id, current_user["id"])
    except AnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Experiment not found") from exc


@router.get(
    "/{analysis_id}",
    response_model=AnalysisResponse,
    summary="Retrieve one persisted analysis",
)
def get_analysis(
    analysis_id: str, current_user: dict = Depends(get_current_user),
) -> AnalysisResponse:
    try:
        return get_analysis_for_user(analysis_id, current_user["id"])
    except AnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Analysis not found") from exc


@router.get(
    "/{analysis_id}/results/{section}",
    response_model=dict,
    summary="Retrieve one bounded analysis result section",
)
def get_analysis_section(
    analysis_id: str,
    section: Literal[
        "data_quality", "descriptive_statistics", "correlations", "sensitivity",
        "pareto", "ranking", "recommendations",
    ],
    current_user: dict = Depends(get_current_user),
) -> dict:
    try:
        analysis = get_analysis_for_user(analysis_id, current_user["id"])
    except AnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Analysis not found") from exc
    if section == "data_quality":
        return analysis.data_quality
    value = analysis.result.get(section)
    if value is None:
        raise HTTPException(status_code=404, detail="Analysis section not available")
    return value if isinstance(value, dict) else {section: value}
