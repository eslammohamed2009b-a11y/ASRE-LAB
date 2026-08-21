"""Certified CAD-derived finite-volume meshing for the bounded OpenFOAM CFD scope.

The CAD BRep remains authoritative.  STL is emitted only as a deterministic,
server-owned snappyHexMesh representation and is never accepted as input.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cadquery as cq
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.module1_design.cad_v2_compiler import CompiledDesign, resolve_length_mm, resolve_parameters
from app.module1_design.cad_v2_semantics import ResolvedSemanticGeometry, resolve_semantic_geometry
from app.module2_simulation.geometry_physics_schemas import DomainKind, PhysicsDomain, ValidationState


MESHER_ID = "openfoam_snappyhex_fv"
MESHER_VERSION = "openfoam-foundation-14_20260724+asre-fv-generator-1.0"
SURFACE_TESSELLATION_TOLERANCE_M = 5e-5
SURFACE_VOLUME_ERROR_LIMIT = 1e-3
SURFACE_AREA_ERROR_LIMIT = 5e-3
FINAL_VOLUME_ERROR_LIMIT = 1e-3
MAX_NON_ORTHOGONALITY = 65.0
MAX_SKEWNESS = 4.0
_DYNAMIC_TOKENS = ("#code", "#codeStream", "#codeDict", "dynamicCode", "codedFixedValue", "#include", "libs")
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class CFDMeshError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CFDMeshResolutionV1(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    transverse_cell_size_m: float = Field(default=0.001, ge=2.5e-4, le=0.02)
    streamwise_cell_size_m: float = Field(default=0.005, ge=5e-4, le=0.05)
    background_margin_cells: Literal[2] = 2
    surface_refinement_level: Literal[0] = 0
    maximum_cells: int = Field(default=500_000, ge=1_000, le=1_000_000)
    maximum_runtime_seconds: int = Field(default=1_200, ge=30, le=3_600)
    maximum_memory_mb: int = Field(default=2_048, ge=256, le=8_192)

    @model_validator(mode="after")
    def finite_sizes(self) -> "CFDMeshResolutionV1":
        if not all(math.isfinite(item) for item in (self.transverse_cell_size_m, self.streamwise_cell_size_m)):
            raise ValueError("CFD mesh sizes must be finite")
        return self


class SemanticSurfacePatchV1(_Strict):
    semantic_region: str
    topology_signatures: list[str]
    surface_region: str
    final_patch: str
    category: Literal["inlet", "outlet", "wall"]
    triangle_count: int = Field(gt=0)
    final_face_count: int = Field(default=0, ge=0)


class SurfaceCertificationV1(_Strict):
    source_surface_hash: str
    tessellation_tolerance_m: Literal[5e-05] = SURFACE_TESSELLATION_TOLERANCE_M
    triangle_count: int = Field(gt=0)
    manifold: Literal[True] = True
    finite: Literal[True] = True
    degenerate_triangle_count: Literal[0] = 0
    cad_volume_m3: float = Field(gt=0)
    surface_volume_m3: float = Field(gt=0)
    relative_volume_error: float = Field(ge=0, le=SURFACE_VOLUME_ERROR_LIMIT)
    cad_surface_area_m2: float = Field(gt=0)
    surface_area_m2: float = Field(gt=0)
    relative_area_error: float = Field(ge=0, le=SURFACE_AREA_ERROR_LIMIT)
    maximum_cad_deviation_m: float = Field(ge=0, le=SURFACE_TESSELLATION_TOLERANCE_M)


class CheckMeshMetricsV1(_Strict):
    mesh_ok: bool
    cell_count: int = Field(gt=0)
    face_count: int = Field(gt=0)
    internal_face_count: int = Field(ge=0)
    boundary_face_count: int = Field(gt=0)
    max_non_orthogonality: float = Field(ge=0)
    average_non_orthogonality: float = Field(ge=0)
    max_skewness: float = Field(ge=0)
    max_aspect_ratio: float = Field(ge=0)
    minimum_volume_m3: float = Field(gt=0)
    total_volume_m3: float = Field(gt=0)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class GeometryCertificationV1(_Strict):
    cad_volume_m3: float = Field(gt=0)
    mesh_volume_m3: float = Field(gt=0)
    relative_volume_error: float = Field(ge=0, le=FINAL_VOLUME_ERROR_LIMIT)
    cell_center_count: int = Field(gt=0)
    cell_centers_inside: Literal[True] = True
    maximum_boundary_deviation_m: float = Field(ge=0, le=SURFACE_TESSELLATION_TOLERANCE_M)
    boundary_conforming: Literal[True] = True
    semantic_conforming: Literal[True] = True
    connected_region_count: Literal[1] = 1


class CFDGeneratedMeshV1(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    mesh_id: str
    mesh_hash: str
    design_hash: str
    geometry_fingerprint: str
    fluid_domain_id: str
    source_body_id: str
    mesher_id: Literal["openfoam_snappyhex_fv"] = MESHER_ID
    mesher_version: Literal["openfoam-foundation-14_20260724+asre-fv-generator-1.0"] = MESHER_VERSION
    source_surface_hash: str
    source_surface_tessellation_tolerance_m: Literal[5e-05] = SURFACE_TESSELLATION_TOLERANCE_M
    resolution: CFDMeshResolutionV1
    cell_count: int = Field(gt=0)
    face_count: int = Field(gt=0)
    internal_face_count: int = Field(ge=0)
    boundary_face_count: int = Field(gt=0)
    cell_types: list[Literal["hex8", "polyhedron"]]
    semantic_patches: list[SemanticSurfacePatchV1]
    surface_certification: SurfaceCertificationV1
    check_mesh: CheckMeshMetricsV1
    geometry_certification: GeometryCertificationV1
    coordinate_unit: Literal["m"] = "m"
    validation_status: Literal[ValidationState.VALID] = ValidationState.VALID
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str]


@dataclass(frozen=True)
class CertifiedCFDSurface:
    certification: SurfaceCertificationV1
    semantic_patches: tuple[SemanticSurfacePatchV1, ...]
    stl_ascii: str
    bounds_m: tuple[float, float, float, float, float, float]
    inside_point_m: tuple[float, float, float]
    triangles_by_semantic: dict[str, tuple[tuple[tuple[float, float, float], ...], ...]]


@dataclass(frozen=True)
class GeneratedMesherCase:
    surface: CertifiedCFDSurface
    resolution: CFDMeshResolutionV1
    background_counts: tuple[int, int, int]
    generated_files: tuple[str, ...]
    configuration_hash: str


@dataclass(frozen=True)
class _PolyMesh:
    points: np.ndarray
    faces: tuple[tuple[int, ...], ...]
    owners: tuple[int, ...]
    neighbours: tuple[int, ...]
    patches: dict[str, tuple[int, int, str]]


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _safe_patch(tag: str, category: str) -> str:
    name = f"asre_{category}_{hashlib.sha256(tag.encode()).hexdigest()[:12]}"
    if not _SAFE_NAME.fullmatch(name):
        raise CFDMeshError("UNSAFE_PATCH_NAME", "Generated semantic patch name is unsafe")
    return name


def _shape_distance_mm(shape: Any, point_mm: np.ndarray) -> float:
    vertex = cq.Vertex.makeVertex(*(float(item) for item in point_mm))
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
            raise CFDMeshError("SURFACE_DISTANCE_FAILED", "OpenCascade could not certify CAD surface distance") from exc


def _normal(face: Any, point_mm: np.ndarray) -> np.ndarray:
    try:
        value = face.normalAt(cq.Vector(*(float(item) for item in point_mm)))
    except Exception:
        value = face.normalAt()
    return np.asarray((value.x, value.y, value.z), dtype=float)


def _require_closed_manifold(edge_counts: dict[Any, int]) -> None:
    if not edge_counts or any(count != 2 for count in edge_counts.values()):
        raise CFDMeshError("NON_MANIFOLD_SURFACE", "Server-generated CFD surface is not a closed two-manifold envelope")


def _inside_point(solid: Any) -> tuple[float, float, float]:
    box = solid.BoundingBox()
    candidates = [solid.Center(), cq.Vector((box.xmin + box.xmax) / 2, (box.ymin + box.ymax) / 2, (box.zmin + box.zmax) / 2)]
    for ix in (0.25, 0.5, 0.75):
        for iy in (0.25, 0.5, 0.75):
            for iz in (0.25, 0.5, 0.75):
                candidates.append(cq.Vector(box.xmin + ix * box.xlen, box.ymin + iy * box.ylen, box.zmin + iz * box.zlen))
    for point in candidates:
        if solid.isInside(point, 1e-7):
            return point.x * 1e-3, point.y * 1e-3, point.z * 1e-3
    raise CFDMeshError("INSIDE_POINT_NOT_PROVEN", "No deterministic point was proven inside the authoritative fluid BRep")


def _resolved_semantics(compiled: CompiledDesign) -> list[ResolvedSemanticGeometry]:
    parameters = resolve_parameters(compiled.document)
    return resolve_semantic_geometry(
        compiled.document,
        compiled.bodies,
        resolve_length_mm=lambda value: resolve_length_mm(value, parameters),
        tolerance_mm=SURFACE_TESSELLATION_TOLERANCE_M * 1e3,
        fail_on_lost=True,
    )


def generate_certified_cfd_surface(
    compiled: CompiledDesign,
    domain: PhysicsDomain,
    semantic_categories: dict[str, Literal["inlet", "outlet", "wall"]],
) -> CertifiedCFDSurface:
    if domain.domain_kind != DomainKind.FLUID or not domain.explicit_fluid_volume:
        raise CFDMeshError("FLUID_DOMAIN_REQUIRED", "CFD meshing requires one explicit authoritative FLUID CAD volume")
    if domain.source_body_id not in compiled.bodies:
        raise CFDMeshError("DOMAIN_BODY_MISSING", "CFD fluid domain references a missing CAD body")
    if sorted(semantic_categories.values()) != ["inlet", "outlet", "wall"] or len(semantic_categories) != 3:
        raise CFDMeshError("SEMANTIC_SCOPE_MISMATCH", "Exactly one inlet, outlet, and wall semantic group are required")
    solids = list(compiled.bodies[domain.source_body_id].solids().vals())
    if len(solids) != 1:
        raise CFDMeshError("DOMAIN_SOLID_CARDINALITY", "CFD domain must resolve to exactly one closed CAD solid")
    solid = solids[0]
    regions = {item.tag: item for item in _resolved_semantics(compiled) if item.body_id == domain.source_body_id}
    if set(regions) != set(semantic_categories):
        raise CFDMeshError("SEMANTIC_SCOPE_MISMATCH", "CFD semantics must cover exactly the selected fluid body's boundary groups")
    if any(item.topology_kind != "face" for item in regions.values()):
        raise CFDMeshError("SEMANTIC_DIMENSION_MISMATCH", "Every CFD semantic region must resolve to CAD faces")

    all_faces = list(compiled.bodies[domain.source_body_id].faces().vals())
    resolved_faces = [shape for item in regions.values() for shape in item.shapes]
    if len(resolved_faces) != len(all_faces) or any(sum(bool(face.isSame(candidate)) for candidate in resolved_faces) != 1 for face in all_faces):
        raise CFDMeshError("SEMANTIC_SURFACE_COVERAGE", "Semantic CFD regions must cover every CAD boundary face exactly once")

    triangles_by_semantic: dict[str, tuple[tuple[tuple[float, float, float], ...], ...]] = {}
    maximum_deviation_m = 0.0
    surface_area_m2 = 0.0
    stl_parts: list[str] = []
    patches: list[SemanticSurfacePatchV1] = []
    edge_counts: dict[tuple[tuple[float, float, float], tuple[float, float, float]], int] = {}
    signed_volume = 0.0
    canonical: list[dict[str, Any]] = []
    for tag in sorted(semantic_categories):
        region = regions[tag]
        category = semantic_categories[tag]
        patch = _safe_patch(tag, category)
        output: list[tuple[tuple[float, float, float], ...]] = []
        for face in region.shapes:
            vertices, indices = face.tessellate(SURFACE_TESSELLATION_TOLERANCE_M * 1e3, 0.05)
            points_mm = np.asarray([(item.x, item.y, item.z) for item in vertices], dtype=float)
            for raw in indices:
                triangle_mm = points_mm[list(raw)]
                cross = np.cross(triangle_mm[1] - triangle_mm[0], triangle_mm[2] - triangle_mm[0])
                area_mm2 = 0.5 * float(np.linalg.norm(cross))
                if not np.isfinite(triangle_mm).all() or area_mm2 <= 1e-16:
                    raise CFDMeshError("DEGENERATE_SURFACE_TRIANGLE", "CAD tessellation produced a degenerate or nonfinite triangle")
                normal = _normal(face, triangle_mm.mean(axis=0))
                if float(np.dot(cross, normal)) < 0:
                    triangle_mm[[1, 2]] = triangle_mm[[2, 1]]
                    cross = -cross
                for sample in (*triangle_mm, triangle_mm.mean(axis=0)):
                    maximum_deviation_m = max(maximum_deviation_m, _shape_distance_mm(face, sample) * 1e-3)
                triangle_m = tuple(tuple(float(format(value * 1e-3, ".15g")) for value in point) for point in triangle_mm)
                output.append(triangle_m)
                surface_area_m2 += area_mm2 * 1e-6
                a, b, c = np.asarray(triangle_m)
                signed_volume += float(np.dot(a, np.cross(b, c))) / 6.0
                keys = [tuple(round(value, 12) for value in point) for point in triangle_m]
                for first, second in ((keys[0], keys[1]), (keys[1], keys[2]), (keys[2], keys[0])):
                    edge = tuple(sorted((first, second)))
                    edge_counts[edge] = edge_counts.get(edge, 0) + 1
        ordered = tuple(sorted(output, key=lambda item: tuple(value for point in item for value in point)))
        triangles_by_semantic[tag] = ordered
        canonical.append({"semantic_region": tag, "category": category, "patch": patch, "topology_signatures": list(region.topology_signatures), "triangles": ordered})
        patches.append(SemanticSurfacePatchV1(semantic_region=tag, topology_signatures=list(region.topology_signatures), surface_region=patch, final_patch=patch, category=category, triangle_count=len(ordered)))
        lines = [f"solid {patch}"]
        for triangle in ordered:
            a, b, c = (np.asarray(item) for item in triangle)
            normal = np.cross(b - a, c - a); normal /= np.linalg.norm(normal)
            lines.extend([f"  facet normal {normal[0]:.17g} {normal[1]:.17g} {normal[2]:.17g}", "    outer loop", *(f"      vertex {point[0]:.17g} {point[1]:.17g} {point[2]:.17g}" for point in triangle), "    endloop", "  endfacet"])
        lines.append(f"endsolid {patch}")
        stl_parts.append("\n".join(lines))
    _require_closed_manifold(edge_counts)

    cad_volume_m3 = float(solid.Volume()) * 1e-9
    cad_area_m2 = float(solid.Area()) * 1e-6
    surface_volume_m3 = abs(signed_volume)
    volume_error = abs(surface_volume_m3 - cad_volume_m3) / cad_volume_m3
    area_error = abs(surface_area_m2 - cad_area_m2) / cad_area_m2
    if volume_error > SURFACE_VOLUME_ERROR_LIMIT:
        raise CFDMeshError("SURFACE_VOLUME_MISMATCH", "Tessellated surface volume does not certify against the CAD BRep")
    if area_error > SURFACE_AREA_ERROR_LIMIT or maximum_deviation_m > SURFACE_TESSELLATION_TOLERANCE_M:
        raise CFDMeshError("SURFACE_GEOMETRY_MISMATCH", "Tessellated surface area/deviation does not certify against the CAD BRep")
    surface_hash = _hash({"design_hash": compiled.design_hash, "geometry_fingerprint": compiled.geometry_fingerprint, "tolerance_m": SURFACE_TESSELLATION_TOLERANCE_M, "regions": canonical})
    box = solid.BoundingBox()
    certification = SurfaceCertificationV1(
        source_surface_hash=surface_hash, triangle_count=sum(item.triangle_count for item in patches),
        cad_volume_m3=cad_volume_m3, surface_volume_m3=surface_volume_m3, relative_volume_error=volume_error,
        cad_surface_area_m2=cad_area_m2, surface_area_m2=surface_area_m2, relative_area_error=area_error,
        maximum_cad_deviation_m=maximum_deviation_m,
    )
    return CertifiedCFDSurface(
        certification=certification, semantic_patches=tuple(patches), stl_ascii="\n".join(stl_parts) + "\n",
        bounds_m=tuple(value * 1e-3 for value in (box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax)),
        inside_point_m=_inside_point(solid), triangles_by_semantic=triangles_by_semantic,
    )


def _foam_header(object_name: str) -> str:
    return f"FoamFile\n{{\n    version 2.0;\n    format ascii;\n    class dictionary;\n    object {object_name};\n}}\n"


def generate_snappyhex_case(case: Path, surface: CertifiedCFDSurface, resolution: CFDMeshResolutionV1 = CFDMeshResolutionV1()) -> GeneratedMesherCase:
    xmin, ymin, zmin, xmax, ymax, zmax = surface.bounds_m
    lengths = np.asarray((xmax - xmin, ymax - ymin, zmax - zmin))
    stream_axis = int(np.argmax(lengths))
    sizes = np.full(3, resolution.transverse_cell_size_m); sizes[stream_axis] = resolution.streamwise_cell_size_m
    lower = np.asarray((xmin, ymin, zmin)) - resolution.background_margin_cells * sizes
    upper = np.asarray((xmax, ymax, zmax)) + resolution.background_margin_cells * sizes
    counts = np.ceil((upper - lower) / sizes).astype(int)
    upper = lower + counts * sizes
    background_cells = int(np.prod(counts))
    if background_cells > resolution.maximum_cells:
        raise CFDMeshError("MESH_RESOURCE_LIMIT", "Server-generated background mesh exceeds the fixed cell cap")
    vertices = (
        (lower[0], lower[1], lower[2]), (upper[0], lower[1], lower[2]), (upper[0], upper[1], lower[2]), (lower[0], upper[1], lower[2]),
        (lower[0], lower[1], upper[2]), (upper[0], lower[1], upper[2]), (upper[0], upper[1], upper[2]), (lower[0], upper[1], upper[2]),
    )
    block = _foam_header("blockMeshDict") + "scale 1;\nvertices\n(\n" + "\n".join(f"    ({' '.join(f'{value:.17g}' for value in point)})" for point in vertices) + ") ;\n" + f"blocks (hex (0 1 2 3 4 5 6 7) ({counts[0]} {counts[1]} {counts[2]}) simpleGrading (1 1 1));\nedges ();\nboundary (background {{ type patch; faces ((0 4 7 3) (1 2 6 5) (0 1 5 4) (3 7 6 2) (0 3 2 1) (4 5 6 7)); }});\n"
    regions = "\n".join(f"            {item.surface_region} {{ name {item.final_patch}; }}" for item in surface.semantic_patches)
    refinement = "\n".join(f"            {item.final_patch} {{ level (0 0); patchInfo {{ type {'wall' if item.category == 'wall' else 'patch'}; }} }}" for item in surface.semantic_patches)
    inside = " ".join(f"{value:.17g}" for value in surface.inside_point_m)
    snappy = _foam_header("snappyHexMeshDict") + f"""castellatedMesh true;
