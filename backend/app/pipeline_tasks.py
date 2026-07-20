from app.core.celery_app import celery_app
from app.module2_simulation.schemas import AnalysisType
from app.pipeline_service import run_pipeline_flow


@celery_app.task(name="pipeline.run_pipeline_task")
def run_pipeline_task(payload: dict) -> dict:
    analyses = [AnalysisType(a) for a in payload.get("analyses", [])]
    return run_pipeline_flow(
        prompt=payload["prompt"],
        variation_count=payload["variation_count"],
        analyses=analyses,
        user_id=payload["user_id"],
        job_id=payload.get("job_id"),
        experiment_id=payload.get("experiment_id"),
    )
