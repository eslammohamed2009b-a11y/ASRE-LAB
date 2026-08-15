"""Authoritative BRep-to-tetrahedral-mesh foundation.

The preferred production adapter is Gmsh/OpenCascade.  The current pinned
runtime does not ship Gmsh, so this module provides a bounded deterministic
fallback: surface/interior points are obtained from the *compiled* OCC solid,
SciPy computes tetrahedral connectivity, and every retained cell is tested
against that same solid.  STL is never imported or used as volume authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from itertools import combinations, permutations
from pathlib import Path
from typing import Any

import cadquery as cq
import numpy as np
import scipy
from scipy.spatial import Delaunay, QhullError

from app.module1_design.cad_v2_compiler import (
    CompiledDesign,
    resolve_length_mm,
    resolve_parameters,
)
from app.module1_design.cad_v2_semantics import (
    SemanticTopologyError,
    resolve_semantic_geometry,
)
from app.module2_simulation.geometry_physics_schemas import (
    DomainMeshMapping,
    GeometryPreparationResult,
    GeometryPreparationState,
    MeshArtifact,
    MeshQualityMetrics,
    MeshSpecification,
    PhysicsDomain,
    SemanticMeshMapping,
    ValidationState,
)


MESHER_ID = "asre-occ-scipy-tet"
MESHER_VERSION = f"1.0+scipy-{scipy.__version__}"
MM_TO_M = 1e-3
_FALLBACK_REFINEMENT_FACTORS = (1.0, 0.75, 0.5, 0.375)
_COVERAGE_TOLERANCE = 0.15


class MeshingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GeneratedMesh:
    metadata: MeshArtifact
    nodes_m: tuple[tuple[float, float, float], ...]
    tetrahedra: tuple[tuple[int, int, int, int], ...]
    boundary_facets: tuple[tuple[int, int, int], ...]


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _length_mm(quantity) -> float:
    multipliers = {"m": 1000.0, "mm": 1.0, "cm": 10.0, "um": 1e-3, "in": 25.4}
    try:
        result = float(quantity.value) * multipliers[quantity.unit.value]
    except KeyError as exc:
        raise MeshingError("INVALID_MESH_UNIT", "Mesh sizes require a supported length unit") from exc
    if not math.isfinite(result) or result <= 0:
        raise MeshingError("INVALID_MESH_SIZE", "Mesh sizes must be finite and positive")
    return result


def _solids(workplane: cq.Workplane) -> list[Any]:
    return list(workplane.solids().vals())


def prepare_geometry(compiled: CompiledDesign, domains: list[PhysicsDomain]) -> GeometryPreparationResult:
    diagnostics: list[str] = []
    domain_ids = [item.domain_id for item in domains]
    if len(domain_ids) != len(set(domain_ids)):
        diagnostics.append("Physics domain identifiers must be unique")
    body_ids = [item.source_body_id for item in domains]
    selected: list[str] = []
    solid_count = 0
    for body_id in body_ids:
        if body_id in selected:
            diagnostics.append(f"Body '{body_id}' is assigned to more than one domain")
            continue
        selected.append(body_id)
        body = compiled.bodies.get(body_id)
        if body is None:
            diagnostics.append(f"Domain references unknown compiled body '{body_id}'")
            continue
        solids = _solids(body)
        if not solids:
            diagnostics.append(f"Body '{body_id}' is not a closed solid suitable for a 3D volume mesh")
            continue
        solid_count += len(solids)
        for solid in solids:
            try:
                box = solid.BoundingBox()
                bounds = (box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax)
                if not solid.isValid() or not all(math.isfinite(value) for value in bounds):
                    diagnostics.append(f"Body '{body_id}' contains an invalid or non-finite BRep solid")
                if float(solid.Volume()) <= 0:
                    diagnostics.append(f"Body '{body_id}' has non-positive solid volume")
                if max(box.xlen, box.ylen, box.zlen) > 1e7 or min(box.xlen, box.ylen, box.zlen) <= 1e-9:
                    diagnostics.append(f"Body '{body_id}' is outside the bounded meshing scale policy")
            except Exception:
                diagnostics.append(f"Body '{body_id}' failed closed-solid geometry inspection")
    status = GeometryPreparationState.INVALID_FOR_MESHING if diagnostics else GeometryPreparationState.READY
    return GeometryPreparationResult(
        status=status,
        design_hash=compiled.design_hash,
        geometry_fingerprint=compiled.geometry_fingerprint,
        selected_body_ids=selected,
        solid_count=solid_count,
        diagnostics=diagnostics,
    )


def _point_tuple(vector: Any) -> tuple[float, float, float]:
    return (float(vector.x), float(vector.y), float(vector.z))


def _deduplicate(points: list[tuple[float, float, float]], tolerance_mm: float) -> np.ndarray:
    quantum = max(tolerance_mm, 1e-12)
    unique = sorted({
        tuple(round(value / quantum) * quantum for value in point) for point in points
    })
    return np.asarray(unique, dtype=np.float64)


def _inside(solid: Any, point: np.ndarray, tolerance_mm: float) -> bool:
    vector = cq.Vector(float(point[0]), float(point[1]), float(point[2]))
    try:
        return bool(solid.isInside(vector, tolerance_mm, True))
    except TypeError:
        return bool(solid.isInside(vector, tolerance_mm))


def _candidate_points(solid: Any, size_mm: float, maximum_nodes: int) -> np.ndarray:
    # OCC tessellation supplies boundary points directly from the authoritative
    # BRep.  These are point samples only; no STL or triangulated surrogate is
    # ever re-imported as the volume definition.
    surface, _ = solid.tessellate(max(size_mm * 0.20, 1e-5), 0.12)
    points = [_point_tuple(item) for item in surface]
    box = solid.BoundingBox()
    counts = [max(2, int(math.ceil(length / size_mm)) + 1) for length in (box.xlen, box.ylen, box.zlen)]
    projected = counts[0] * counts[1] * counts[2] + len(points)
    if projected > maximum_nodes:
        raise MeshingError(
            "MESH_RESOURCE_LIMIT",
            f"Requested characteristic size projects {projected} candidate nodes, above the configured limit",
        )
    axes = [
        np.linspace(low, high, count, dtype=np.float64)
        for low, high, count in zip(
            (box.xmin, box.ymin, box.zmin), (box.xmax, box.ymax, box.zmax), counts
        )
    ]
    tolerance = max(size_mm * 1e-5, 1e-6)
    for x in axes[0]:
        for y in axes[1]:
            for z in axes[2]:
                point = np.asarray((x, y, z), dtype=np.float64)
                if _inside(solid, point, tolerance):
                    points.append((float(x), float(y), float(z)))
    return _deduplicate(points, tolerance)


def _rectangular_brep_mesh(solid: Any, size_mm: float, maximum_nodes: int) -> tuple[np.ndarray, list[tuple[int, int, int, int]]] | None:
    """Mesh a BRep proven equal to its axis-aligned bounding volume.

    This is a generic BRep-derived rectilinear fallback, not a parameterized
    geometry shortcut: bounds and volume are read from the compiled solid.
    It avoids surface-tessellation drift for analytical prism benchmarks.
    """
    box = solid.BoundingBox(); volume = float(box.xlen * box.ylen * box.zlen)
    if volume <= 0 or abs(float(solid.Volume()) - volume) > max(volume * 1e-9, 1e-9):
        return None
    counts = [max(2, int(math.ceil(length / size_mm)) + 1) for length in (box.xlen, box.ylen, box.zlen)]
    total = counts[0] * counts[1] * counts[2]
    if total > maximum_nodes:
        raise MeshingError("MESH_RESOURCE_LIMIT", "Rectilinear BRep mesh exceeds configured node limit")
    axes = [np.linspace(low, high, count) for low, high, count in zip((box.xmin, box.ymin, box.zmin), (box.xmax, box.ymax, box.zmax), counts)]
    points = np.asarray([(x, y, z) for x in axes[0] for y in axes[1] for z in axes[2]], dtype=float)
    def index(ix: int, iy: int, iz: int) -> int: return (ix * counts[1] + iy) * counts[2] + iz
    tetrahedra: list[tuple[int, int, int, int]] = []
    for ix in range(counts[0] - 1):
        for iy in range(counts[1] - 1):
            for iz in range(counts[2] - 1):
                a=index(ix,iy,iz); b=index(ix+1,iy,iz); c=index(ix,iy+1,iz); d=index(ix+1,iy+1,iz)
                e=index(ix,iy,iz+1); f=index(ix+1,iy,iz+1); g=index(ix,iy+1,iz+1); h=index(ix+1,iy+1,iz+1)
                tetrahedra.extend(((a,b,d,h),(a,d,c,h),(a,c,g,h),(a,g,e,h),(a,e,f,h),(a,f,b,h)))
    return points, tetrahedra


def _tet_volume(points: np.ndarray) -> float:
    return float(np.linalg.det(np.stack((points[1] - points[0], points[2] - points[0], points[3] - points[0]))) / 6.0)


def _tetra_solid(points_mm: np.ndarray):
    """Build an OCC tetrahedron solely for authoritative Boolean containment."""
    import cadquery as cq
    faces = [cq.Face.makeFromWires(cq.Wire.makePolygon([tuple(points_mm[index]) for index in face], close=True))
             for face in ((0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2))]
    return cq.Solid.makeSolid(cq.Shell.makeShell(faces))


def _contained_tetrahedron(solid: Any, points_mm: np.ndarray, *, absolute_tolerance_mm3: float,
                           relative_tolerance: float = 1e-7) -> bool:
    volume = abs(_tet_volume(points_mm))
    if volume <= absolute_tolerance_mm3:
        return False
    try:
        intersection = float(_tetra_solid(points_mm).intersect(solid).Volume())
    except Exception:
        return False
    return abs(intersection - volume) <= max(absolute_tolerance_mm3, relative_tolerance * volume)


def _tetrahedralize(
    solid: Any, points_mm: np.ndarray, size_mm: float, maximum_elements: int = 300_000,
    *, return_metrics: bool = False,
) -> list[tuple[int, int, int, int]] | tuple[list[tuple[int, int, int, int]], dict[str, float | int]]:
    if len(points_mm) < 4:
        raise MeshingError("EMPTY_VOLUME_MESH", "Fewer than four distinct CAD-derived points are available")
    try:
        raw = Delaunay(points_mm, qhull_options="Qbb Qc Qz Q12").simplices
    except QhullError as exc:
        raise MeshingError("TETRAHEDRALIZATION_FAILED", "CAD-derived points could not form a 3D tetrahedralization") from exc
    if len(raw) > maximum_elements:
        raise MeshingError("MESH_RESOURCE_LIMIT", "Fallback Delaunay connectivity exceeds the configured element limit")
    volume_tolerance_mm3 = max(size_mm**3 * 1e-12, 1e-18)
    kept: list[tuple[int, int, int, int]] = []
    for simplex in raw:
        ids = [int(value) for value in simplex]
        tet = points_mm[ids]
        signed = _tet_volume(tet)
        if abs(signed) <= volume_tolerance_mm3:
            continue
        if signed < 0:
            ids[2], ids[3] = ids[3], ids[2]
            tet = points_mm[ids]
        if not _contained_tetrahedron(solid, tet, absolute_tolerance_mm3=volume_tolerance_mm3):
            continue
        kept.append(tuple(ids))
    if not kept:
        raise MeshingError("EMPTY_VOLUME_MESH", "No positive-volume tetrahedra remained inside the authoritative BRep")
    total = sum(abs(_tet_volume(points_mm[list(item)])) for item in kept)
    source_volume = float(solid.Volume())
    coverage = total / source_volume
    if abs(total - source_volume) > max(size_mm**3, _COVERAGE_TOLERANCE * source_volume):
        raise MeshingError("UNSUPPORTED_VOLUME_TOPOLOGY", "Bounded OCC fallback cannot establish aggregate authoritative volume agreement")
    result = sorted(set(kept))
    metrics: dict[str, float | int] = {
        "candidate_node_count": len(points_mm), "raw_delaunay_tetrahedron_count": len(raw),
        "accepted_contained_tetrahedron_count": len(kept),
        "rejected_crossing_tetrahedron_count": len(raw) - len(kept),
        "brep_volume_mm3": source_volume, "retained_tetrahedron_volume_mm3": total,
        "coverage_ratio": coverage, "aggregate_relative_volume_error": abs(total - source_volume) / source_volume,
    }
    return (result, metrics) if return_metrics else result


def _fallback_mesh_with_adaptive_coverage(
    solid: Any, requested_size_mm: float, maximum_nodes: int, maximum_elements: int,
) -> tuple[np.ndarray, list[tuple[int, int, int, int]], dict[str, float | int]]:
    """Return the first certified bounded OCC/SciPy fallback attempt.

    Candidate samples are regenerated from the BRep for every level.  The
    sequence is deliberately short and deterministic; a geometry which cannot
    attain 85% authoritative-volume coverage within these contracts is not
    represented as a solver-grade mesh.
    """
    last_error: MeshingError | None = None
    for attempt, factor in enumerate(_FALLBACK_REFINEMENT_FACTORS):
        effective_size = requested_size_mm * factor
        try:
            points = _candidate_points(solid, effective_size, maximum_nodes)
            tetrahedra, metrics = _tetrahedralize(
                solid, points, effective_size, maximum_elements, return_metrics=True,
            )
        except MeshingError as exc:
            last_error = exc
            if exc.code in {"MESH_RESOURCE_LIMIT", "EMPTY_VOLUME_MESH", "UNSUPPORTED_VOLUME_TOPOLOGY"}:
                continue
            raise
        metrics.update({
            "requested_characteristic_size_mm": requested_size_mm,
            "effective_characteristic_size_mm": effective_size,
            "adaptive_attempt": attempt,
        })
        return points, tetrahedra, metrics
    if last_error is not None and last_error.code == "MESH_RESOURCE_LIMIT":
        raise last_error
    raise MeshingError(
        "UNSUPPORTED_VOLUME_TOPOLOGY",
        "Bounded OCC fallback could not certify aggregate BRep volume coverage within its fixed sampling budget",
    )


def _canonicalize(
    nodes_mm: np.ndarray, tetrahedra: list[tuple[int, int, int, int]]
) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    order = np.lexsort((nodes_mm[:, 2], nodes_mm[:, 1], nodes_mm[:, 0]))
    inverse = np.empty(len(order), dtype=np.int64)
    inverse[order] = np.arange(len(order))
    nodes = nodes_mm[order] * MM_TO_M
    tets: list[tuple[int, int, int, int]] = []
    for tet in tetrahedra:
        mapped = [int(inverse[index]) for index in tet]
        positive_orderings = [
            permutation for permutation in permutations(mapped)
            if _tet_volume(nodes[list(permutation)]) > 0
        ]
        if not positive_orderings:
            raise MeshingError("INVALID_ELEMENT_QUALITY", "A tetrahedron has no positive orientation")
        tets.append(min(positive_orderings))
    return nodes, sorted(set(tets))


def _boundary_facets(tetrahedra: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int]]:
    counts: dict[tuple[int, int, int], int] = {}
    for tet in tetrahedra:
        for face in combinations(tet, 3):
            key = tuple(sorted(face))
            counts[key] = counts.get(key, 0) + 1
    return sorted(face for face, count in counts.items() if count == 1)


def _shape_distance_mm(shape: Any, point_mm: np.ndarray) -> float:
    vertex = cq.Vertex.makeVertex(*[float(value) for value in point_mm])
    try:
        return float(shape.distanceToShape(vertex)[0])
    except Exception:
        try:
            from OCP.BRepExtrema import BRepExtrema_DistShapeShape

            distance = BRepExtrema_DistShapeShape(shape.wrapped, vertex.wrapped)
            distance.Perform()
            if not distance.IsDone():
                return math.inf
            return float(distance.Value())
        except Exception as exc:
            raise MeshingError("SEMANTIC_DISTANCE_FAILED", "OpenCascade could not measure semantic face distance") from exc


def _quality(nodes: np.ndarray, tetrahedra: list[tuple[int, int, int, int]], facets: list[tuple[int, int, int]]) -> MeshQualityMetrics:
    volumes: list[float] = []
    edge_lengths: list[float] = []
    ratios: list[float] = []
    mean_ratios: list[float] = []
    inverted = 0
    degenerate = 0
    for tet in tetrahedra:
        points = nodes[list(tet)]
        signed = _tet_volume(points)
        if signed < 0:
            inverted += 1
        volume = abs(signed)
        volumes.append(volume)
        lengths = [float(np.linalg.norm(points[a] - points[b])) for a, b in combinations(range(4), 2)]
        edge_lengths.extend(lengths)
        shortest = min(lengths)
        ratios.append(max(lengths) / shortest if shortest > 0 else math.inf)
        denominator = sum(value * value for value in lengths)
        quality = 12.0 * (3.0 * volume) ** (2.0 / 3.0) / denominator if denominator > 0 and volume > 0 else 0.0
        mean_ratios.append(min(1.0, max(0.0, quality)))
        if volume <= 1e-24 or shortest <= 1e-12:
            degenerate += 1
    return MeshQualityMetrics(
        node_count=len(nodes), tetrahedron_count=len(tetrahedra), boundary_facet_count=len(facets),
        minimum_element_volume_m3=min(volumes, default=0.0),
        minimum_edge_length_m=min(edge_lengths, default=0.0),
        maximum_edge_length_m=max(edge_lengths, default=0.0),
        maximum_edge_aspect_ratio=max(ratios, default=0.0),
        minimum_mean_ratio_quality=min(mean_ratios, default=0.0),
        inverted_element_count=inverted, degenerate_element_count=degenerate,
    )


def generate_mesh(compiled: CompiledDesign, domains: list[PhysicsDomain], specification: MeshSpecification) -> GeneratedMesh:
    preparation = prepare_geometry(compiled, domains)
    if preparation.status == GeometryPreparationState.INVALID_FOR_MESHING:
        raise MeshingError("INVALID_GEOMETRY_FOR_MESHING", "; ".join(preparation.diagnostics))
    size_mm = _length_mm(specification.target_size)
    parameters = resolve_parameters(compiled.document)
    tolerance_mm = max(size_mm * 1e-6, 1e-7)
    try:
        semantic_geometry = resolve_semantic_geometry(
            compiled.document,
            compiled.bodies,
            resolve_length_mm=lambda value: resolve_length_mm(value, parameters),
            tolerance_mm=tolerance_mm,
            fail_on_lost=True,
        )
    except SemanticTopologyError as exc:
        raise MeshingError(exc.code, str(exc)) from exc
    sizing_tags = {item.semantic_region for item in specification.semantic_sizing}
    semantic_tags = {item.tag for item in semantic_geometry}
    unknown_sizing = sorted(sizing_tags - semantic_tags)
    if unknown_sizing:
        raise MeshingError("UNKNOWN_SEMANTIC_SIZING_REGION", f"Unknown semantic sizing regions: {unknown_sizing}")

    all_nodes: list[tuple[float, float, float]] = []
    all_tets: list[tuple[int, int, int, int]] = []
    domain_ranges: list[tuple[PhysicsDomain, int, int, int, int]] = []
    fallback_provenance: list[dict[str, float | int | str]] = []
    for domain in domains:
        solids = _solids(compiled.bodies[domain.source_body_id])
        if len(solids) != 1:
            raise MeshingError(
                "DOMAIN_SOLID_CARDINALITY",
                f"Domain '{domain.domain_id}' must resolve to exactly one solid in this bounded adapter",
            )
        rectangular = _rectangular_brep_mesh(solids[0], size_mm, specification.maximum_nodes - len(all_nodes))
        if rectangular is None:
            points_mm, local_tets, provenance = _fallback_mesh_with_adaptive_coverage(
                solids[0], size_mm, specification.maximum_nodes - len(all_nodes),
                specification.maximum_elements - len(all_tets),
            )
            fallback_provenance.append({"domain_id": domain.domain_id, **provenance})
            # Delaunay boundary facets are not CAD-face entities.  A local
            # semantic sizing request requires a certified boundary group, and
            # this bounded fallback cannot manufacture one from its artificial
            # cut facets.  Reject before emitting a mesh whose port sizing is
            # only metadata rather than an actual proven boundary condition.
            if any(
                region.body_id == domain.source_body_id and region.tag in sizing_tags
                for region in semantic_geometry
            ):
                raise MeshingError(
                    "UNSUPPORTED_SEMANTIC_BOUNDARY_MAPPING",
                    "Bounded OCC fallback cannot certify local semantic boundary facets on this nonrectangular BRep",
                )
        else:
            points_mm, local_tets = rectangular
        nodes_m, local_tets = _canonicalize(points_mm, local_tets)
        offset = len(all_nodes)
        node_start = offset
        start = len(all_tets)
        all_nodes.extend(tuple(float(value) for value in point) for point in nodes_m)
        all_tets.extend(tuple(index + offset for index in tet) for tet in local_tets)
        end = len(all_tets)
        domain_ranges.append((domain, start, end, node_start, len(all_nodes)))
    if len(all_nodes) > specification.maximum_nodes or len(all_tets) > specification.maximum_elements:
        raise MeshingError("MESH_RESOURCE_LIMIT", "Generated mesh exceeds configured node/element limits")
    nodes = np.asarray(all_nodes, dtype=np.float64)
    facets = _boundary_facets(all_tets)
    quality = _quality(nodes, all_tets, facets)
    if quality.inverted_element_count or quality.degenerate_element_count:
        raise MeshingError("INVALID_ELEMENT_QUALITY", "Mesh contains inverted or degenerate tetrahedra")

    domain_mappings: list[DomainMeshMapping] = []
    for group, (domain, start, end, _node_start, _node_end) in enumerate(domain_ranges, start=1):
        domain_mappings.append(DomainMeshMapping(
            domain_id=domain.domain_id,
            source_body_id=domain.source_body_id,
            domain_kind=domain.domain_kind,
            physical_group_id=group,
            volume_element_ids=list(range(start + 1, end + 1)),
        ))

    semantic_mappings: list[SemanticMeshMapping] = []
    ignored_non_surface_regions: list[str] = []
    ignored_unselected_regions: list[str] = []
    domain_by_body = {item.source_body_id: item.domain_id for item in domains}
    node_range_by_domain = {
        domain.domain_id: (node_start, node_end)
        for domain, _start, _end, node_start, node_end in domain_ranges
    }
    facet_centroids_mm = np.asarray([nodes[list(facet)].mean(axis=0) / MM_TO_M for facet in facets])
    semantic_group_base = len(domain_mappings) + 1
    for group_offset, region in enumerate(semantic_geometry):
        if region.topology_kind != "face":
            if region.tag in sizing_tags:
                raise MeshingError(
                    "SEMANTIC_DIMENSION_MISMATCH",
                    f"Sizing region '{region.tag}' resolves to {region.topology_kind}, not a boundary face",
                )
            ignored_non_surface_regions.append(region.tag)
            continue
        domain_id = domain_by_body.get(region.body_id)
        if domain_id is None:
            if region.tag in sizing_tags:
                raise MeshingError(
                    "SEMANTIC_DOMAIN_MISSING",
                    f"Sizing region '{region.tag}' does not belong to a selected physics domain",
                )
            ignored_unselected_regions.append(region.tag)
            continue
        matching: list[int] = []
        threshold = max(size_mm * 0.55, tolerance_mm * 10)
        all_body_faces = list(compiled.bodies[region.body_id].faces().vals())
        for facet_id, centroid in enumerate(facet_centroids_mm, start=1):
            node_start, node_end = node_range_by_domain[domain_id]
            if not all(node_start <= node_id < node_end for node_id in facets[facet_id - 1]):
                continue
            selected_distance = min(
                (_shape_distance_mm(shape, centroid) for shape in region.shapes), default=math.inf
            )
            nearest_body_distance = min(
                (_shape_distance_mm(shape, centroid) for shape in all_body_faces), default=math.inf
            )
            # A semantic boundary facet must have every vertex on the selected
            # CAD face.  Centroid-only matching would incorrectly bind an
            # artificial/internal cut facet that merely crosses near a face.
            vertex_distance = max(
                min((_shape_distance_mm(shape, nodes[node_id] / MM_TO_M) for shape in region.shapes), default=math.inf)
                for node_id in facets[facet_id - 1]
            )
            if (
                vertex_distance <= max(size_mm * 1e-3, tolerance_mm * 10)
                and selected_distance <= threshold
                and selected_distance <= nearest_body_distance + tolerance_mm
            ):
                matching.append(facet_id)
        if not matching:
            raise MeshingError(
                "EMPTY_SEMANTIC_MESH_GROUP",
                f"Semantic region '{region.tag}' mapped to no boundary facets",
            )
        semantic_mappings.append(SemanticMeshMapping(
            semantic_region=region.tag,
            body_id=region.body_id,
            cad_resolution_status=region.status.value,
            topology_kind="face",
            topology_signatures=list(region.topology_signatures),
            physical_group_id=semantic_group_base + group_offset,
            boundary_facet_ids=matching,
            domain_ids=[domain_id],
            mapping_status="EXACT" if region.status.value == "EXACT" else "MAPPED",
        ))

    identity = {
        "design_hash": compiled.design_hash,
        "geometry_fingerprint": compiled.geometry_fingerprint,
        "mesher": {"id": MESHER_ID, "version": MESHER_VERSION},
        "specification": specification.model_dump(mode="json"),
        "domains": [item.model_dump(mode="json") for item in domains],
        "nodes_m": [[float(format(value, ".15g")) for value in point] for point in nodes],
        "tetra4": all_tets,
        "boundary_triangle3": facets,
        "semantic_mappings": [item.model_dump(mode="json") for item in semantic_mappings],
        "fallback_provenance": fallback_provenance,
    }
    mesh_hash = _canonical_hash(identity)
    mesh_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"asre-lab:mesh:{mesh_hash}"))
    warnings = [
        "Bounded OCC/SciPy fallback is active; Gmsh is not installed in the deployment environment",
        "Bounded OCC/SciPy volume fallback is active; arbitrary nonconforming, contact, and multi-body coupling meshes are unsupported",
    ]
    if ignored_non_surface_regions:
        warnings.append(
            "Non-surface semantic regions are not emitted by the tetra4 boundary adapter: "
            + ", ".join(sorted(ignored_non_surface_regions))
        )
    if ignored_unselected_regions:
        warnings.append(
            "Semantic regions on unselected CAD bodies are not part of this mesh: "
            + ", ".join(sorted(ignored_unselected_regions))
        )
    metadata = MeshArtifact(
        mesh_id=mesh_id,
        mesh_hash=mesh_hash,
        design_hash=compiled.design_hash,
        geometry_fingerprint=compiled.geometry_fingerprint,
        mesher_version=MESHER_VERSION,
        element_types=["tetra4", "triangle3"],
        specification=specification,
        domains=domain_mappings,
        semantic_mappings=semantic_mappings,
        quality=quality,
        fallback_provenance=fallback_provenance,
        validation_status=ValidationState.VALID_WITH_WARNINGS,
        warnings=warnings,
    )
    return GeneratedMesh(
        metadata=metadata,
        nodes_m=tuple(tuple(float(value) for value in point) for point in nodes),
        tetrahedra=tuple(all_tets),
        boundary_facets=tuple(facets),
    )


def write_gmsh22(mesh: GeneratedMesh, path: Path) -> None:
    """Serialize physical volume/surface groups to deterministic Gmsh 2.2 ASCII."""
    facet_groups: dict[int, int] = {}
    for mapping in mesh.metadata.semantic_mappings:
        for facet_id in mapping.boundary_facet_ids:
            previous = facet_groups.get(facet_id)
            if previous is not None and previous != mapping.physical_group_id:
                raise MeshingError("AMBIGUOUS_FACET_GROUP", "A boundary facet belongs to multiple semantic groups")
            facet_groups[facet_id] = mapping.physical_group_id
    volume_group_by_element: dict[int, int] = {}
    for mapping in mesh.metadata.domains:
        for element_id in mapping.volume_element_ids:
            volume_group_by_element[element_id] = mapping.physical_group_id
    physical_names = [
        (3, item.physical_group_id, f"domain:{item.domain_id}") for item in mesh.metadata.domains
    ] + [
        (2, item.physical_group_id, f"region:{item.semantic_region}") for item in mesh.metadata.semantic_mappings
    ]
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", str(len(physical_names))]
    lines.extend(f'{dim} {group} "{name}"' for dim, group, name in physical_names)
    lines.extend(["$EndPhysicalNames", "$Nodes", str(len(mesh.nodes_m))])
    lines.extend(
        f"{index} {point[0]:.17g} {point[1]:.17g} {point[2]:.17g}"
        for index, point in enumerate(mesh.nodes_m, start=1)
    )
    lines.extend(["$EndNodes", "$Elements", str(len(mesh.boundary_facets) + len(mesh.tetrahedra))])
    element_id = 1
    for facet_index, facet in enumerate(mesh.boundary_facets, start=1):
        group = facet_groups.get(facet_index, 0)
        nodes = " ".join(str(value + 1) for value in facet)
        lines.append(f"{element_id} 2 2 {group} {group} {nodes}")
        element_id += 1
    for tet_index, tet in enumerate(mesh.tetrahedra, start=1):
        group = volume_group_by_element[tet_index]
        nodes = " ".join(str(value + 1) for value in tet)
        lines.append(f"{element_id} 4 2 {group} {group} {nodes}")
        element_id += 1
    lines.extend(["$EndElements", ""])
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
