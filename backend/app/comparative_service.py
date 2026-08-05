"""Controlled multi-design physics planning and dispatch."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.repository import PersistenceRepository, get_repository
from app.module2_simulation.materials import properties_as_dict
from app.module2_simulation.schemas import (
    BoundaryConditions, Geometry, InitialConditions, MaterialSelection,
    NumericalSettings, SimulationCreateRequest,
)
from app.module2_simulation.service import SOLVER_CLASSES
from app.module2_simulation.solver_registry import require_available


class ComparativeRunRequest(BaseModel):
    design_ids: list[str] = Field(min_length=1, max_length=100)
    solver_id: Literal["pyramid_thermal_conduction_v1", "thermal_conduction_v1"]
    material: str = Field(min_length=2, max_length=100)
    boundary_conditions: BoundaryConditions
    numerical_settings: NumericalSettings = Field(default_factory=NumericalSettings)
    grid_resolution: int = Field(default=17, ge=9, le=41)
    reference_num_elements: int = Field(default=40, ge=2, le=499)


def _geometry_for_design(payload: ComparativeRunRequest, parameters: dict[str, Any]) -> Geometry:
    height = parameters.get("height_m")
    base = parameters.get("base_length_m")
    if not isinstance(height, (int, float)) or height <= 0:
        raise ValueError("Every selected design requires a positive height_m")
    if payload.solver_id == "pyramid_thermal_conduction_v1":
        if not isinstance(base, (int, float)) or base <= 0:
            raise ValueError("Every selected pyramid design requires a positive base_length_m")
        return Geometry(
            dimension="pyramid3d", base_length_m=base, height_m=height,
            grid_resolution=payload.grid_resolution,
        )
    return Geometry(dimension="1d", length_m=height, num_elements=payload.reference_num_elements)


def build_comparison_plan(
    study_id: str, user_id: str, payload: ComparativeRunRequest,
    repository: PersistenceRepository | None = None,
) -> dict[str, Any]:
    repo = repository or get_repository()
    study = repo.get_experiment(study_id)
    if study is None or study.user_id != user_id:
        raise LookupError("Study not found")
    if study.status == "archived":
        raise ValueError("Archived studies cannot execute simulations")
    if len(payload.design_ids) != len(set(payload.design_ids)):
        raise ValueError("Selected design IDs must be unique")

    available = {item.id: item for item in repo.list_design_models_for_experiment(study_id)}
    missing = [design_id for design_id in payload.design_ids if design_id not in available]
    if missing:
        raise LookupError("One or more selected designs were not found")
    selected = [available[design_id] for design_id in payload.design_ids]
    families = {item.geometry_family for item in selected}
    if len(families) != 1:
        raise ValueError("Selected designs have incompatible geometry families")
    if payload.solver_id == "pyramid_thermal_conduction_v1" and families != {"pyramid"}:
        raise ValueError("The geometry-aware pyramid solver accepts pyramid designs only")
    if payload.grid_resolution % 2 == 0 and payload.solver_id == "pyramid_thermal_conduction_v1":
        raise ValueError("Geometry-aware pyramid grid resolution must be odd")

    require_available(payload.solver_id)
    properties_as_dict(payload.material)
    variants = []
    for design in selected:
        geometry = _geometry_for_design(payload, design.parameters)
        request = SimulationCreateRequest(
            solver_id=payload.solver_id, experiment_id=study_id, design_id=design.id,
            material=MaterialSelection(name=payload.material), geometry=geometry,
            boundary_conditions=payload.boundary_conditions,
            initial_conditions=InitialConditions(), numerical_settings=payload.numerical_settings,
        )
        SOLVER_CLASSES[payload.solver_id]().validate_inputs(request)
        variants.append({
            "design_id": design.id, "variation_index": design.variation_index,
            "parameters": design.parameters, "geometry": geometry.model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
        })

    parameter_names = sorted({name for item in selected for name in item.parameters})
    varies, design_constants = [], []
    for name in parameter_names:
        values = {
            json.dumps(item.parameters.get(name), sort_keys=True, default=str) for item in selected
        }
        (varies if len(values) > 1 else design_constants).append(name)
    evaluation_class = (
        "geometry_aware_model" if payload.solver_id == "pyramid_thermal_conduction_v1"
        else "reference_parameter_model"
    )
    disclosure = (
        "3D steady thermal conduction on a structured Cartesian mask of the resolved parametric pyramid. "
        "This is geometry-sensitive but is not CAD-mesh FEA."
        if evaluation_class == "geometry_aware_model"
        else "1D steady thermal reference model whose length is derived from design height. "
        "This does not simulate the pyramid geometry."
    )
    return {
        "study_id": study_id, "solver_id": payload.solver_id,
        "evaluation_class": evaluation_class, "model_disclosure": disclosure,
        "variant_count": len(variants), "varies": varies,
        "held_constant": {
            "design_parameters": design_constants,
            "material": payload.material,
            "boundary_conditions": payload.boundary_conditions.model_dump(mode="json"),
            "numerical_settings": payload.numerical_settings.model_dump(mode="json"),
            "grid_resolution": payload.grid_resolution if evaluation_class == "geometry_aware_model" else None,
            "reference_num_elements": payload.reference_num_elements if evaluation_class == "reference_parameter_model" else None,
        },
        "variants": variants,
        "warnings": [
            "Only parameters listed under VARIES differ; review derived pyramid slope when base or height changes.",
            "All results remain bounded by the selected solver's declared validity envelope.",
        ],
    }


def create_comparative_batch(
    study_id: str, user_id: str, payload: ComparativeRunRequest,
    repository: PersistenceRepository | None = None,
) -> dict[str, Any]:
    repo = repository or get_repository()
    plan = build_comparison_plan(study_id, user_id, payload, repo)
    job_id = repo.create_job(study_id, user_id, "comparative_simulation_batch", len(plan["variants"]))
    material_properties = properties_as_dict(payload.material)
    specifications = []
    for item in plan["variants"]:
        request = item["request"]
        simulation_id = repo.create_simulation_job(
            user_id=user_id, solver_id=payload.solver_id, experiment_id=study_id,
            design_id=item["design_id"],
        )
        repo.record_simulation_input(
            simulation_id=simulation_id, material_name=payload.material,
            material_properties=material_properties,
            units={
                "geometry.base_length_m": "m", "geometry.height_m": "m",
                "geometry.length_m": "m", "temperature": "degC", "heat_source": "W/m^3",
            },
            initial_conditions=request["initial_conditions"],
            boundary_conditions=request["boundary_conditions"],
            numerical_settings=request["numerical_settings"], geometry=request["geometry"],
        )
        specifications.append({
            "simulation_id": simulation_id, "solver_id": payload.solver_id,
            "material_name": payload.material, "geometry": request["geometry"],
            "boundary_conditions": request["boundary_conditions"],
            "initial_conditions": request["initial_conditions"],
            "numerical_settings": request["numerical_settings"],
            "experiment_id": study_id, "design_id": item["design_id"],
        })
    repo.update_experiment(study_id, status="active")
    return {"job_id": job_id, "study_id": study_id, "status": "queued", "plan": plan, "specifications": specifications}