snap true;
addLayers false;
geometry
{{
    asre_surface.stl
    {{
        type triSurfaceMesh;
        file "asre_surface.stl";
        name asre_surface;
        regions
        {{
{regions}
        }}
    }}
}}
castellatedMeshControls
{{
    maxLocalCells {resolution.maximum_cells};
    maxGlobalCells {resolution.maximum_cells};
    minRefinementCells 0;
    maxLoadUnbalance 0.10;
    nCellsBetweenLevels 2;
    features ();
    refinementSurfaces
    {{
        asre_surface
        {{
            level (0 0);
            regions
            {{
{refinement}
            }}
        }}
    }}
    resolveFeatureAngle 30;
    refinementRegions {{}}
    locationInMesh ({inside});
    allowFreeStandingZoneFaces false;
}}
snapControls
{{
    nSmoothPatch 3;
    tolerance 2.0;
    nSolveIter 30;
    nRelaxIter 5;
    nFeatureSnapIter 10;
    implicitFeatureSnap true;
    explicitFeatureSnap false;
    multiRegionFeatureSnap false;
}}
addLayersControls {{ layers {{}} relativeSizes true; expansionRatio 1; finalLayerThickness 0.3; minThickness 0.1; nGrow 0; featureAngle 60; nRelaxIter 3; nSmoothSurfaceNormals 1; nSmoothNormals 3; nSmoothThickness 10; maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90; nBufferCellsNoExtrude 0; nLayerIter 50; }}
meshQualityControls
{{
    maxNonOrtho {MAX_NON_ORTHOGONALITY};
    maxBoundarySkewness 4;
    maxInternalSkewness {MAX_SKEWNESS};
    maxConcave 80;
    minVol 1e-18;
    minTetQuality 1e-15;
    minArea -1;
    minTwist 0.02;
    minDeterminant 0.001;
    minFaceWeight 0.02;
    minVolRatio 0.01;
    minTriangleTwist -1;
    nSmoothScale 4;
    errorReduction 0.75;
}}
mergeTolerance 1e-6;
"""
    generated = {
        "constant/triSurface/asre_surface.stl": surface.stl_ascii,
        "system/controlDict": _foam_header("controlDict") + "application blockMesh;\nstartFrom startTime;\nstartTime 0;\nstopAt endTime;\nendTime 1;\ndeltaT 1;\nwriteControl timeStep;\nwriteInterval 1;\nrunTimeModifiable false;\n",
        "system/blockMeshDict": block,
        "system/snappyHexMeshDict": snappy,
    }
    if any(token in content for content in generated.values() for token in _DYNAMIC_TOKENS):
        raise CFDMeshError("DYNAMIC_CODE_FORBIDDEN", "Generated mesher case contains a forbidden OpenFOAM directive")
    for relative, content in generated.items():
        target = case / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content, encoding="utf-8", newline="\n")
    configuration_hash = _hash({"surface_hash": surface.certification.source_surface_hash, "resolution": resolution.model_dump(mode="json"), "files": generated})
    return GeneratedMesherCase(surface, resolution, tuple(int(item) for item in counts), tuple(sorted(generated)), configuration_hash)


def run_snappyhex_mesher(case: Path, resolution: CFDMeshResolutionV1 = CFDMeshResolutionV1()) -> tuple[str, str, str]:
    outputs: list[str] = []
    for executable, arguments in (("blockMesh", ["-case", str(case)]), ("snappyHexMesh", ["-overwrite", "-case", str(case)]), ("checkMesh", ["-case", str(case)])):
        path = shutil.which(executable)
        if path is None:
            raise CFDMeshError("MESHER_BACKEND_UNAVAILABLE", f"Required fixed OpenFOAM utility {executable} is unavailable")
        try:
            completed = subprocess.run([path, *arguments], shell=False, capture_output=True, text=True, timeout=resolution.maximum_runtime_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise CFDMeshError("MESHER_TIMEOUT", f"Fixed OpenFOAM utility {executable} exceeded its runtime cap") from exc
        output = completed.stdout + completed.stderr
        if completed.returncode != 0:
            raise CFDMeshError("MESHER_EXECUTION_FAILED", f"Fixed OpenFOAM utility {executable} exited {completed.returncode}")
        outputs.append(output)
    return tuple(outputs)  # type: ignore[return-value]


def parse_check_mesh(log: str) -> CheckMeshMetricsV1:
    numeric = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    def integer(label: str) -> int:
        match = re.search(rf"(?m)^\s*{re.escape(label)}:\s*(\d+)", log)
        if not match: raise CFDMeshError("CHECKMESH_PARSE_FAILED", f"Missing checkMesh {label}")
        return int(match.group(1))
    def number(pattern: str, name: str) -> float:
        match = re.search(pattern, log)
        if not match: raise CFDMeshError("CHECKMESH_PARSE_FAILED", f"Missing checkMesh {name}")
        return float(match.group(1))
    cells = integer("cells"); faces = integer("faces"); internal = integer("internal faces")
    nonortho = re.search(r"Mesh non-orthogonality Max:\s*([0-9.eE+-]+)\s+average:\s*([0-9.eE+-]+)", log)
    if not nonortho: raise CFDMeshError("CHECKMESH_PARSE_FAILED", "Missing non-orthogonality metrics")
    minimum = number(r"Min volume =\s*" + numeric, "minimum volume")
    total = number(r"Total volume =\s*" + numeric, "total volume")
    errors = [line.strip() for line in log.splitlines() if "Failed" in line or "FOAM FATAL" in line or "negative volume" in line.lower()]
    warnings = [line.strip() for line in log.splitlines() if "warning" in line.lower()]
    metrics = CheckMeshMetricsV1(
        mesh_ok="Mesh OK." in log and not errors, cell_count=cells, face_count=faces, internal_face_count=internal,
        boundary_face_count=faces - internal, max_non_orthogonality=float(nonortho.group(1)), average_non_orthogonality=float(nonortho.group(2)),
        max_skewness=number(r"Max skewness =\s*" + numeric, "maximum skewness"),
        max_aspect_ratio=number(r"Max aspect ratio =\s*" + numeric, "maximum aspect ratio"),
        minimum_volume_m3=minimum, total_volume_m3=total, warnings=warnings, errors=errors,
    )
    if not metrics.mesh_ok or metrics.max_non_orthogonality > MAX_NON_ORTHOGONALITY or metrics.max_skewness > MAX_SKEWNESS or metrics.minimum_volume_m3 <= 0:
        raise CFDMeshError("CHECKMESH_REJECTED", "Final CFD mesh exceeds fixed checkMesh acceptance limits")
    return metrics


def _parse_list(path: Path, pattern: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.S)
    if not match: raise CFDMeshError("POLYMESH_PARSE_FAILED", f"Cannot parse {path.name}")
    return match.group(1)


def _parse_poly_mesh(case: Path) -> _PolyMesh:
    root = case / "constant" / "polyMesh"
    list_pattern = r"\n\s*\d+\s*\(\s*(.*?)\s*\)\s*\n\s*//"
    point_body = _parse_list(root / "points", list_pattern)
    points = np.asarray([[float(item) for item in row.split()] for row in re.findall(r"\(([^()]*)\)", point_body)], dtype=float)
    face_body = _parse_list(root / "faces", list_pattern)
    faces = tuple(tuple(int(item) for item in row.split()) for row in re.findall(r"\d+\(([^()]*)\)", face_body))
    owners = tuple(int(item) for item in _parse_list(root / "owner", list_pattern).split())
    neighbours = tuple(int(item) for item in _parse_list(root / "neighbour", list_pattern).split())
    boundary = (root / "boundary").read_text(encoding="utf-8")
    patches: dict[str, tuple[int, int, str]] = {}
    for name, block in re.findall(r"(?m)^\s*([a-z][a-z0-9_]*)\s*\n\s*\{(.*?)\}", boundary, re.S):
        n = re.search(r"nFaces\s+(\d+);", block); start = re.search(r"startFace\s+(\d+);", block); kind = re.search(r"type\s+(\w+);", block)
        if n and start and kind: patches[name] = (int(start.group(1)), int(n.group(1)), kind.group(1))
    if len(owners) != len(faces) or len(neighbours) > len(faces) or not patches:
        raise CFDMeshError("POLYMESH_PARSE_FAILED", "Final polyMesh topology is malformed")
    return _PolyMesh(points, faces, owners, neighbours, patches)


def read_fv_cell_geometry(case: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return deterministic polyhedral cell centroids and volumes from polyMesh."""
    poly = _parse_poly_mesh(case)
    cell_count = max((*poly.owners, *poly.neighbours)) + 1
    cell_faces: list[list[int]] = [[] for _ in range(cell_count)]
    for face_id, owner in enumerate(poly.owners):
        cell_faces[owner].append(face_id)
        if face_id < len(poly.neighbours):
            cell_faces[poly.neighbours[face_id]].append(face_id)
    centers: list[np.ndarray] = []
    volumes: list[float] = []
    for face_ids in cell_faces:
        point_ids = sorted({point for face_id in face_ids for point in poly.faces[face_id]})
        reference = poly.points[point_ids].mean(axis=0)
        weighted_center = np.zeros(3); volume = 0.0
        for face_id in face_ids:
            face = poly.faces[face_id]
            first = poly.points[face[0]]
            for index in range(1, len(face) - 1):
                a, b, c = first, poly.points[face[index]], poly.points[face[index + 1]]
                tetra_volume = abs(float(np.dot(a - reference, np.cross(b - reference, c - reference)))) / 6.0
                volume += tetra_volume
                weighted_center += tetra_volume * (reference + a + b + c) / 4.0
        if volume <= 0 or not math.isfinite(volume):
            raise CFDMeshError("INVALID_CELL_VOLUME", "Final CFD polyMesh contains a non-positive cell volume")
        centers.append(weighted_center / volume); volumes.append(volume)
    return np.asarray(centers), np.asarray(volumes)


