"""Bounded CadQuery-backed sketch constraint solving for CAD V2."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import cadquery as cq

from app.module1_design.cad_v2_schemas import (
    AngleConstraint,
    ArcEntity,
    BinaryGeometryConstraint,
    CircleEntity,
    CoincidentConstraint,
    DistanceConstraint,
    EntityDimensionConstraint,
    FixedConstraint,
    LineEntity,
    OrientationConstraint,
    ParameterReference,
    SketchDefinition,
    SketchSolveResult,
    SketchSolveState,
)


@dataclass(frozen=True)
class SolvedEntity:
    geometry_type: str
    data: tuple[Any, ...]


@dataclass(frozen=True)
class ConstraintSolveOutput:
    result: SketchSolveResult
    entities: dict[str, SolvedEntity]


def _original_length(edge: cq.Edge) -> float:
    return float(edge.Length())


def solve_sketch_constraints(
    sketch: SketchDefinition,
    *,
    point: Callable[[Any], tuple[float, float]],
    length: Callable[[Any], float],
    angle: Callable[[Any], float],
    residual_tolerance: float,
) -> ConstraintSolveOutput:
    """Solve supported line/arc/circle constraints with CadQuery's NLopt solver.

    Explicit-coordinate sketches are already fully defined. Constraint-driven
    sketches are only reported fully constrained when every variable entity is
    fixed; CadQuery exposes solve residuals but not a trustworthy Jacobian rank,
    so other successful systems remain truthfully UNDERCONSTRAINED.
    """
    if not sketch.constraints:
        state = (
            SketchSolveState.FULLY_CONSTRAINED
            if sketch.constraint_mode == "explicit_coordinates"
            else SketchSolveState.UNDERCONSTRAINED
        )
        return ConstraintSolveOutput(
            SketchSolveResult(
                sketch_id=sketch.sketch_id,
                state=state,
                residual=0.0,
                degrees_of_freedom=0 if state == SketchSolveState.FULLY_CONSTRAINED else sum(
                    4 if isinstance(item, LineEntity) else 5 if isinstance(item, (ArcEntity, CircleEntity)) else 0
                    for item in sketch.entities if not item.construction
                ),
            ),
            {},
        )

    # Detect an important class of deterministic contradictions before asking
    # the numerical solver.  A single entity cannot have two unequal values
    # for the same authored dimension.  Keeping this separate from kernel
    # failure lets callers distinguish OVERCONSTRAINED from INVALID.
    dimensions: dict[tuple[str, str], float] = {}
    for constraint in sketch.constraints:
        if not isinstance(constraint, EntityDimensionConstraint):
            continue
        kind = "radius" if constraint.constraint_type == "diameter" else constraint.constraint_type
        target = length(constraint.value)
        if constraint.constraint_type == "diameter":
            target /= 2.0
        key = (constraint.entity_id, kind)
        if key in dimensions and not math.isclose(dimensions[key], target, rel_tol=1e-12, abs_tol=residual_tolerance):
            return ConstraintSolveOutput(
                SketchSolveResult(
                    sketch_id=sketch.sketch_id,
                    state=SketchSolveState.OVERCONSTRAINED,
                    residual=abs(dimensions[key] - target),
                    degrees_of_freedom=0,
                    diagnostics=[f"Entity '{constraint.entity_id}' has conflicting {kind} constraints"],
                ),
                {},
            )
        dimensions[key] = target

    solver = cq.Sketch()
    supported: dict[str, cq.Edge] = {}
    authored = {item.entity_id: item for item in sketch.entities}
    unsupported_ids: set[str] = set()
    for entity in sketch.entities:
        if entity.construction:
            continue
        edge: cq.Edge | None = None
        if isinstance(entity, LineEntity):
            edge = cq.Edge.makeLine(point(entity.start), point(entity.end))
        elif isinstance(entity, ArcEntity):
            edge = cq.Edge.makeThreePointArc(point(entity.start), point(entity.midpoint), point(entity.end))
        elif isinstance(entity, CircleEntity):
            center = point(entity.center) if entity.center else (0.0, 0.0)
            # CadQuery 2.8 cannot reconstruct a mathematically closed circle
            # after constraint solving (its three-point arc reconstruction has
            # coincident endpoints).  A kernel-equivalent near-complete arc is
            # used only inside the solver; the authored entity is rebuilt as a
            # full circle below.
            edge = cq.Edge.makeCircle(
                length(entity.radius), cq.Vector(*center, 0), angle1=0.0, angle2=359.999
            )
        else:
            unsupported_ids.add(entity.entity_id)
        if edge is not None:
            solver = solver.edge(edge, tag=entity.entity_id)
            supported[entity.entity_id] = edge

    diagnostics: list[str] = []
    fixed_ids: set[str] = set()
    equation_count = 0
    try:
        for constraint in sketch.constraints:
            referenced = {
                getattr(constraint, name) for name in ("entity_id", "first_entity_id", "second_entity_id")
                if hasattr(constraint, name)
            }
            if referenced & unsupported_ids:
                if isinstance(constraint, FixedConstraint):
                    fixed_ids.update(referenced)
                    continue
                diagnostics.append(
                    f"Constraint '{constraint.constraint_id}' targets an entity type not supported by the bounded solver"
                )
                continue
            if isinstance(constraint, FixedConstraint):
                solver = solver.constrain(constraint.entity_id, "Fixed", None)
                fixed_ids.add(constraint.entity_id)
                equation_count += 5
            elif isinstance(constraint, CoincidentConstraint):
                solver = solver.constrain(
                    constraint.first_entity_id, constraint.second_entity_id, "Coincident", None
                )
                equation_count += 2
            elif isinstance(constraint, OrientationConstraint):
                direction = (1.0, 0.0) if constraint.constraint_type == "horizontal" else (0.0, 1.0)
                solver = solver.constrain(constraint.entity_id, "Orientation", direction)
                equation_count += 1
            elif isinstance(constraint, BinaryGeometryConstraint):
                if constraint.constraint_type in {"parallel", "perpendicular"}:
                    target_angle = 0.0 if constraint.constraint_type == "parallel" else 90.0
                    solver = solver.constrain(
                        constraint.first_entity_id, constraint.second_entity_id, "Angle", target_angle
                    )
                    equation_count += 1
                else:
                    first, second = supported[constraint.first_entity_id], supported[constraint.second_entity_id]
                    if first.geomType() == "LINE" and second.geomType() == "LINE":
                        solver = solver.constrain(constraint.second_entity_id, "Length", _original_length(first))
                    elif first.geomType() == "CIRCLE" and second.geomType() == "CIRCLE":
                        solver = solver.constrain(constraint.second_entity_id, "Radius", float(first.radius()))
                    else:
                        diagnostics.append(
                            f"Equal constraint '{constraint.constraint_id}' requires matching geometry types"
                        )
                    equation_count += 1
            elif isinstance(constraint, DistanceConstraint):
                if constraint.constraint_type != "distance":
                    diagnostics.append(
                        f"Constraint '{constraint.constraint_id}' is preserved but horizontal/vertical distance solving is unsupported"
                    )
                    continue
                first_position = constraint.first_position
                second_position = constraint.second_position
                if first_position is None and supported[constraint.first_entity_id].geomType() == "LINE":
                    first_position = 0.5
                if second_position is None and supported[constraint.second_entity_id].geomType() == "LINE":
                    second_position = 0.5
                solver = solver.constrain(
                    constraint.first_entity_id,
                    constraint.second_entity_id,
                    "Distance",
                    (first_position, second_position, length(constraint.value)),
                )
                equation_count += 1
            elif isinstance(constraint, EntityDimensionConstraint):
                target = length(constraint.value)
                if constraint.constraint_type == "diameter":
                    target /= 2.0
                kind = "Length" if constraint.constraint_type == "length" else "Radius"
                solver = solver.constrain(constraint.entity_id, kind, target)
                equation_count += 1
            elif isinstance(constraint, AngleConstraint):
                if constraint.constraint_type == "tangent":
                    diagnostics.append(
                        f"Constraint '{constraint.constraint_id}' tangent solving is not supported by this kernel adapter"
                    )
                    continue
                if constraint.value is None:
                    diagnostics.append(f"Constraint '{constraint.constraint_id}' requires an angle")
                    continue
                solver = solver.constrain(
                    constraint.first_entity_id,
                    constraint.second_entity_id,
                    "Angle",
                    angle(constraint.value),
                )
                equation_count += 1
        if diagnostics:
            return ConstraintSolveOutput(
                SketchSolveResult(
                    sketch_id=sketch.sketch_id,
                    state=SketchSolveState.INVALID,
                    residual=math.inf,
                    degrees_of_freedom=0,
                    diagnostics=diagnostics,
                ),
                {},
            )
        if not supported:
            active_ids = {item.entity_id for item in sketch.entities if not item.construction}
            fully = sketch.constraint_mode == "explicit_coordinates" or active_ids <= fixed_ids
            return ConstraintSolveOutput(
                SketchSolveResult(
                    sketch_id=sketch.sketch_id,
                    state=(
                        SketchSolveState.FULLY_CONSTRAINED
                        if fully else SketchSolveState.UNDERCONSTRAINED
                    ),
                    residual=0.0,
                    degrees_of_freedom=0 if fully else max(1, len(active_ids)),
                ),
                {},
            )
        solver.solve()
    except Exception:
        return ConstraintSolveOutput(
            SketchSolveResult(
                sketch_id=sketch.sketch_id,
                state=SketchSolveState.INVALID,
                residual=math.inf,
                degrees_of_freedom=0,
                diagnostics=["CadQuery could not solve the bounded constraint system"],
            ),
            {},
        )

    status = solver._solve_status or {}
    residual = float(status.get("cost", math.inf))
    active_ids = {item.entity_id for item in sketch.entities if not item.construction}
    if residual > residual_tolerance:
        state = SketchSolveState.OVERCONSTRAINED
        diagnostics.append("Constraint residual exceeds the modeling tolerance")
    elif sketch.constraint_mode == "explicit_coordinates" or active_ids <= fixed_ids:
        state = SketchSolveState.FULLY_CONSTRAINED
    else:
        state = SketchSolveState.UNDERCONSTRAINED
        diagnostics.append("The kernel solved the constraints, but independent degrees of freedom remain unproven")
    raw_dof = sum(
        4 if isinstance(item, LineEntity) else 5 if isinstance(item, (ArcEntity, CircleEntity)) else 0
        for item in sketch.entities if not item.construction
    )
    dof = 0 if state == SketchSolveState.FULLY_CONSTRAINED else max(1, raw_dof - equation_count)
    solved: dict[str, SolvedEntity] = {}
    for entity_id in supported:
        edge = solver._tags[entity_id][0]
        if edge.geomType() == "LINE":
            start, end = edge.startPoint(), edge.endPoint()
            solved[entity_id] = SolvedEntity("line", ((start.x, start.y), (end.x, end.y)))
        elif edge.geomType() == "CIRCLE" and isinstance(authored[entity_id], CircleEntity):
            center = edge.arcCenter()
            solved[entity_id] = SolvedEntity("circle", ((center.x, center.y), float(edge.radius())))
        elif edge.geomType() == "CIRCLE":
            center = edge.arcCenter()
            start, midpoint, end = edge.startPoint(), edge.positionAt(0.5), edge.endPoint()
            if start.distanceTo(end) <= 1e-8:
                solved[entity_id] = SolvedEntity("circle", ((center.x, center.y), float(edge.radius())))
            else:
                solved[entity_id] = SolvedEntity(
                    "arc", ((start.x, start.y), (midpoint.x, midpoint.y), (end.x, end.y))
                )
    return ConstraintSolveOutput(
        SketchSolveResult(
            sketch_id=sketch.sketch_id,
            state=state,
            residual=residual,
            degrees_of_freedom=dof,
            diagnostics=diagnostics,
        ),
        solved,
    )
