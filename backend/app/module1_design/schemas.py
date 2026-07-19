"""
Module 1 — Input Protocol
Converts natural language into structured engineering parameters.
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class MaterialType(str, Enum):
    LIMESTONE = "limestone"
    GRANITE = "granite"
    CONCRETE = "concrete"
    STEEL = "steel"
    ALUMINUM = "aluminum"


class GeometryType(str, Enum):
    PYRAMID = "pyramid"
    BRIDGE = "bridge"
    TOWER = "tower"
    ARCH = "arch"
    DOME = "dome"


class DesignParameters(BaseModel):
    """
    Structured JSON object produced by the LLM function-calling step.
    Any field left blank by the user is filled from the internal
    knowledge base defaults in `resolve_defaults()`.
    """

    geometry_type: GeometryType
    base_length_m: Optional[float] = Field(None, gt=0)
    height_m: Optional[float] = Field(None, gt=0)
    slope_angle_deg: Optional[float] = Field(None, ge=0, le=90)
    material: Optional[MaterialType] = None
    wall_thickness_m: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def resolve_defaults(self) -> "DesignParameters":
        """Internal-knowledge-base fallback for missing parameters."""
        defaults_by_geometry = {
            GeometryType.PYRAMID: {
                "base_length_m": self.height_m * 1.27 if self.height_m else 100.0,
                "slope_angle_deg": 51.8,
                "material": MaterialType.LIMESTONE,
            },
            GeometryType.BRIDGE: {
                "base_length_m": 200.0,
                "slope_angle_deg": 0.0,
                "material": MaterialType.STEEL,
            },
            GeometryType.TOWER: {
                "base_length_m": 20.0,
                "slope_angle_deg": 0.0,
                "material": MaterialType.CONCRETE,
                "wall_thickness_m": 0.5,
            },
        }
        fallback = defaults_by_geometry.get(self.geometry_type, {})
        for field_name, default_value in fallback.items():
            if getattr(self, field_name) is None:
                setattr(self, field_name, default_value)
        return self


class DesignVariationRequest(BaseModel):
    """A batch request to generate N variations around a base design."""

    base_params: DesignParameters
    variation_count: int = Field(100, ge=1, le=500)
    vary_fields: list[str] = Field(default_factory=lambda: ["slope_angle_deg", "height_m"])
    variation_range_pct: float = Field(0.2, gt=0, le=1.0)

    @field_validator("vary_fields")
    @classmethod
    def validate_vary_fields(cls, value: list[str]) -> list[str]:
        allowed = {"base_length_m", "height_m", "slope_angle_deg", "wall_thickness_m"}
        invalid = [field_name for field_name in value if field_name not in allowed]
        if invalid:
            raise ValueError(f"Unsupported vary_fields: {invalid}. Allowed: {sorted(allowed)}")
        return value


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=3)


class ParseResponse(BaseModel):
    params: DesignParameters


class GenerateSingleResponse(BaseModel):
    design_id: str
    params: dict
    stl_path: str
    step_path: str
