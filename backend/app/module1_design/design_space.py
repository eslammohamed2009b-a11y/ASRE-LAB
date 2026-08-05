"""Deterministic, bounded design-space construction.

The returned list is the reproducibility contract used by preview, CAD
generation, and comparative execution.  No global random state is involved.
"""
from __future__ import annotations

from itertools import product

from app.module1_design.schemas import DesignParameters, DesignSpaceRequest, SweepParameter


MAX_DESIGN_SPACE_VARIANTS = 500


def _values(rule: SweepParameter) -> list[float]:
    if rule.method == "explicit":
        return [float(value) for value in rule.values]
    assert rule.minimum is not None and rule.maximum is not None and rule.count is not None
    if rule.count == 2:
        return [float(rule.minimum), float(rule.maximum)]
    step = (rule.maximum - rule.minimum) / (rule.count - 1)
    return [round(rule.minimum + index * step, 12) for index in range(rule.count)]


def _resolve_variant(base: DesignParameters, changes: dict[str, float]) -> DesignParameters:
    values = base.model_dump(mode="json")
    values.update(changes)
    changed = set(changes)

    # A pyramid has two independent geometric dimensions.  Remove the one
    # dependent value before validation so it is explicitly re-derived.
    if base.geometry_type.value == "pyramid":
        if {"base_length_m", "height_m"}.issubset(changed):
            values.pop("slope_angle_deg", None)
        elif {"base_length_m", "slope_angle_deg"}.issubset(changed):
            values.pop("height_m", None)
        elif {"height_m", "slope_angle_deg"}.issubset(changed):
            values.pop("base_length_m", None)
        elif "height_m" in changed or "base_length_m" in changed:
            values.pop("slope_angle_deg", None)
        elif "slope_angle_deg" in changed:
            values.pop("height_m", None)
    return DesignParameters(**values)


def build_design_space(request: DesignSpaceRequest) -> list[dict]:
    axes = [_values(rule) for rule in request.parameters]
    variant_count = 1
    for axis in axes:
        variant_count *= len(axis)
    if variant_count > MAX_DESIGN_SPACE_VARIANTS:
        raise ValueError(
            f"Design space contains {variant_count} variants; maximum is {MAX_DESIGN_SPACE_VARIANTS}"
        )

    variants: list[dict] = []
    for index, coordinates in enumerate(product(*axes)):
        changes = {
            rule.field: value for rule, value in zip(request.parameters, coordinates)
        }
        params = _resolve_variant(request.base_params, changes)
        variants.append({
            "variation_index": index,
            "varied_values": changes,
            "parameters": params.model_dump(mode="json"),
        })
    return variants
