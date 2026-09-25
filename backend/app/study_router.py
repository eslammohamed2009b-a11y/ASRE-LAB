"""Owner-scoped Research Study aggregate over the existing experiment graph."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.repository import ExperimentRecord, get_repository
from app.v2.repository import EvidenceRepository
from app.v2.reasoning_reports import public_record
from app.comparative_service import ComparativeRunRequest, build_comparison_plan, create_comparative_batch
from app.module2_simulation.materials import MaterialNotFoundError, MaterialPropertyNotFoundError
from app.module2_simulation.solvers.base_solver import SolverValidationError
from app.module3_analysis.schemas import (
    ConstraintSpec, DOEConfig, HypothesisSpec, NextExperimentRequest,
    StudyAnalysisRequest, StudyFactor, StudyResponse, ExperimentDataset,
)
from app.module3_analysis.intelligence import AnalysisInputError
from app.module3_analysis.study_engine import (
    StudyError, assert_definition, definition_hash, generate_doe, manifest,
    next_experiments, normalized_metadata, run_study_analysis,
)


router = APIRouter(
    prefix="/api/studies",
    tags=["Research Studies"],
    dependencies=[Depends(get_current_user)],
)


class VariableDefinition(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    unit: str | None = Field(default=None, max_length=60)


class ControlledVariable(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    value: Any
    unit: str | None = Field(default=None, max_length=60)


class StudyCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    research_question: str = Field(min_length=3, max_length=2000)
    hypothesis: str | HypothesisSpec | None = Field(default=None)
    geometry_family: str = Field(default="study_scoped_authoritative_runs", min_length=1, max_length=100)
    independent_variables: list[VariableDefinition] = Field(default_factory=list, max_length=32)
    output_variables: list[VariableDefinition] = Field(default_factory=list, max_length=64)
    controlled_variables: list[ControlledVariable] = Field(default_factory=list, max_length=64)
    objectives: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    solver_ids: list[str] = Field(default_factory=list, max_length=16)
    material: str | None = Field(default=None, max_length=100)
    boundary_conditions: dict[str, Any] = Field(default_factory=dict)
    numerical_settings: dict[str, Any] = Field(default_factory=dict)
    design_space: dict[str, Any] = Field(default_factory=dict)
    baseline_simulation_id: str | None = None
    factors: list[StudyFactor] = Field(default_factory=list, max_length=16)
    responses: list[StudyResponse] = Field(default_factory=list, max_length=32)
    constraints: list[ConstraintSpec] = Field(default_factory=list, max_length=32)
    doe: DOEConfig | None = None
    linked_simulation_ids: list[str] = Field(default_factory=list, max_length=5000)


class StudyUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    research_question: str | None = Field(default=None, min_length=3, max_length=2000)
    hypothesis: str | HypothesisSpec | None = Field(default=None)
    geometry_family: str | None = Field(default=None, min_length=1, max_length=100)
    independent_variables: list[VariableDefinition] | None = Field(default=None, max_length=32)
    output_variables: list[VariableDefinition] | None = Field(default=None, max_length=64)
    controlled_variables: list[ControlledVariable] | None = Field(default=None, max_length=64)
    objectives: list[dict[str, Any]] | None = Field(default=None, max_length=16)
    solver_ids: list[str] | None = Field(default=None, max_length=16)
    material: str | None = Field(default=None, max_length=100)
    boundary_conditions: dict[str, Any] | None = None
    numerical_settings: dict[str, Any] | None = None
    design_space: dict[str, Any] | None = None
    status: Literal["draft", "active", "completed", "archived"] | None = None
    factors: list[StudyFactor] | None = Field(default=None, max_length=16)
    responses: list[StudyResponse] | None = Field(default=None, max_length=32)
    constraints: list[ConstraintSpec] | None = Field(default=None, max_length=32)
    doe: DOEConfig | None = None


class StudyRevisionRequest(BaseModel):
    research_question: str | None = Field(default=None, min_length=3, max_length=2000)
    hypothesis: str | HypothesisSpec | None = None
    factors: list[StudyFactor] | None = Field(default=None, max_length=16)
    responses: list[StudyResponse] | None = Field(default=None, max_length=32)
    constraints: list[ConstraintSpec] | None = Field(default=None, max_length=32)
    objectives: list[dict[str, Any]] | None = Field(default=None, max_length=16)
    doe: DOEConfig | None = None


class SimulationLinksRequest(BaseModel):
    simulation_ids: list[str] = Field(min_length=1, max_length=5000)


def _owned_study(study_id: str, user_id: str) -> ExperimentRecord:
    record = get_repository().get_experiment(study_id)
    if record is None or record.user_id != user_id:
        raise HTTPException(status_code=404, detail="Study not found")
    return record


def _metadata(record: ExperimentRecord) -> dict[str, Any]:
    configured = record.input_specification.get("study")
    if isinstance(configured, dict):
        return {"title": record.name, **configured}
    return {
        "title": record.name,
        "description": "",
        "research_question": record.input_specification.get("prompt", "Not available"),
        "hypothesis": None,
        "geometry_family": record.input_specification.get("base_params", {}).get(
            "geometry_type", "not_available"
        ),
        "independent_variables": [],
        "output_variables": [],
        "controlled_variables": [],
        "objectives": [],
        "solver_ids": [],
        "material": None,
        "boundary_conditions": {},
        "numerical_settings": {},
        "design_space": {},
        "factors": [], "responses": [], "constraints": [], "doe": None,
        "linked_simulation_ids": [], "revision": 1,
    }


def _study_graph(record: ExperimentRecord) -> dict[str, Any]:
    repo = get_repository()
    designs = repo.list_design_models_for_experiment(record.id)
    design_files = repo.list_design_files_for_experiment(record.id)
    generation_jobs = repo.list_generation_jobs_for_experiment(record.id)
    simulations = repo.list_simulation_jobs_for_experiment(record.id)
    analyses = repo.list_analyses_for_experiment(record.id)
    evidence = EvidenceRepository().list(record.user_id, experiment_id=record.id)

    simulation_items = []
    for job in simulations:
        item = asdict(job)
        simulation_input = repo.get_simulation_input(job.id)
        simulation_result = repo.get_simulation_result(job.id)
        item["input"] = asdict(simulation_input) if simulation_input else None
        item["result"] = asdict(simulation_result) if simulation_result else None
        item["fields"] = [asdict(field) for field in repo.list_field_results(job.id)]
        simulation_items.append(item)

    return public_record({
        "designs": [
            {
                **asdict(item),
                "files": [asdict(file) for file in design_files if file.design_model_id == item.id],
            }
            for item in designs
        ],
        "generation_jobs": [asdict(item) for item in generation_jobs],
        "simulations": simulation_items,
        "analyses": [asdict(item) for item in analyses],
        "evidence": evidence,
        "decisions": [item for item in evidence if item["record_type"] == "engineering_decision"],
        "reports": [item for item in evidence if item["record_type"] == "research_report"],
    })


def _summary(record: ExperimentRecord) -> dict[str, Any]:
    graph = _study_graph(record)
    completed = sum(item["status"] in {"completed", "partial_failure"} for item in graph["simulations"])
    failed = sum(item["status"] == "failed" for item in graph["simulations"])
    return {
        "id": record.id,
        **_metadata(record),
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "design_count": len(graph["designs"]),
        "simulation_count": len(graph["simulations"]),
        "completed_run_count": completed,
        "failed_run_count": failed,
        "analysis_count": len(graph["analyses"]),
        "report_count": len(graph["reports"]),
    }


def _list_summary(record: ExperimentRecord) -> dict[str, Any]:
    """Return list counts without loading per-run result or field payloads."""
    repo = get_repository()
    simulations = repo.list_simulation_jobs_for_experiment(record.id)
    evidence = EvidenceRepository().list(record.user_id, experiment_id=record.id)
    return {
        "id": record.id,
        **_metadata(record),
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "design_count": len(repo.list_design_models_for_experiment(record.id)),
        "simulation_count": len(simulations),
        "completed_run_count": sum(item.status in {"completed", "partial_failure"} for item in simulations),
        "failed_run_count": sum(item.status == "failed" for item in simulations),
        "analysis_count": len(repo.list_analyses_for_experiment(record.id)),
        "report_count": sum(item["record_type"] == "research_report" for item in evidence),
    }


def _is_research_study(record: ExperimentRecord) -> bool:
    return record.input_specification.get("schema_version") == "research-study-v1"


def _phase4_metadata(record: ExperimentRecord) -> dict[str, Any]:
    metadata = _metadata(record)
    # Legacy Studies stored lifecycle state only on the experiment aggregate.
    # Preserve that authoritative state when introducing Phase 4 metadata.
    metadata.setdefault("status", record.status)
    return normalized_metadata(metadata, study_id=record.id, owner_id=record.user_id, experiment_id=record.id)


def _persist_metadata(repo, record: ExperimentRecord, metadata: dict, *, status: str | None = None, title: str | None = None) -> ExperimentRecord:
    metadata = normalized_metadata(metadata, study_id=record.id, owner_id=record.user_id, experiment_id=record.id)
    assert_definition(metadata)
    repo.update_experiment(record.id, name=title, status=status or metadata.get("status"), input_specification={
        **record.input_specification, "study": metadata, "schema_version": "research-study-v1",
    })
    return repo.get_experiment(record.id)


def _study_evidence(repo, user_id: str, record: ExperimentRecord, metadata: dict, kind: str, payload: dict) -> str:
    try:
        evidence_repository = EvidenceRepository(repository=repo)
    except TypeError:  # legacy test/local dependency injection adapter
        evidence_repository = EvidenceRepository()
    evidence = evidence_repository.create(user_id, {
        "record_type": f"study_{kind}", "experiment_id": record.id, "simulation_id": None,
        "status": "completed", "payload": {"study_id": record.id, "definition_hash": metadata["definition_hash"], "revision": metadata["revision"], **payload},
    })
    return evidence["id"]


@router.post("", status_code=201)
def create_study(
    payload: StudyCreateRequest, current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    repo = get_repository()
    metadata = payload.model_dump(mode="json")
    metadata.pop("title", None)
    study_id = repo.create_experiment(
        current_user["id"], payload.title, {"study": metadata, "schema_version": "research-study-v1"}
    )
    repo.update_experiment(study_id, status="draft")
    record = repo.get_experiment(study_id)
    metadata = _phase4_metadata(record)
    record = _persist_metadata(repo, record, metadata, status="draft")
    _study_evidence(repo, current_user["id"], record, metadata, "definition", {"definition": metadata})
    return _summary(record)


@router.get("")
def list_studies(
    include_archived: bool = False, current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    records = [
        record for record in get_repository().list_experiments_for_user(current_user["id"])
        if _is_research_study(record)
    ]
    if not include_archived:
        records = [record for record in records if record.status != "archived"]
    return {"items": [_list_summary(record) for record in records]}


@router.get("/{study_id}")
def get_study(study_id: str, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    record = _owned_study(study_id, current_user["id"])
    return {**_summary(record), **_study_graph(record)}


@router.patch("/{study_id}")
def update_study(
    study_id: str,
    payload: StudyUpdateRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    repo = get_repository()
    record = _owned_study(study_id, current_user["id"])
    changes = payload.model_dump(exclude_unset=True, mode="json")
    title = changes.pop("title", None)
    status = changes.pop("status", None)
    metadata = _phase4_metadata(record)
    metadata.pop("title", None)
    scientific = {"research_question", "hypothesis", "factors", "responses", "constraints", "objectives", "doe"}
    if record.status == "active" and metadata.get("linked_simulation_ids") and scientific & set(changes):
        raise HTTPException(status_code=409, detail="Active Study scientific definition is immutable; create an explicit revision")
    metadata.update(changes)
    try:
        updated = _persist_metadata(repo, record, metadata, status=status, title=title)
    except StudyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _summary(updated)


@router.post("/{study_id}/revisions", status_code=201)
def create_revision(study_id: str, payload: StudyRevisionRequest, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"])
    metadata = _phase4_metadata(record)
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise HTTPException(status_code=422, detail="Revision must change at least one scientific definition field")
    history = list(metadata.get("revision_history", []))
    history.append({"revision": metadata["revision"], "definition_hash": metadata["definition_hash"]})
    metadata.update(changes); metadata["revision"] += 1; metadata["revision_history"] = history
    try:
        updated = _persist_metadata(repo, record, metadata, status="draft")
    except StudyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    fresh = _phase4_metadata(updated)
    _study_evidence(repo, current_user["id"], updated, fresh, "definition", {"definition": fresh, "revision_of": history[-1]})
    return _summary(updated)


@router.post("/{study_id}/activate")
def activate_study(study_id: str, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"]); metadata = _phase4_metadata(record)
    if not metadata.get("linked_simulation_ids"):
        raise HTTPException(status_code=422, detail="Study cannot activate without linked authoritative simulations")
    try:
        updated = _persist_metadata(repo, record, metadata, status="active")
    except StudyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _summary(updated)


@router.post("/{study_id}/simulations")
def link_study_simulations(study_id: str, payload: SimulationLinksRequest, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"]); metadata = _phase4_metadata(record)
    available = {item.id: item for item in repo.list_simulation_jobs_for_experiment(record.id)}
    existing = list(metadata.get("linked_simulation_ids", []))
    if len(set(payload.simulation_ids)) != len(payload.simulation_ids):
        raise HTTPException(status_code=422, detail="Duplicate simulation link requested")
    for simulation_id in payload.simulation_ids:
        job = available.get(simulation_id)
        if job is None or job.user_id != current_user["id"]:
            raise HTTPException(status_code=404, detail="Simulation not found in this Study experiment")
        if simulation_id in existing:
            raise HTTPException(status_code=409, detail="Simulation is already linked to this Study")
    metadata["linked_simulation_ids"] = existing + payload.simulation_ids
    updated = _persist_metadata(repo, record, metadata)
    fresh = _phase4_metadata(updated)
    _study_evidence(repo, current_user["id"], updated, fresh, "links", {"linked_simulation_ids": fresh["linked_simulation_ids"]})
    return _summary(updated)


@router.delete("/{study_id}/simulations/{simulation_id}")
def unlink_study_simulation(study_id: str, simulation_id: str, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"]); metadata = _phase4_metadata(record)
    if simulation_id not in metadata.get("linked_simulation_ids", []):
        raise HTTPException(status_code=404, detail="Study simulation link not found")
    metadata["linked_simulation_ids"] = [item for item in metadata["linked_simulation_ids"] if item != simulation_id]
    return _summary(_persist_metadata(repo, record, metadata))


@router.post("/{study_id}/doe", status_code=201)
def plan_study_doe(study_id: str, payload: DOEConfig, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"]); metadata = _phase4_metadata(record)
    try:
        plan = generate_doe(metadata.get("factors", []), payload)
    except StudyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    metadata["doe"] = plan
    updated = _persist_metadata(repo, record, metadata)
    fresh = _phase4_metadata(updated)
    evidence_id = _study_evidence(repo, current_user["id"], updated, fresh, "doe", {"doe": plan})
    return {**plan, "evidence_id": evidence_id, "study_id": study_id, "definition_hash": fresh["definition_hash"]}


@router.post("/{study_id}/analyze", status_code=201)
def analyze_study(study_id: str, payload: StudyAnalysisRequest, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"]); metadata = _phase4_metadata(record)
    try:
        analysis = run_study_analysis(repo, record, current_user["id"], metadata, payload)
    except (StudyError, AnalysisInputError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    metadata["analysis_ids"] = list(dict.fromkeys([*metadata.get("analysis_ids", []), analysis.id])); metadata["latest_analysis_id"] = analysis.id
    _persist_metadata(repo, record, metadata)
    return {"id": analysis.id, "study_id": study_id, "dataset_hash": analysis.dataset_hash, "reproducibility_hash": analysis.reproducibility_hash, "findings": analysis.result.get("findings", {}), "warnings": analysis.warnings}


@router.get("/{study_id}/analyses")
def list_study_analyses(study_id: str, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"])
    items = [item for item in repo.list_analyses_for_experiment(study_id) if item.analysis_type == "research_study" and item.user_id == current_user["id"]]
    return {"items": [{"id": item.id, "dataset_hash": item.dataset_hash, "reproducibility_hash": item.reproducibility_hash, "created_at": item.created_at} for item in items]}


@router.get("/{study_id}/findings")
def get_study_findings(study_id: str, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"]); metadata = _phase4_metadata(record)
    analysis_id = metadata.get("latest_analysis_id")
    analysis = repo.get_analysis(analysis_id) if analysis_id else None
    if analysis is None or analysis.user_id != current_user["id"]:
        raise HTTPException(status_code=404, detail="No authoritative Study analysis found")
    return analysis.result.get("findings", {})


@router.post("/{study_id}/next-experiments", status_code=201)
def propose_next_experiments(study_id: str, payload: NextExperimentRequest, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"]); metadata = _phase4_metadata(record)
    analysis = repo.get_analysis(metadata.get("latest_analysis_id")) if metadata.get("latest_analysis_id") else None
    if analysis is None or analysis.user_id != current_user["id"]:
        raise HTTPException(status_code=422, detail="Next-experiment proposals require an authoritative Study analysis")
    dataset = ExperimentDataset.model_validate(analysis.result["dataset"])
    findings = analysis.result.get("findings", {})
    plan = next_experiments(
        dataset, metadata.get("factors", []), payload, source_analysis_id=analysis.id,
        response_surface_result=findings.get("response_surface"),
        declared_response_columns={item["column"] for item in metadata.get("responses", [])},
        study_definition_hash=metadata["definition_hash"], study_revision=metadata["revision"],
        analysis_reproducibility_hash=analysis.reproducibility_hash,
    )
    evidence_id = _study_evidence(repo, current_user["id"], record, metadata, "next_experiment", {"analysis_id": analysis.id, "plan": plan})
    return {**plan, "evidence_id": evidence_id}


@router.get("/{study_id}/manifest")
def study_manifest(study_id: str, current_user: dict = Depends(get_current_user)) -> dict[str, Any]:
    repo = get_repository(); record = _owned_study(study_id, current_user["id"]); metadata = _phase4_metadata(record)
    analysis = repo.get_analysis(metadata.get("latest_analysis_id")) if metadata.get("latest_analysis_id") else None
    if analysis is not None and analysis.user_id != current_user["id"]: analysis = None
    return manifest(record, metadata, analysis)


@router.post("/{study_id}/comparison-plan")
def comparison_plan(
    study_id: str, payload: ComparativeRunRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return build_comparison_plan(study_id, current_user["id"], payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, SolverValidationError, MaterialNotFoundError, MaterialPropertyNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{study_id}/comparative-runs", status_code=202)
def comparative_run(
    study_id: str, payload: ComparativeRunRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        created = create_comparative_batch(study_id, current_user["id"], payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, SolverValidationError, MaterialNotFoundError, MaterialPropertyNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from app.comparative_tasks import run_comparative_batch_task
    specifications = created.pop("specifications")
    try:
        run_comparative_batch_task.delay(
            job_id=created["job_id"], study_id=study_id, user_id=current_user["id"],
            specifications=specifications,
        )
    except Exception as exc:
        repo = get_repository()
        repo.update_job(
            created["job_id"], status="failed", progress_percent=100,
            error_code="worker_dispatch_failed",
            safe_error_message="The computation worker is unavailable; no simulation was executed.",
        )
        for specification in specifications:
            repo.update_simulation_job(
                specification["simulation_id"], status="failed", progress_percent=100,
                error_code="worker_dispatch_failed",
                safe_error_message="The computation worker is unavailable; no result was produced.",
            )
        raise HTTPException(status_code=503, detail="The computation worker is unavailable") from exc
    return created
