from pydantic import BaseModel, Field


class DesignResult(BaseModel):
    design_id: str
    params: dict
    metrics: dict[str, float]


class FullReportRequest(BaseModel):
    design_results: list[DesignResult] = Field(default_factory=list)
    n_clusters: int = Field(default=4, ge=1, le=20)


class FullReportResponse(BaseModel):
    clusters: dict
    correlation: dict
    insights: dict