def certify_final_cfd_mesh(
    compiled: CompiledDesign,
    domain: PhysicsDomain,
    generated: GeneratedMesherCase,
    case: Path,
    check_mesh_log: str,
) -> CFDGeneratedMeshV1:
    metrics = parse_check_mesh(check_mesh_log)
    poly = _parse_poly_mesh(case)
    expected = {item.final_patch for item in generated.surface.semantic_patches}
    if set(poly.patches) != expected:
        raise CFDMeshError("FINAL_SEMANTIC_COVERAGE", "Final CFD mesh contains missing or unexplained physical boundary patches")
    solid = list(compiled.bodies[domain.source_body_id].solids().vals())[0]
    regions = {item.tag: item for item in _resolved_semantics(compiled) if item.body_id == domain.source_body_id}
    cells: list[set[int]] = [set() for _ in range(metrics.cell_count)]
    for face_id, face in enumerate(poly.faces):
        cells[poly.owners[face_id]].update(face)
        if face_id < len(poly.neighbours): cells[poly.neighbours[face_id]].update(face)
    if any(not item for item in cells): raise CFDMeshError("POLYMESH_PARSE_FAILED", "Final mesh contains an empty cell topology")
    centers, computed_volumes = read_fv_cell_geometry(case)
    if abs(float(computed_volumes.sum()) - metrics.total_volume_m3) / metrics.total_volume_m3 > 1e-4:
        raise CFDMeshError("POLYMESH_VOLUME_MISMATCH", "Parsed polyhedral volumes disagree with checkMesh")
    tolerance_mm = SURFACE_TESSELLATION_TOLERANCE_M * 1e3
    if any(not solid.isInside(cq.Vector(*(point * 1e3)), tolerance_mm) for point in centers):
        raise CFDMeshError("CELL_OUTSIDE_AUTHORITATIVE_BREP", "A final CFD cell center is outside the authoritative fluid BRep")
    maximum_deviation = 0.0
    updated: list[SemanticSurfacePatchV1] = []
    for item in generated.surface.semantic_patches:
        start, count, _ = poly.patches[item.final_patch]
        shapes = regions[item.semantic_region].shapes
        for face_id in range(start, start + count):
            for point_id in poly.faces[face_id]:
                point_mm = poly.points[point_id] * 1e3
                maximum_deviation = max(maximum_deviation, min(_shape_distance_mm(shape, point_mm) for shape in shapes) * 1e-3)
        updated.append(item.model_copy(update={"final_face_count": count}))
    if maximum_deviation > SURFACE_TESSELLATION_TOLERANCE_M:
        raise CFDMeshError("FINAL_BOUNDARY_MISMATCH", "Final CFD boundary exceeds certified CAD deviation tolerance")
    cad_volume = generated.surface.certification.cad_volume_m3
    volume_error = abs(metrics.total_volume_m3 - cad_volume) / cad_volume
    if volume_error > FINAL_VOLUME_ERROR_LIMIT:
        raise CFDMeshError("FINAL_VOLUME_MISMATCH", "Final CFD mesh volume does not certify against the CAD BRep")
    cell_types = sorted({"hex8" if len(points) == 8 else "polyhedron" for points in cells})
    topology = {
        "points": [[float(format(value, ".15g")) for value in point] for point in poly.points],
        "faces": poly.faces, "owners": poly.owners, "neighbours": poly.neighbours,
        "patches": {key: poly.patches[key] for key in sorted(poly.patches)},
    }
    topology_hash = _hash(topology)
    mapping_hash = _hash([item.model_dump(mode="json") for item in updated])
    identity = {
        "design_hash": compiled.design_hash, "geometry_fingerprint": compiled.geometry_fingerprint,
        "domain": domain.model_dump(mode="json"), "surface": generated.surface.certification.model_dump(mode="json"),
        "semantic_topology": [item.model_dump(mode="json") for item in updated], "mesher": [MESHER_ID, MESHER_VERSION],
        "resolution": generated.resolution.model_dump(mode="json"), "configuration_hash": generated.configuration_hash,
        "topology_hash": topology_hash, "mapping_hash": mapping_hash,
    }
    mesh_hash = _hash(identity)
    geometry = GeometryCertificationV1(
        cad_volume_m3=cad_volume, mesh_volume_m3=metrics.total_volume_m3, relative_volume_error=volume_error,
        cell_center_count=len(centers), maximum_boundary_deviation_m=maximum_deviation,
    )
    return CFDGeneratedMeshV1(
        mesh_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"asre:{mesh_hash}")), mesh_hash=mesh_hash,
        design_hash=compiled.design_hash, geometry_fingerprint=compiled.geometry_fingerprint,
        fluid_domain_id=domain.domain_id, source_body_id=domain.source_body_id,
        source_surface_hash=generated.surface.certification.source_surface_hash, resolution=generated.resolution,
        cell_count=metrics.cell_count, face_count=metrics.face_count, internal_face_count=metrics.internal_face_count,
        boundary_face_count=metrics.boundary_face_count, cell_types=cell_types, semantic_patches=updated,
        surface_certification=generated.surface.certification, check_mesh=metrics, geometry_certification=geometry,
        limitations=["One closed single-region internal FLUID CAD volume only", "No turbulence, moving mesh, AMI, CHT, cell zones, or multi-region flow"],
    )
