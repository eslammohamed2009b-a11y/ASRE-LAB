"""Durable orchestration for the authoritative Module 1 -> 2 -> 3 pipeline.

The integrated pipeline uses the same persisted design, simulation, field-result,
and deterministic-analysis contracts as the standalone module APIs.  The legacy
``/api/simulate`` compatibility surface is intentionally not called from here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.repository import PersistenceRepository, get_repository
from app.core.storage import get_storage
from app.module1_design.nl_parser import parse_design_request
from app.module1_design.schemas import DesignParameters, DesignVariationRequest
from app.module1_design.tasks import persist_generated_design
from app.module2_simulation.materials import properties_as_dict
from app.module2_simulation.schemas import AnalysisType
from app.module2_simulation.tasks import run_simulation_job
from app.module3_analysis.schemas import AnalysisCreateRequest
from app.module3_analysis.service import run_experiment_analysis

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


PIPELINE_REFERENCE_SCENARIOS = {
    AnalysisType.THERMAL: {
        "solver_id": "thermal_conduction_v1",
        "disclosure": (
            "1D steady-state reference scenario derived from the persisted design length; "
            "20 degC end temperatures and 100 W/m3 volumetric heating are prescribed inputs, "
            "not inferred service conditions."
        ),
    },
    AnalysisType.STRUCTURAL: {
        "solver_id": "structural_linear_1d_v1",
        "disclosure": (
            "1D axial-bar reference scenario derived from the persisted design length and "
            "base-length x wall-thickness area; the 1000 N end load is a prescribed comparison "
            "load, not a predicted service load."
        ),
    },
}


def _positive_number(parameters: dict[str, Any], name: str, fallback: float) -> float:
    value = parameters.get(name)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return fallback


def _authoritative_simulation_inputs(
    analysis: AnalysisType, parameters: dict[str, Any]
) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    """Map a CAD variant to a bounded, explicitly disclosed reference model.

    This is a comparison scenario, not arbitrary-CAD mesh ingestion. Unsupported
    families return ``None`` instead of falling through to an empirical placeholder.
    """
    scenario = PIPELINE_REFERENCE_SCENARIOS.get(analysis)
    if scenario is None:
        return None
    length_m = _positive_number(
        parameters, "height_m", _positive_number(parameters, "base_length_m", 1.0)
    )
    if analysis is AnalysisType.THERMAL:
        geometry = {"dimension": "1d", "length_m": length_m, "num_elements": 20}
        boundary_conditions = {
            "ambient_temperature_c": 20.0,
            "prescribed_temperature_c": 20.0,
            "heat_source_w_m3": 100.0,
        }
    else:
        base_length = _positive_number(parameters, "base_length_m", 1.0)
        thickness = _positive_number(parameters, "wall_thickness_m", 0.1)
        geometry = {
            "dimension": "1d",
            "length_m": length_m,
            "cross_section_area_m2": base_length * thickness,
            "num_elements": 20,
        }
        boundary_conditions = {"axial_load_n": 1000.0}
    return scenario["solver_id"], geometry, boundary_conditions


def _persist_authoritative_simulation(
    repo: PersistenceRepository,
    *,
    user_id: str,
    experiment_id: str,
    design_model_id: str,
    analysis: AnalysisType,
    material: str,
    design_parameters: dict[str, Any],
) -> tuple[str | None, str | None]:
    mapped = _authoritative_simulation_inputs(analysis, design_parameters)
    if mapped is None:
        return None, analysis.value
    solver_id, geometry, boundary_conditions = mapped
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
        units={"geometry.length_m": "m", "geometry.cross_section_area_m2": "m^2"},
        initial_conditions={},
        boundary_conditions=boundary_conditions,
        numerical_settings={"max_iterations": 300, "tolerance": 1e-5},
    )
    outcome = run_simulation_job(
        simulation_id=simulation_id,
        solver_id=solver_id,
        material_name=material,
        geometry=geometry,
        boundary_conditions=boundary_conditions,
        initial_conditions={},
        numerical_settings={"max_iterations": 300, "tolerance": 1e-5},
        experiment_id=experiment_id,
        design_id=design_model_id,
        repository=repo,
        storage=get_storage(),
    )
    return simulation_id, None if outcome["status"] in {"completed", "partial_failure"} else analysis.value


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
    successful_simulation_ids: list[str] = []
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
                design_model_id = persist_generated_design(
                    repo=repo,
                    storage=get_storage(),
                    experiment_id=experiment_id,
                    user_id=user_id,
                    variation_index=idx,
                    params=DesignParameters(**design.get("params", {})),
                    result=design,
                )
                item_failed = False
                for analysis in analyses:
                    current = repo.get_job(job_id)
                    if current is not None and current.status == "cancelled":
                        return {**_job_payload(current), "skipped_analyses": sorted(skipped_analyses)}
                    simulation_id, failed_analysis = _persist_authoritative_simulation(
                        repo,
                        user_id=user_id,
                        experiment_id=experiment_id,
                        design_model_id=design_model_id,
                        analysis=analysis,
                        material=base_params.material.value if base_params.material else "concrete",
                        design_parameters=design.get("params", {}),
                    )
                    if simulation_id and not failed_analysis:
                        successful_simulation_ids.append(simulation_id)
                    if failed_analysis:
                        skipped_analyses.add(failed_analysis)
                        item_failed = True
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

        analysis_record = None
        if successful_simulation_ids:
            analysis_record = run_experiment_analysis(
                experiment_id, user_id, AnalysisCreateRequest(), repository=repo
            )
        current = repo.get_job(job_id)
        if current is not None and current.status == "cancelled":
            return {**_job_payload(current), "skipped_analyses": sorted(skipped_analyses)}
        status = "partial_failure" if failed_count else "completed"
        if not successful_simulation_ids and failed_count:
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
        analysis_record = None

    final_job = repo.get_job(job_id)
    return {
        **_job_payload(final_job),
        "base_params": base_params.model_dump() if "base_params" in locals() else None,
        "generated_count": generated_count,
        "analyzed_count": len(successful_simulation_ids),
        "analysis_id": analysis_record.id if analysis_record else None,
        "analysis": analysis_record.model_dump(mode="json") if analysis_record else None,
        "reference_scenarios": {
            item.value: PIPELINE_REFERENCE_SCENARIOS[item]["disclosure"]
            for item in analyses if item in PIPELINE_REFERENCE_SCENARIOS
        },
        "persistence_enabled": True,
        "skipped_analyses": sorted(skipped_analyses),
    }
