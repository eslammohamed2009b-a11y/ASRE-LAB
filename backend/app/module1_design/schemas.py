"""
Module 1 — Input Protocol
Converts natural language into structured engineering parameters.
"""
from enum import Enum
import math
from typing import Literal, Optional

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
        """Resolve a geometrically consistent, inspectable parameter set.

        For a square pyramid, slope is the face angle measured from the base
        plane, so ``tan(slope) = height / (base_length / 2)``.  Two supplied
        dimensions determine the third.  Supplying all three is accepted only
        when they agree within 0.5 degrees; research inputs must never carry a
        silent geometric contradiction.
        """
        if self.geometry_type == GeometryType.PYRAMID:
            supplied = self.model_fields_set
            has_base = "base_length_m" in supplied and self.base_length_m is not None
            has_height = "height_m" in supplied and self.height_m is not None
            has_slope = "slope_angle_deg" in supplied and self.slope_angle_deg is not None

            if has_base and has_height:
                derived_slope = math.degrees(math.atan2(self.height_m, self.base_length_m / 2.0))
                if has_slope and abs(self.slope_angle_deg - derived_slope) > 0.5:
                    raise ValueError(
                        "Inconsistent pyramid dimensions: base_length_m and height_m imply "
                        f"slope_angle_deg={derived_slope:.3f}"
                    )
                self.slope_angle_deg = round(derived_slope, 6)
            elif has_height and has_slope:
                if self.slope_angle_deg <= 0 or self.slope_angle_deg >= 90:
                    raise ValueError("A pyramid slope must be greater than 0 and less than 90 degrees")
                self.base_length_m = round(
                    2.0 * self.height_m / math.tan(math.radians(self.slope_angle_deg)), 6
                )
            elif has_base and has_slope:
                if self.slope_angle_deg <= 0 or self.slope_angle_deg >= 90:
                    raise ValueError("A pyramid slope must be greater than 0 and less than 90 degrees")
                self.height_m = round(
                    (self.base_length_m / 2.0) * math.tan(math.radians(self.slope_angle_deg)), 6
                )
            elif has_height:
                self.slope_angle_deg = 51.8
                self.base_length_m = round(
                    2.0 * self.height_m / math.tan(math.radians(self.slope_angle_deg)), 6
                )
            elif has_base:
                self.slope_angle_deg = 51.8
                self.height_m = round(
                    (self.base_length_m / 2.0) * math.tan(math.radians(self.slope_angle_deg)), 6
                )
            elif has_slope:
                if self.slope_angle_deg <= 0 or self.slope_angle_deg >= 90:
                    raise ValueError("A pyramid slope must be greater than 0 and less than 90 degrees")
                self.base_length_m = 100.0
                self.height_m = round(
                    50.0 * math.tan(math.radians(self.slope_angle_deg)), 6
                )
            else:
                self.base_length_m = 100.0
                self.slope_angle_deg = 51.8
                self.height_m = round(50.0 * math.tan(math.radians(51.8)), 6)
            if self.material is None:
                self.material = MaterialType.LIMESTONE
            return self

        defaults_by_geometry = {
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


class SweepParameter(BaseModel):
    field: Literal["base_length_m", "height_m", "slope_angle_deg", "wall_thickness_m"]
    method: Literal["linear", "explicit"] = "linear"
    minimum: float | None = Field(default=None, gt=0)
    maximum: float | None = Field(default=None, gt=0)
    count: int | None = Field(default=None, ge=2, le=100)
    values: list[float] = Field(default_factory=list, min_length=0, max_length=100)

    @model_validator(mode="after")
    def validate_rule(self) -> "SweepParameter":
        if self.method == "linear":
            if self.minimum is None or self.maximum is None or self.count is None:
                raise ValueError("A linear sweep requires minimum, maximum, and count")
            if self.maximum <= self.minimum:
                raise ValueError("A linear sweep maximum must be greater than minimum")
        elif not self.values:
            raise ValueError("An explicit sweep requires at least one value")
        if any(value <= 0 for value in self.values):
            raise ValueError("Explicit sweep values must be greater than zero")
        return self


class DesignSpaceRequest(BaseModel):
    base_params: DesignParameters
    parameters: list[SweepParameter] = Field(min_length=1, max_length=2)
    seed: int = Field(default=0, ge=0, le=2_147_483_647)

    @model_validator(mode="after")
    def unique_parameters(self) -> "DesignSpaceRequest":
        names = [parameter.field for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("Design-space parameter fields must be unique")
        return self


class DesignSpacePreviewResponse(BaseModel):
    method: str
    seed: int
    variant_count: int
    variants: list[dict]


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
    experiment_id: str
    params: dict
    # Object keys inside the FileStorage abstraction (app.core.storage), NOT
    # raw filesystem paths - the client downloads the actual bytes via the
    # authenticated `/api/design/export/{design_id}` endpoint.
    stl_object_key: str
    step_object_key: str


class BatchGenerateRequest(BaseModel):
    base_params: DesignParameters
    variation_count: int = Field(10, ge=1, le=500)
    vary_fields: list[str] = Field(default_factory=lambda: ["slope_angle_deg", "height_m"])
    variation_range_pct: float = Field(0.2, gt=0, le=1.0)
    experiment_id: str | None = None
    sweep_parameters: list[SweepParameter] = Field(default_factory=list, max_length=2)
    seed: int = Field(default=0, ge=0, le=2_147_483_647)

    @field_validator("vary_fields")
    @classmethod
    def validate_vary_fields(cls, value: list[str]) -> list[str]:
        allowed = {"base_length_m", "height_m", "slope_angle_deg", "wall_thickness_m"}
        invalid = [field_name for field_name in value if field_name not in allowed]
        if invalid:
            raise ValueError(f"Unsupported vary_fields: {invalid}. Allowed: {sorted(allowed)}")
        return value

    @model_validator(mode="after")
    def validate_sweep_parameters(self) -> "BatchGenerateRequest":
        names = [parameter.field for parameter in self.sweep_parameters]
        if len(names) != len(set(names)):
            raise ValueError("Sweep parameter fields must be unique")
        return self


class BatchGenerateResponse(BaseModel):
    job_id: str
    experiment_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    experiment_id: str
    status: str
    requested_count: int
    completed_count: int
    failed_count: int
    progress_percent: int
    error_code: str | None = None
    safe_error_message: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class JobDesignFileSummary(BaseModel):
    design_file_id: str
    file_format: str
    object_key: str
    file_size_bytes: int | None
    checksum_sha256: str | None
    media_type: str


class JobDesignSummary(BaseModel):
    design_model_id: str
    variation_index: int
    geometry_family: str
    parameters: dict
    generation_status: str
    files: list[JobDesignFileSummary]


class JobResultsResponse(BaseModel):
    job_id: str
    status: str
    designs: list[JobDesignSummary]
