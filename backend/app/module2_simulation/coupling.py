"""Owner-scoped one-way steady thermal-to-structural coupling."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.core.repository import AnalysisRecord, PersistenceRepository, get_repository
from app.core.storage import get_storage
from app.module2_simulation.materials import properties_as_dict
from app.module2_simulation.tasks import run_simulation_job


class ThermalStructuralCouplingRequest(BaseModel):
    experiment_id: str
    design_id: str
    material: str = "steel"
    length_m: float = Field(gt=0)
    cross_section_area_m2: float = Field(gt=0)
    num_elements: int = Field(default=20, ge=2, le=200)
    reference_temperature_c: float = 20.0
    hot_end_temperature_c: float
    restraint: str = "fully_restrained"


class CouplingNotFoundError(LookupError):
    pass


def _record_input(repo, simulation_id, material, boundary, tolerance=1e-9):
    repo.record_simulation_input(simulation_id=simulation_id, material_name=material,
        material_properties=properties_as_dict(material), units={}, initial_conditions={},
        boundary_conditions=boundary, numerical_settings={"max_iterations":300,"tolerance":tolerance})


def run_thermal_structural_coupling(request: ThermalStructuralCouplingRequest, user_id: str,
                                    repository: PersistenceRepository | None = None, storage=None) -> AnalysisRecord:
    repo, storage = repository or get_repository(), storage or get_storage()
    experiment = repo.get_experiment(request.experiment_id)
    designs = repo.list_design_models_for_experiment(request.experiment_id) if experiment else []
    if experiment is None or experiment.user_id != user_id or request.design_id not in {d.id for d in designs}:
        raise CouplingNotFoundError("Experiment or design not found")
    source_ids: list[str] = []
    status, warnings, result = "running", [], {}
    try:
        thermal_bc = {"ambient_temperature_c":request.reference_temperature_c,
                      "prescribed_temperature_c":request.hot_end_temperature_c}
        thermal_id = repo.create_simulation_job(user_id=user_id, solver_id="thermal_conduction_v1",
            experiment_id=request.experiment_id, design_id=request.design_id)
        source_ids.append(thermal_id); _record_input(repo, thermal_id, request.material, thermal_bc)
        thermal_outcome = run_simulation_job(simulation_id=thermal_id, solver_id="thermal_conduction_v1",
            material_name=request.material, geometry={"dimension":"1d","length_m":request.length_m,"num_elements":request.num_elements},
            boundary_conditions=thermal_bc, initial_conditions={}, numerical_settings={"max_iterations":300,"tolerance":1e-9},
            experiment_id=request.experiment_id, design_id=request.design_id, repository=repo, storage=storage)
        if thermal_outcome["status"] != "completed":
            raise RuntimeError("thermal stage failed")
        temperatures = repo.get_simulation_result(thermal_id).field_values
        mapped_temperature = sum(temperatures)/len(temperatures)
        delta_temperature = mapped_temperature-request.reference_temperature_c

        structural_bc = {"thermal_delta_temperature_c":delta_temperature,"thermal_restraint":request.restraint}
        structural_id = repo.create_simulation_job(user_id=user_id, solver_id="structural_linear_1d_v1",
            experiment_id=request.experiment_id, design_id=request.design_id)
        source_ids.append(structural_id); _record_input(repo, structural_id, request.material, structural_bc)
        structural_outcome = run_simulation_job(simulation_id=structural_id, solver_id="structural_linear_1d_v1",
            material_name=request.material, geometry={"dimension":"1d","length_m":request.length_m,
                "cross_section_area_m2":request.cross_section_area_m2,"num_elements":request.num_elements},
            boundary_conditions=structural_bc, initial_conditions={}, numerical_settings={"max_iterations":1,"tolerance":1e-9},
            experiment_id=request.experiment_id, design_id=request.design_id, repository=repo, storage=storage)
        if structural_outcome["status"] != "completed":
            raise RuntimeError("structural stage failed")
        structural = repo.get_simulation_result(structural_id)
        result = {"coupling":"one_way_sequential_steady_linear","mapping_method":"arithmetic_mean_nodal_temperature",
                  "reference_temperature_c":request.reference_temperature_c,"mapped_mean_temperature_c":mapped_temperature,
                  "delta_temperature_k":delta_temperature,"restraint":request.restraint,
                  "thermal_simulation_id":thermal_id,"structural_simulation_id":structural_id,
                  "structural_metrics":structural.summary_metrics}
        status = "completed"
    except Exception as exc:
        status = "failed" if not source_ids else "partial_failure"
        warnings.append(f"Coupled workflow stopped after a stage failure: {type(exc).__name__}")
    now = datetime.now(timezone.utc).isoformat()
    evidence = {"request":request.model_dump(mode="json"),"sources":source_ids,"result":result,"status":status}
    digest = hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    record = AnalysisRecord(id=str(uuid.uuid4()),experiment_id=request.experiment_id,user_id=user_id,
        analysis_type="thermal_structural_coupling",status=status,dataset_hash=digest,configuration=request.model_dump(mode="json"),
        result=result,warnings=warnings,source_design_ids=[request.design_id],source_simulation_ids=source_ids,
        data_quality={"mapping":"arithmetic_mean","compatible_discretization":"uniform_1d"},engine_version="1.0",
        reproducibility_hash=digest,created_at=now,updated_at=now)
    repo.create_analysis(record)
    return record


def get_coupling(coupling_id: str, user_id: str, repository: PersistenceRepository | None = None) -> AnalysisRecord:
    record = (repository or get_repository()).get_analysis(coupling_id)
    if record is None or record.user_id != user_id or record.analysis_type != "thermal_structural_coupling":
        raise CouplingNotFoundError("Coupling not found")
    return record
