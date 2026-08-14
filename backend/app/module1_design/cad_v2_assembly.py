"""Deterministic explicit-placement assembly compiler and interference checks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import cadquery as cq

from app.module1_design.cad_v2_schemas import (
    AssemblyInterference,
    AssemblyValidationResult,
    EngineeringDesignDocumentV2,
)


@dataclass(frozen=True)
class CompiledAssembly:
    instance_shapes: dict[str, cq.Workplane]
    validation: AssemblyValidationResult


def _shape_objects(workplane: cq.Workplane) -> list[Any]:
    return [item for item in workplane.vals() if hasattr(item, "Solids")]


def _unit(value: tuple[float, float, float]) -> tuple[float, float, float]:
    magnitude = sum(item * item for item in value) ** 0.5
    if magnitude <= 1e-15:
        raise ValueError("Assembly rotation axis must be non-zero")
    return tuple(item / magnitude for item in value)  # type: ignore[return-value]


def compile_assembly(
    document: EngineeringDesignDocumentV2,
    bodies: dict[str, cq.Workplane],
    *,
    vector: Callable[[Any], tuple[float, float, float]],
    angle: Callable[[Any], float],
    minimum_interference_volume_mm3: float,
    identity_payload: dict[str, Any],
) -> CompiledAssembly | None:
    if not document.component_instances:
        return None
    components = {item.component_id: item for item in document.components}
    instances = {item.instance_id: item for item in document.component_instances}

    def placement_chain(instance_id: str):
        chain = []
        current = instances[instance_id]
        while current is not None:
            chain.append(current.placement)
            current = instances.get(current.parent_instance_id) if current.parent_instance_id else None
        return chain

    instance_shapes: dict[str, cq.Workplane] = {}
    for instance in sorted(document.component_instances, key=lambda item: item.instance_id):
        component = components[instance.component_id]
        shapes = [shape for body_id in component.body_ids for shape in _shape_objects(bodies[body_id])]
        transformed = []
        for source in shapes:
            shape = source
            for placement in placement_chain(instance.instance_id):
                if placement.rotation is not None:
                    origin = vector(placement.rotation.axis_origin)
                    direction = _unit(placement.rotation.axis_direction)
                    end = tuple(a + b for a, b in zip(origin, direction))
                    shape = shape.rotate(origin, end, angle(placement.rotation.angle))
                translation = vector(placement.translation)
                shape = shape.translate(cq.Vector(*translation))
            transformed.append(shape)
        instance_shapes[instance.instance_id] = cq.Workplane("XY").newObject(transformed)

    interferences: list[AssemblyInterference] = []
    if document.detect_interference:
        ordered = sorted(instance_shapes)
        for index, first_id in enumerate(ordered):
            for second_id in ordered[index + 1:]:
                volume = 0.0
                for first in _shape_objects(instance_shapes[first_id]):
                    for second in _shape_objects(instance_shapes[second_id]):
                        try:
                            common = first.intersect(second)
                            volume += sum(float(solid.Volume()) for solid in common.Solids())
                        except Exception as exc:
                            raise ValueError(
                                f"Interference calculation failed for '{first_id}' and '{second_id}'"
                            ) from exc
                if volume > minimum_interference_volume_mm3:
                    interferences.append(AssemblyInterference(
                        first_instance_id=first_id,
                        second_instance_id=second_id,
                        intersection_volume_m3=volume * 1e-9,
                    ))

    from app.v2.execution import digest

    assembly_hash = digest(identity_payload)
    diagnostics = [
        f"Interference detected between '{item.first_instance_id}' and '{item.second_instance_id}'"
        for item in interferences
    ]
    return CompiledAssembly(
        instance_shapes=instance_shapes,
        validation=AssemblyValidationResult(
            valid=not interferences,
            instance_count=len(instance_shapes),
            interferences=interferences,
            diagnostics=diagnostics,
            assembly_hash=assembly_hash,
        ),
    )
