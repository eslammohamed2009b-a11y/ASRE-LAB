"""Durable orchestration for the legacy Module 1 -> 2 -> 3 pipeline.

The public pipeline request/response remains legacy-compatible, while all
persistence is translated onto the authoritative repository contracts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.repository import PersistenceRepository, SimulationResultRecord, get_repository
from app.module1_design.nl_parser import parse_design_request
from app.module1_design.schemas import DesignVariationRequest
from app.module2_simulation.materials import properties_as_dict
from app.module2_simulation.schemas import AnalysisType, SimulationRunRequest
from app.module2_simulation.service import run_simulation_service
from app.module2_simulation.solver_registry import UnsupportedAnalysisError
from app.module3_analysis.clustering import cluster_designs
from app.module3_analysis.correlation import build_correlation_matrix
from app.module3_analysis.synthesis import synthesize_report

logger = logging.getLogger(__name__)
TERMINAL_STATES = {"partial_failure", "completed", "failed", "cancelled"}


def generate_design_matrix(request: DesignVariationRequest) -> list[dict]:
    """Lazy CAD import keeps status/retrieval paths independent of the CAD runtime."""
    from app.module1_design.multiprocessing_generator import generate_design_matrix as generate

    return generate(request)


class PipelineNotFoundError(Exception):
    """Unknown pipeline job, or one owned by another user."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_payload(job: Any) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "experiment_id": job.experiment_id,
        "status": job.status,
        "requested_count": job.requested_count,
        "completed_count": job.completed_count,
        "failed_count": job.failed_count,
        "progress_percent": job.progress_percent,
        "error_code": job.error_code,
        "safe_error_message": job.safe_error_message,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def create_pipeline_job(
    prompt: str,
    variation_count: int,
    analyses: list[AnalysisType],
    user_id: str,
    repo: PersistenceRepository | None = None,
) -> tuple[str, str]:
    repo = repo or get_repository()
    experiment_id = repo.create_experiment(
        user_id=user_id,
        name="Integrated pipeline run",
        input_specification={
            "prompt": prompt,
            "variation_count": variation_count,
            "analyses": [analysis.value for analysis in analyses],
        },
    )
    job_id = repo.create_job(
        experiment_id=experiment_id,
        user_id=user_id,
        job_type="integrated_pipeline",
        requested_count=variation_count,
    )
    return job_id, experiment_id


def get_pipeline_job_service(job_id: str, user_id: str) -> dict[str, Any]:
    job = get_repository().get_job(job_id)
    if job is None or job.user_id != user_id or job.job_type != "integrated_pipeline":
        raise PipelineNotFoundError(job_id)
    return _job_payload(job)


def cancel_pipeline_job_service(job_id: str, user_id: str) -> dict[str, Any]:
    repo = get_repository()
    job = repo.get_job(job_id)
    if job is None or job.user_id != user_id or job.job_type != "integrated_pipeline":
        raise PipelineNotFoundError(job_id)
    if job.status not in TERMINAL_STATES:
        repo.update_job(job_id, status="cancelled", finished_at=_now_iso())
        job = repo.get_job(job_id)
    return _job_payload(job)


def _persist_legacy_simulation(
    repo: PersistenceRepository,
    *,
    user_id: str,
    experiment_id: str,
    design_model_id: str,
    analysis: AnalysisType,
    material: str,
    geometry_type: str,
    legacy_design_id: str,
) -> tuple[dict[str, float], str | None]:
    """Narrow adapter from the legacy solver response to Module 2 tables."""
    solver_id = f"legacy_{analysis.value}_v1"
    simulation_id = repo.create_simulation_job(
        user_id=user_id,
        solver_id=solver_id,
        experiment_id=experiment_id,
        design_id=design_model_id,
    )
    repo.record_simulation_input(
        simulation_id=simulation_id,
        material_name=material,
        material_properties=properties_as_dict(material),
        units={},
        initial_conditions={},
        boundary_conditions={"geometry_type": geometry_type},
        numerical_settings={"interface": "legacy_pipeline"},
    )
    repo.update_simulation_job(
        simulation_id, status="running", progress_percent=10, started_at=_now_iso()
    )
    try:
        result = run_simulation_service(
            SimulationRunRequest(
                design_id=legacy_design_id,
                geometry_type=geometry_type,
                analysis_type=analysis,
                material=material,
            )
        )
    except UnsupportedAnalysisError as exc:
        repo.update_simulation_job(
            simulation_id,
            status="failed",
            progress_percent=100,
            error_code="unsupported_analysis",
            safe_error_message=str(exc),
            finished_at=_now_iso(),
        )
        return {}, analysis.value
    except Exception:
        logger.error("Pipeline simulation %s failed", simulation_id, exc_info=True)
        repo.update_simulation_job(
            simulation_id,
            status="failed",
            progress_percent=100,
            error_code="simulation_failed",
            safe_error_message="The simulation failed unexpectedly.",
            finished_at=_now_iso(),
        )
        return {}, analysis.value

    repo.record_simulation_result(
        SimulationResultRecord(
            simulation_id=simulation_id,
            solver_id=solver_id,
            solver_version="legacy-v1",
            assumptions=["Legacy /api/simulate compatibility solver"],
            warnings=[],
            converged=True,
            summary_metrics=result.summary_metrics,
            field_values=result.field_values,
            hotspot_node_ids=result.hotspot_node_ids,
            application_version=settings.APPLICATION_VERSION,
        )
    )
    repo.update_simulation_job(
        simulation_id, status="completed", progress_percent=100, finished_at=_now_iso()
    )
    return result.summary_metrics, None


