from enum import Enum

from pydantic import BaseModel, Field


class AnalysisType(str, Enum):
    THERMAL = "thermal"
    STRUCTURAL = "structural"
    WIND_LOAD = "wind_load"


class AdvisorRequest(BaseModel):
    model_type: str = Field(min_length=2)


class AdvisorResponse(BaseModel):
    recommended: list[str]


class SimulationRunRequest(BaseModel):
    design_id: str = "unknown"
    geometry_type: str = "tower"
    analysis_type: AnalysisType
    material: str = "concrete"
    boundary_conditions: dict = Field(default_factory=dict)


class SimulationRunResponse(BaseModel):
    analysis_type: str
    design_id: str
    summary_metrics: dict[str, float]
    field_values: list[float]
    hotspot_node_ids: list[int]
