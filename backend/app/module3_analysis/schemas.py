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
    analysis_evidence_id: str | None = None


# Phase 4 research-study contracts.  They intentionally bind only persisted
# numeric paths; the API never accepts a client formula or executable transform.
class HypothesisSpec(BaseModel):
    statement: str = Field(min_length=3, max_length=2000)
    response_column: str | None = Field(default=None, max_length=160)
    expected_direction: Literal["increase", "decrease", "at_least", "at_most"] | None = None
    threshold: float | None = None


class StudyFactor(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_path: str = Field(pattern=r"^(design|material|boundary|solver_input)\.[A-Za-z0-9_.-]+$")
    unit: str = Field(min_length=1, max_length=60)
    minimum: float
    maximum: float
    allowed_values: list[float] | None = Field(default=None, max_length=64)
    role: Literal["controlled", "varied"] = "varied"

    @model_validator(mode="after")
    def valid_bounds(self):
        if not self.minimum < self.maximum:
            raise ValueError("factor minimum must be less than maximum")
        if self.allowed_values is not None:
            if len(self.allowed_values) < 2 or len(set(self.allowed_values)) != len(self.allowed_values):
                raise ValueError("factor allowed_values must contain at least two distinct values")
            if any(value < self.minimum or value > self.maximum for value in self.allowed_values):
                raise ValueError("factor allowed_values must lie within declared bounds")
        return self


class StudyResponse(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    column: str = Field(pattern=r"^(metric|field|quality)\.[A-Za-z0-9_.-]+$")
    unit: str = Field(min_length=1, max_length=60)
    desired_direction: Literal["minimize", "maximize"] | None = None
    threshold: float | None = None


class ConstraintSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    column: str = Field(min_length=1, max_length=160)
    operator: Literal["<=", ">=", "between"]
    value: float | None = None
    lower: float | None = None
    upper: float | None = None
    unit: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def valid_constraint(self):
        if self.operator == "between":
            if self.lower is None or self.upper is None or self.lower > self.upper:
                raise ValueError("between constraints require ordered lower and upper values")
        elif self.value is None:
            raise ValueError("inequality constraints require value")
        return self


class DOEConfig(BaseModel):
    method: Literal["full_factorial", "latin_hypercube"]
    levels: dict[str, list[float]] = Field(default_factory=dict)
    count: int | None = Field(default=None, ge=2, le=500)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)


class StudyAnalysisRequest(BaseModel):
    include_infeasible: bool = False
    include_nonconverged: bool = False
    correlation_method: Literal["pearson", "spearman", "both"] = "both"
    fdr_alpha: float = Field(default=0.05, gt=0, le=0.25)
    sensitivity: SensitivitySpec | None = None
    response_surface_response: str | None = Field(default=None, max_length=160)
    objectives: list[ObjectiveSpec] = Field(default_factory=list, max_length=16)


class NextExperimentRequest(BaseModel):
    candidate_count: int = Field(default=512, ge=16, le=8192)
    count: int = Field(default=3, ge=1, le=32)
    seed: int = Field(default=20260401, ge=0, le=2_147_483_647)
