from app.core.celery_app import celery_app
from app.module2_simulation.schemas import SimulationRunRequest
from app.module2_simulation.service import run_simulation_service


@celery_app.task(name="module2.run_simulation_task")
def run_simulation_task(payload: dict) -> dict:
    request = SimulationRunRequest(**payload)
    return run_simulation_service(request).model_dump()
