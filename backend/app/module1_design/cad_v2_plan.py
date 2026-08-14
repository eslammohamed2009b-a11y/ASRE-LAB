"""Non-authoritative, schema-constrained planning boundary for future AI use."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.module1_design.cad_v2_schemas import (
    EngineeringDesignDocumentV2,
    ParameterDefinition,
    StrictModel,
)


class PlannedComponent(StrictModel):
    component_id: str
    purpose: str = Field(min_length=1, max_length=500)
    important_dimensions: list[str] = Field(default_factory=list, max_length=100)


class PlannedRelationship(StrictModel):
    first_component_id: str
    second_component_id: str
    relationship: Literal["fixed", "offset", "aligned", "concentric", "coincident"]
    description: str = Field(min_length=1, max_length=500)


class PlannedFeature(StrictModel):
    feature_id: str
    operation: Literal[
        "extrude", "revolve", "loft", "sweep", "union", "subtract", "intersection", "split",
        "transform", "mirror", "fillet", "chamfer", "shell", "hole",
        "linear_pattern", "circular_pattern", "grid_pattern",
    ]
    purpose: str = Field(min_length=1, max_length=500)


class DesignIntentPlan(StrictModel):
    plan_version: Literal["1.0"] = "1.0"
    components: list[PlannedComponent] = Field(default_factory=list, max_length=1000)
    parameters: list[ParameterDefinition] = Field(default_factory=list, max_length=1000)
    functional_relationships: list[PlannedRelationship] = Field(default_factory=list, max_length=1000)
    manufacturing_constraints: list[str] = Field(default_factory=list, max_length=1000)
    proposed_features: list[PlannedFeature] = Field(default_factory=list, max_length=5000)
    uncertainties: list[str] = Field(default_factory=list, max_length=1000)
    questions: list[str] = Field(default_factory=list, max_length=1000)
    unsupported_requests: list[str] = Field(default_factory=list, max_length=1000)
    design_document_candidate: EngineeringDesignDocumentV2 | None = None

    @model_validator(mode="after")
    def validate_references(self) -> "DesignIntentPlan":
        component_ids = [item.component_id for item in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("Design plan component IDs must be unique")
        known = set(component_ids)
        for relationship in self.functional_relationships:
            if relationship.first_component_id not in known or relationship.second_component_id not in known:
                raise ValueError("Design plan relationship references an unknown component")
        feature_ids = [item.feature_id for item in self.proposed_features]
        if len(feature_ids) != len(set(feature_ids)):
            raise ValueError("Design plan feature IDs must be unique")
        return self


class DesignPlanAssessment(StrictModel):
    ready_for_translation: bool
    blockers: list[str]
    candidate_validated: bool


def assess_design_plan(plan: DesignIntentPlan) -> DesignPlanAssessment:
    blockers = []
    if plan.unsupported_requests:
        blockers.append("The request contains unsupported CAD capabilities")
    if plan.questions or plan.uncertainties:
        blockers.append("Required design intent remains ambiguous")
    if plan.design_document_candidate is None:
        blockers.append("No authoritative typed design-document candidate is present")
    return DesignPlanAssessment(
        ready_for_translation=not blockers,
        blockers=blockers,
        candidate_validated=plan.design_document_candidate is not None,
    )
