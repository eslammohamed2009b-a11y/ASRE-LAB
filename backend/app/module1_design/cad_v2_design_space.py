"""Generic deterministic design-variable sweeps and chunked V2 execution."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from app.module1_design.cad_v2_compiler import (
    ANGLE_TO_RADIANS,
    LENGTH_TO_METRES,
    CADCompilationError,
    FeatureCompilationCache,
    compile_design,
    design_hash,
    export_compiled_design,
)
from app.module1_design.cad_v2_schemas import (
    AngleUnit,
    EngineeringDesignDocumentV2,
    LengthUnit,
    ParameterDefinition,
    ParameterType,
    Quantity,
    StrictModel,
)


SweepValue = Quantity | float | int | bool | str
MAX_V2_VARIANTS = 10_000
MAX_FEATURE_EXECUTIONS = 250_000


class V2SweepRule(StrictModel):
    parameter_name: str
    method: Literal["explicit", "linear", "integer_range", "boolean", "categorical"]
    values: list[SweepValue] = Field(default_factory=list, max_length=10_000)
    start: Quantity | float | int | None = None
    stop: Quantity | float | int | None = None
    count: int | None = Field(default=None, ge=2, le=10_000)
    step: int = Field(default=1, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_rule(self) -> "V2SweepRule":
        if self.method in {"explicit", "categorical"} and not self.values:
            raise ValueError(f"{self.method} sweep requires values")
        if self.method == "linear" and (self.start is None or self.stop is None or self.count is None):
            raise ValueError("Linear sweep requires start, stop, and count")
        if self.method == "integer_range" and (self.start is None or self.stop is None):
            raise ValueError("Integer range requires start and stop")
        if self.method == "boolean" and self.values:
            raise ValueError("Boolean sweep uses the canonical [False, True] axis")
        return self


class V2DesignSpaceRequest(StrictModel):
    document: EngineeringDesignDocumentV2
    sweeps: list[V2SweepRule] = Field(min_length=1, max_length=20)
    chunk_size: int = Field(default=25, ge=1, le=250)
    artifact_mode: Literal["deferred", "preview", "full", "selected"] = "deferred"
    selected_artifact_indices: list[int] = Field(default_factory=list, max_length=1000)
    continue_on_error: bool = True

    @model_validator(mode="after")
    def unique_sweeps(self) -> "V2DesignSpaceRequest":
        names = [item.parameter_name for item in self.sweeps]
        if len(names) != len(set(names)):
            raise ValueError("Design-space parameter names must be unique")
        if self.artifact_mode == "selected" and not self.selected_artifact_indices:
            raise ValueError("Selected artifact mode requires at least one variant index")
        return self


class V2DesignVariant(StrictModel):
    variant_index: int
    variant_id: str
    parameter_values: dict[str, Any]
    document: EngineeringDesignDocumentV2


class V2DesignSpacePreview(StrictModel):
    variant_count: int
    chunk_size: int
    variant_ids: list[str]
    parameter_values: list[dict[str, Any]]


class V2VariantExecution(StrictModel):
    variant_index: int
    variant_id: str
    status: Literal["completed", "failed", "cancelled"]
    design_hash: str | None = None
    geometry_fingerprint: str | None = None
    semantic_region_status: dict[str, str] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    safe_error_message: str | None = None


class V2DesignSpaceResult(StrictModel):
    requested_count: int
    completed_count: int
    failed_count: int
    cancelled_count: int
    chunk_size: int
    artifact_mode: str
    variants: list[V2VariantExecution]


def _dimension_value(value: SweepValue, parameter: ParameterDefinition) -> float:
    if not isinstance(value, Quantity):
        raise ValueError(f"Dimensional parameter '{parameter.name}' requires an explicit-unit quantity")
    if parameter.parameter_type == ParameterType.LENGTH:
        if not isinstance(value.unit, LengthUnit) or not isinstance(parameter.unit, LengthUnit):
            raise ValueError(f"Parameter '{parameter.name}' requires a length unit")
        canonical = value.value * LENGTH_TO_METRES[value.unit]
        return canonical / LENGTH_TO_METRES[parameter.unit]
    if parameter.parameter_type == ParameterType.ANGLE:
        if not isinstance(value.unit, AngleUnit) or not isinstance(parameter.unit, AngleUnit):
            raise ValueError(f"Parameter '{parameter.name}' requires an angle unit")
        canonical = value.value * ANGLE_TO_RADIANS[value.unit]
        return canonical / ANGLE_TO_RADIANS[parameter.unit]
    raise ValueError("Internal dimension mismatch")


def _coerce(value: SweepValue, parameter: ParameterDefinition) -> Any:
    if parameter.parameter_type in {ParameterType.LENGTH, ParameterType.ANGLE}:
        result: Any = _dimension_value(value, parameter)
    elif parameter.parameter_type == ParameterType.INTEGER:
        if type(value) is not int:
            raise ValueError(f"Parameter '{parameter.name}' requires integer values")
        result = value
    elif parameter.parameter_type == ParameterType.BOOLEAN:
        if type(value) is not bool:
            raise ValueError(f"Parameter '{parameter.name}' requires boolean values")
        result = value
    elif parameter.parameter_type == ParameterType.CATEGORICAL:
        if not isinstance(value, str) or value not in parameter.choices:
            raise ValueError(f"Parameter '{parameter.name}' value is outside its categories")
        result = value
    else:
        if isinstance(value, (Quantity, bool, str)):
            raise ValueError(f"Parameter '{parameter.name}' requires scalar values")
        result = float(value)
    if isinstance(result, (int, float)) and not isinstance(result, bool):
        if parameter.minimum is not None and result < parameter.minimum:
            raise ValueError(f"Parameter '{parameter.name}' variant is below its minimum")
        if parameter.maximum is not None and result > parameter.maximum:
            raise ValueError(f"Parameter '{parameter.name}' variant is above its maximum")
    return result


def _axis(rule: V2SweepRule, parameter: ParameterDefinition) -> list[Any]:
    if not parameter.design_variable:
        raise ValueError(f"Parameter '{parameter.name}' is not declared as a design variable")
    if rule.method == "boolean":
        if parameter.parameter_type != ParameterType.BOOLEAN:
            raise ValueError("Boolean sweep requires a boolean parameter")
        raw: list[SweepValue] = [False, True]
    elif rule.method in {"explicit", "categorical"}:
        raw = rule.values
    elif rule.method == "integer_range":
        if parameter.parameter_type != ParameterType.INTEGER or type(rule.start) is not int or type(rule.stop) is not int:
            raise ValueError("Integer range requires an integer parameter and integer endpoints")
        if rule.stop < rule.start:
            raise ValueError("Integer range stop must be at least start")
        raw = list(range(rule.start, rule.stop + 1, rule.step))
    else:
        assert rule.start is not None and rule.stop is not None and rule.count is not None
        start = _coerce(rule.start, parameter)
        stop = _coerce(rule.stop, parameter)
        if not isinstance(start, (int, float)) or isinstance(start, bool) or stop <= start:
            raise ValueError("Linear sweep stop must be greater than start")
        raw = [start + (stop - start) * index / (rule.count - 1) for index in range(rule.count)]
        # Values are already in the parameter's unit after endpoint coercion.
        return [
            _coerce(
                Quantity(value=item, unit=parameter.unit) if parameter.unit is not None else item,
                parameter,
            )
            for item in raw
        ]
    return [_coerce(item, parameter) for item in raw]


def calculate_variant_count(request: V2DesignSpaceRequest) -> int:
    parameters = {item.name: item for item in request.document.parameters}
    count = 1
    for rule in request.sweeps:
        parameter = parameters.get(rule.parameter_name)
        if parameter is None:
            raise ValueError(f"Unknown design variable '{rule.parameter_name}'")
        count *= len(_axis(rule, parameter))
    complexity_limit = max(1, MAX_FEATURE_EXECUTIONS // len(request.document.features))
    maximum = min(MAX_V2_VARIANTS, complexity_limit)
    if count > maximum:
        raise ValueError(
            f"Design space requires {count} variants; resource policy permits {maximum} for this feature graph"
        )
    if any(index < 0 or index >= count for index in request.selected_artifact_indices):
        raise ValueError("Selected artifact index is outside the design space")
    return count


def build_design_variants(request: V2DesignSpaceRequest) -> list[V2DesignVariant]:
    from itertools import product
    from app.v2.execution import digest

    parameters = {item.name: item for item in request.document.parameters}
    axes = [(_axis(rule, parameters[rule.parameter_name])) for rule in request.sweeps]
    calculate_variant_count(request)
    variants: list[V2DesignVariant] = []
    for index, coordinates in enumerate(product(*axes)):
        payload = request.document.model_dump(mode="json")
        changed = {rule.parameter_name: value for rule, value in zip(request.sweeps, coordinates)}
        for parameter in payload["parameters"]:
            if parameter["name"] in changed:
                parameter["value"] = changed[parameter["name"]]
        document = EngineeringDesignDocumentV2.model_validate(payload)
        variant_id = digest({
            "schema": "cad-v2-variant-1.0",
            "base_design_hash": design_hash(request.document),
            "variant_index": index,
            "parameter_values": changed,
        })
        variants.append(V2DesignVariant(
            variant_index=index,
            variant_id=variant_id,
            parameter_values=changed,
            document=document,
        ))
    return variants


def variant_chunks(request: V2DesignSpaceRequest) -> Iterator[list[V2DesignVariant]]:
    variants = build_design_variants(request)
    for start in range(0, len(variants), request.chunk_size):
        yield variants[start:start + request.chunk_size]


def execute_design_space(
    request: V2DesignSpaceRequest,
    *,
    export_directory: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> V2DesignSpaceResult:
    cache = FeatureCompilationCache()
    results: list[V2VariantExecution] = []
    selected = set(request.selected_artifact_indices)
    for chunk in variant_chunks(request):
        for variant in chunk:
            if cancelled and cancelled():
                results.append(V2VariantExecution(
                    variant_index=variant.variant_index,
                    variant_id=variant.variant_id,
                    status="cancelled",
                ))
                continue
            try:
                compiled = compile_design(variant.document, cache=cache)
                formats: tuple[Literal["step", "stl"], ...] | None = None
                if request.artifact_mode == "preview":
                    formats = ("stl",)
                elif request.artifact_mode == "full":
                    formats = ("step", "stl")
                elif request.artifact_mode == "selected" and variant.variant_index in selected:
                    formats = ("step", "stl")
                artifacts = export_compiled_design(compiled, export_directory, formats=formats) if formats else []
                results.append(V2VariantExecution(
                    variant_index=variant.variant_index,
                    variant_id=variant.variant_id,
                    status="completed",
                    design_hash=compiled.design_hash,
                    geometry_fingerprint=compiled.geometry_fingerprint,
                    semantic_region_status={item["tag"]: item["status"] for item in compiled.semantic_regions},
                    artifact_ids=[item.metadata.artifact_id for item in artifacts],
                ))
            except (CADCompilationError, ValueError) as exc:
                results.append(V2VariantExecution(
                    variant_index=variant.variant_index,
                    variant_id=variant.variant_id,
                    status="failed",
                    error_code=getattr(exc, "code", "INVALID_VARIANT"),
                    safe_error_message=str(exc),
                ))
                if not request.continue_on_error:
                    return V2DesignSpaceResult(
                        requested_count=calculate_variant_count(request),
                        completed_count=sum(item.status == "completed" for item in results),
                        failed_count=sum(item.status == "failed" for item in results),
                        cancelled_count=sum(item.status == "cancelled" for item in results),
                        chunk_size=request.chunk_size,
                        artifact_mode=request.artifact_mode,
                        variants=results,
                    )
    return V2DesignSpaceResult(
        requested_count=calculate_variant_count(request),
        completed_count=sum(item.status == "completed" for item in results),
        failed_count=sum(item.status == "failed" for item in results),
        cancelled_count=sum(item.status == "cancelled" for item in results),
        chunk_size=request.chunk_size,
        artifact_mode=request.artifact_mode,
        variants=results,
    )
