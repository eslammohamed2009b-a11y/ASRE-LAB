"""Owner-scoped Research Study aggregate over the existing experiment graph."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.repository import ExperimentRecord, get_repository
from app.v2.repository import EvidenceRepository
from app.comparative_service import ComparativeRunRequest, build_comparison_plan, create_comparative_batch
from app.module2_simulation.materials import MaterialNotFoundError, MaterialPropertyNotFoundError
from app.module2_simulation.solvers.base_solver import SolverValidationError


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
    hypothesis: str | None = Field(default=None, max_length=2000)
    geometry_family: str = Field(min_length=1, max_length=100)
    independent_variables: list[VariableDefinition] = Field(default_factory=list, max_length=32)
    output_variables: list[VariableDefinition] = Field(default_factory=list, max_length=64)
    controlled_variables: list[ControlledVariable] = Field(default_factory=list, max_length=64)
    objectives: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    solver_ids: list[str] = Field(default_factory=list, max_length=16)
    material: str | None = Field(default=None, max_length=100)
    boundary_conditions: dict[str, Any] = Field(default_factory=dict)
    numerical_settings: dict[str, Any] = Field(default_factory=dict)
    design_space: dict[str, Any] = Field(default_factory=dict)


class StudyUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    research_question: str | None = Field(default=None, min_length=3, max_length=2000)
    hypothesis: str | None = Field(default=None, max_length=2000)
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

    return {
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
    }


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
    return _summary(repo.get_experiment(study_id))


@router.get("")
def list_studies(
    include_archived: bool = False, current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    records = get_repository().list_experiments_for_user(current_user["id"])
    if not include_archived:
        records = [record for record in records if record.status != "archived"]
    return {"items": [_summary(record) for record in records]}


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
    metadata = _metadata(record)
    metadata.pop("title", None)
    metadata.update(changes)
    repo.update_experiment(
        study_id,
        name=title,
        status=status,
        input_specification={
            **record.input_specification,
            "study": metadata,
            "schema_version": "research-study-v1",
        },
    )
    return _summary(repo.get_experiment(study_id))


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
