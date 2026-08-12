"""Typed, versioned scientific evidence payloads persisted in engineering_evidence_records."""
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

class EvidenceType(str, Enum):
    NUMERICAL_RESULT="numerical_result"; VALIDITY="validity"; BENCHMARK="benchmark"
    RUN_CONVERGENCE="run_convergence"; REFINEMENT_CONVERGENCE="refinement_convergence"
    FIELD_RESULT="field_result"; ANALYSIS="analysis"

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

class RunConvergenceEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.RUN_CONVERGENCE
    metric_type: str
    metric_value: float | None = None
    tolerance: float | None = None
    iterations: int | None = None
    criterion: str
    passed: bool | None = None

class NumericalResultEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.NUMERICAL_RESULT
    summary_metrics: dict[str, float]
    material_snapshot: dict[str, dict[str, float | str]]
    numerical_method: str
    convergence: dict

class FieldResultEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.FIELD_RESULT
    variable_name: str; unit: str; array_shape: list[int]; checksum_sha256: str
    format: str; format_version: str

class ValidityEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.VALIDITY
    evaluated_inputs: dict[str, Any]
    rules: list[dict[str, Any]]

class RefinementConvergenceEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.REFINEMENT_CONVERGENCE
    levels: list[dict[str, Any]]

class AnalysisEvidence(EvidenceBase):
    evidence_type: EvidenceType = EvidenceType.ANALYSIS
    analysis_id: str; dataset_hash: str

EVIDENCE_MODELS = {
    EvidenceType.NUMERICAL_RESULT: NumericalResultEvidence,
    EvidenceType.FIELD_RESULT: FieldResultEvidence,
    EvidenceType.VALIDITY: ValidityEvidence,
    EvidenceType.BENCHMARK: BenchmarkEvidence,
    EvidenceType.RUN_CONVERGENCE: RunConvergenceEvidence,
    EvidenceType.REFINEMENT_CONVERGENCE: RefinementConvergenceEvidence,
    EvidenceType.ANALYSIS: AnalysisEvidence,
}
