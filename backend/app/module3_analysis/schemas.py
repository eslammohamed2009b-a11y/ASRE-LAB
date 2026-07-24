from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class DatasetRow(BaseModel):
    design_id: str | None
    simulation_id: str
    solver_id: str
    solver_version: str
    values: dict[str, float]
    converged: bool
    simulation_status: str
    evidence_ids: list[str]


class DatasetQualityReport(BaseModel):
    source_simulation_count: int
    valid_row_count: int
    excluded_row_count: int
    duplicate_simulation_ids: list[str] = Field(default_factory=list)
    missing_value_counts: dict[str, int] = Field(default_factory=dict)
    constant_columns: list[str] = Field(default_factory=list)
    non_numeric_fields: list[str] = Field(default_factory=list)
    incompatible_units: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ExperimentDataset(BaseModel):
    experiment_id: str
    version: str = "1.0"
    rows: list[DatasetRow] = Field(max_length=5000)
    columns: list[str] = Field(max_length=256)
    units: dict[str, str]
    quality: DatasetQualityReport
    dataset_hash: str


class ObjectiveSpec(BaseModel):
    column: str = Field(min_length=1, max_length=160)
    direction: Literal["minimize", "maximize"]
    weight: float = Field(default=1.0, gt=0, le=1000)


class SensitivitySpec(BaseModel):
    target: str = Field(min_length=1, max_length=160)
    features: list[str] = Field(min_length=1, max_length=64)


class AnalysisCreateRequest(BaseModel):
    include_nonconverged: bool = False
    correlation_method: Literal["pearson", "spearman", "both"] = "both"
    sensitivity: SensitivitySpec | None = None
    objectives: list[ObjectiveSpec] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def unique_objectives(self):
        names = [item.column for item in self.objectives]
        if len(names) != len(set(names)):
            raise ValueError("objective columns must be unique")
        return self


class AnalysisResponse(BaseModel):
    id: str
    experiment_id: str
    analysis_type: str
    status: str
    dataset_hash: str
    configuration: dict
    result: dict
    warnings: list[str]
    source_design_ids: list[str]
    source_simulation_ids: list[str]
    data_quality: dict
    engine_version: str
    reproducibility_hash: str
    created_at: str
    updated_at: str
