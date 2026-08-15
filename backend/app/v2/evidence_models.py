"""Typed, versioned scientific evidence payloads persisted in engineering_evidence_records."""
from __future__ import annotations
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator

class EvidenceType(str, Enum):
    NUMERICAL_RESULT="numerical_result"; VALIDITY="validity"; BENCHMARK="benchmark"
    RUN_CONVERGENCE="run_convergence"; REFINEMENT_CONVERGENCE="refinement_convergence"
    FIELD_RESULT="field_result"; ANALYSIS="analysis"

class ResultEvidenceStatus(str, Enum):
    COMPLETED = "completed"
    WARNING = "warning"
    FAIL = "fail"

class ValidityEvidenceStatus(str, Enum):
    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    INVALID = "invalid"

class BenchmarkEvidenceStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"

class BenchmarkCaseBinding(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    benchmark_id: str
    benchmark_version: str
    authoritative: Literal[True] = True
    eligibility_status: Literal["eligible"] = "eligible"
    simulation_id: str
    solver_id: str
    solver_version: str
    input_fingerprint: str
    mesh_id: str
    mesh_hash: str
    result_hash: str
    field_evidence_id: str
    field_checksum_sha256: str
    derived_parameters: dict[str, float | int | str]
    binding_hash: str

class ConvergenceEvidenceStatus(str, Enum):
    COMPLETED = "completed"
    NOT_CONVERGED = "not_converged"
    NOT_RUN = "not_run"
    NOT_APPLICABLE = "not_applicable"

class EvidenceBase(BaseModel):
    evidence_type: EvidenceType
    schema_version: str = "2.0"
    experiment_id: str | None = None
    design_id: str | None = None
    simulation_id: str | None = None
    solver_id: str | None = None
    solver_version: str | None = None
    input_fingerprint: str | None = None
    result_hash: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    status: str
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

class BenchmarkEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.BENCHMARK
    benchmark_id: str
    metric_name: str
    computed_value: float
    reference_value: float
    absolute_error: float
    relative_error: float
    tolerance: float
    passed: bool
    source_simulation_id: str
    status: BenchmarkEvidenceStatus
    benchmark_details: dict[str, Any] = Field(default_factory=dict)
    case_binding: BenchmarkCaseBinding | None = None

    @model_validator(mode="after")
    def status_matches_result(self):
        if self.passed != (self.status == BenchmarkEvidenceStatus.PASS):
            raise ValueError("Benchmark status must agree with passed")
        return self

class RunConvergenceEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.RUN_CONVERGENCE
    metric_type: str
    metric_value: float | None = None
    tolerance: float | None = None
    iterations: int | None = None
    criterion: str
    passed: bool | None = None
    status: ConvergenceEvidenceStatus

    @model_validator(mode="after")
    def status_matches_result(self):
        expected = {
            ConvergenceEvidenceStatus.COMPLETED: True,
            ConvergenceEvidenceStatus.NOT_CONVERGED: False,
            ConvergenceEvidenceStatus.NOT_RUN: None,
            ConvergenceEvidenceStatus.NOT_APPLICABLE: None,
        }[self.status]
        if self.passed is not expected:
            raise ValueError("Run-convergence status must agree with passed")
        return self

class NumericalResultEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.NUMERICAL_RESULT
    summary_metrics: dict[str, float]
    material_snapshot: dict[str, float | str | bool | None]
    numerical_method: str
    convergence: dict
    status: ResultEvidenceStatus

class FieldResultEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.FIELD_RESULT
    variable_name: str; unit: str; array_shape: list[int]; checksum_sha256: str
    format: str; format_version: str
    status: ResultEvidenceStatus

class ValidityEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.VALIDITY
    evaluated_inputs: dict[str, Any]
    rules: list[dict[str, Any]]
    status: ValidityEvidenceStatus

class RefinementLevel(BaseModel):
    level: Literal["coarse", "medium", "fine"]
    simulation_id: str
    value: float | None = None
    refinement_value: float | None = None
    input_fingerprint: str | None = None
    solver_id: str | None = None
    solver_version: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)

class RefinementConvergenceEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.REFINEMENT_CONVERGENCE
    selected_metric: str
    refinement_parameter: str | None = None
    comparison_hash: str | None = None
    convergence_threshold: float | None = Field(default=None, gt=0, le=1)
    coarse_to_medium_change: float | None = None
    medium_to_fine_change: float | None = None
    passed: bool | None = None
    metric_source: Literal["simulation_summary", "benchmark_evidence"] = "simulation_summary"
    benchmark_id: str | None = None
    levels: list[RefinementLevel]
    status: ConvergenceEvidenceStatus

    @model_validator(mode="after")
    def has_exact_refinement_levels(self):
        if [item.level for item in self.levels] != ["coarse", "medium", "fine"]:
            raise ValueError("Refinement evidence requires ordered coarse, medium, and fine levels")
        if self.passed is not None and self.passed != (self.status == ConvergenceEvidenceStatus.COMPLETED):
            raise ValueError("Refinement status must agree with passed")
        return self

class AnalysisEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.ANALYSIS
    analysis_id: str; dataset_hash: str
    analysis_type: str
    source_simulation_ids: list[str] = Field(min_length=1)
    provenance: dict[str, Any]
    status: ResultEvidenceStatus

EVIDENCE_MODELS = {
    EvidenceType.NUMERICAL_RESULT: NumericalResultEvidence,
    EvidenceType.FIELD_RESULT: FieldResultEvidence,
    EvidenceType.VALIDITY: ValidityEvidence,
    EvidenceType.BENCHMARK: BenchmarkEvidence,
    EvidenceType.RUN_CONVERGENCE: RunConvergenceEvidence,
    EvidenceType.REFINEMENT_CONVERGENCE: RefinementConvergenceEvidence,
    EvidenceType.ANALYSIS: AnalysisEvidence,
}