def run_pipeline_flow(
    prompt: str,
    variation_count: int,
    analyses: list[AnalysisType],
    user_id: str,
    job_id: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    repo = get_repository()
    if job_id is None or experiment_id is None:
        job_id, experiment_id = create_pipeline_job(
            prompt, variation_count, analyses, user_id, repo
        )

    job = repo.get_job(job_id)
    if job is None or job.user_id != user_id:
        raise PipelineNotFoundError(job_id)
    if job.status == "cancelled":
        return _job_payload(job)

    repo.update_job(job_id, status="running", started_at=_now_iso())
    design_results: list[dict[str, Any]] = []
    skipped_analyses: set[str] = set()
    completed_count = 0
    failed_count = 0
    generated_count = 0

    try:
        base_params = parse_design_request(prompt)
        generated_designs = generate_design_matrix(
            DesignVariationRequest(base_params=base_params, variation_count=variation_count)
        )
        generated_count = len(generated_designs)

        for idx, design in enumerate(generated_designs):
            current = repo.get_job(job_id)
            if current is not None and current.status == "cancelled":
                return {**_job_payload(current), "skipped_analyses": sorted(skipped_analyses)}

            if "error" in design:
                failed_count += 1
            else:
                design_model_id = repo.create_design_model(
                    experiment_id=experiment_id,
                    user_id=user_id,
                    geometry_family=base_params.geometry_type.value,
                    parameters=design.get("params", {}),
                    units={"length": "m", "angle": "deg"},
                    variation_index=idx,
                    generation_status="completed",
                )
                merged_metrics: dict[str, float] = {}
                item_failed = False
                for analysis in analyses:
                    current = repo.get_job(job_id)
                    if current is not None and current.status == "cancelled":
                        return {**_job_payload(current), "skipped_analyses": sorted(skipped_analyses)}
                    metrics, failed_analysis = _persist_legacy_simulation(
                        repo,
                        user_id=user_id,
                        experiment_id=experiment_id,
                        design_model_id=design_model_id,
                        analysis=analysis,
                        material=base_params.material.value if base_params.material else "concrete",
                        geometry_type=base_params.geometry_type.value,
                        legacy_design_id=design["design_id"],
                    )
                    merged_metrics.update(metrics)
                    if failed_analysis:
                        skipped_analyses.add(failed_analysis)
                        item_failed = True

                if merged_metrics:
                    design_results.append(
                        {"design_id": design["design_id"], "params": design["params"], "metrics": merged_metrics}
                    )
                if item_failed:
                    failed_count += 1
                else:
                    completed_count += 1

            progress = int(((idx + 1) / max(variation_count, 1)) * 90)
            repo.update_job(
                job_id,
                completed_count=completed_count,
                failed_count=failed_count,
                progress_percent=progress,
            )

        cluster_output = cluster_designs(design_results, n_clusters=4)
        correlation_output = build_correlation_matrix(design_results)
        insights = synthesize_report(cluster_output, correlation_output)
        current = repo.get_job(job_id)
        if current is not None and current.status == "cancelled":
            return {**_job_payload(current), "skipped_analyses": sorted(skipped_analyses)}
        status = "partial_failure" if failed_count else "completed"
        if not design_results and failed_count:
            status = "failed"
        repo.update_job(
            job_id,
            status=status,
            completed_count=completed_count,
            failed_count=failed_count,
            progress_percent=100,
            error_code="partial_failure" if status == "partial_failure" else None,
            safe_error_message=("Some designs or simulations failed; successful records were preserved." if status == "partial_failure" else None),
            finished_at=_now_iso(),
        )
    except Exception:
        logger.error("Pipeline job %s failed", job_id, exc_info=True)
        current = repo.get_job(job_id)
        if current is not None and current.status == "cancelled":
            return {**_job_payload(current), "skipped_analyses": sorted(skipped_analyses)}
        repo.update_job(
            job_id,
            status="partial_failure" if completed_count else "failed",
            completed_count=completed_count,
            failed_count=max(failed_count, variation_count - completed_count),
            progress_percent=100,
            error_code="pipeline_failed",
            safe_error_message="The pipeline failed unexpectedly; successful intermediate records were preserved.",
            finished_at=_now_iso(),
        )
        cluster_output = cluster_designs(design_results, n_clusters=4)
        correlation_output = build_correlation_matrix(design_results)
        insights = synthesize_report(cluster_output, correlation_output)

    final_job = repo.get_job(job_id)
    return {
        **_job_payload(final_job),
        "base_params": base_params.model_dump() if "base_params" in locals() else None,
        "generated_count": generated_count,
        "analyzed_count": len(design_results),
        "clusters": cluster_output,
        "correlation": correlation_output,
        "insights": insights,
        "persistence_enabled": True,
        "skipped_analyses": sorted(skipped_analyses),
    }
