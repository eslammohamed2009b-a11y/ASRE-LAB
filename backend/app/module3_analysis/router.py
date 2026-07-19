from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.module3_analysis.clustering import cluster_designs
from app.module3_analysis.correlation import build_correlation_matrix
from app.module3_analysis.schemas import FullReportRequest, FullReportResponse
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
