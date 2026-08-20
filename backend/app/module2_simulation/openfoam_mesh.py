"""Deterministic authoritative ASRE TET4 to OpenFOAM polyMesh export."""
from __future__ import annotations

import hashlib, json, re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
import numpy as np
from app.module2_simulation.meshing import GeneratedMesh

EXPORTER_VERSION = "asre-openfoam-polymesh-1.0"
PatchCategory = Literal["inlet", "outlet", "wall", "symmetry"]
_CELL_FACES = ((0,2,1),(0,1,3),(1,2,3),(2,0,3))
_FORBIDDEN = ("#code","#codeStream","#codeDict","dynamicCode","codedFixedValue","#include","libs")

class OpenFOAMMeshError(ValueError):
    def __init__(self, code: str, message: str): super().__init__(message); self.code=code

@dataclass(frozen=True)
class BoundaryPatchProvenance:
    semantic_region: str; patch_name: str; category: PatchCategory; boundary_facet_ids: tuple[int,...]

@dataclass(frozen=True)
class PolyMeshExport:
    source_mesh_id: str; source_mesh_hash: str; source_design_hash: str
    poly_mesh_hash: str; cell_mapping_hash: str; boundary_mapping_hash: str
    exporter_version: str; cell_count: int; internal_face_count: int; boundary_face_count: int
    cell_ordering: str; patches: tuple[BoundaryPatchProvenance,...]

def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def _header(class_name: str, object_name: str) -> str:
    return f'FoamFile\n{{\n    version 2.0;\n    format ascii;\n    class {class_name};\n    location "constant/polyMesh";\n    object {object_name};\n}}\n'

def _patch_name(identity: str, category: PatchCategory) -> str:
    name=f"asre_{category}_{hashlib.sha256(identity.encode()).hexdigest()[:12]}"
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}",name): raise OpenFOAMMeshError("UNSAFE_PATCH_NAME","Generated patch name is unsafe")
    return name

def _orient(face: tuple[int,int,int], points: np.ndarray, direction: np.ndarray) -> tuple[int,int,int]:
    a,b,c=(points[index] for index in face); normal=np.cross(b-a,c-a); magnitude=float(np.linalg.norm(normal))
    if not np.isfinite(magnitude) or magnitude <= 1e-15: raise OpenFOAMMeshError("DEGENERATE_FACE","Face has zero area")
    dot=float(np.dot(normal,direction))
    if abs(dot) <= 1e-18: raise OpenFOAMMeshError("AMBIGUOUS_FACE_ORIENTATION","Face orientation is ambiguous")
    return face if dot > 0 else (face[0],face[2],face[1])

def export_poly_mesh(mesh: GeneratedMesh, destination: Path, semantic_categories: dict[str,PatchCategory]) -> PolyMeshExport:
    points=np.asarray(mesh.nodes_m,dtype=np.float64)
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all(): raise OpenFOAMMeshError("INVALID_POINTS","Points must be finite SI xyz")
    cells=[tuple(map(int,tet)) for tet in mesh.tetrahedra]
    if len({tuple(sorted(cell)) for cell in cells})!=len(cells): raise OpenFOAMMeshError("DUPLICATE_CELL","Duplicate cells are forbidden")
    centroids=[]; occurrences: dict[tuple[int,int,int],list[tuple[int,tuple[int,int,int]]]]={}
    for cell_id,cell in enumerate(cells):
        if len(cell)!=4 or len(set(cell))!=4 or any(node<0 or node>=len(points) for node in cell): raise OpenFOAMMeshError("INVALID_CELL","Invalid TET4 connectivity")
        vertices=points[list(cell)]; volume6=float(abs(np.linalg.det(np.stack((vertices[1]-vertices[0],vertices[2]-vertices[0],vertices[3]-vertices[0])))))
        if not np.isfinite(volume6) or volume6<=1e-18: raise OpenFOAMMeshError("DEGENERATE_CELL","Cell has zero or near-zero volume")
        centroids.append(vertices.mean(axis=0))
        for local in _CELL_FACES:
            face=tuple(cell[index] for index in local); occurrences.setdefault(tuple(sorted(face)),[]).append((cell_id,face))
    if any(len(items)>2 for items in occurrences.values()): raise OpenFOAMMeshError("NON_MANIFOLD_FACE","Face is shared by more than two cells")
    exterior={key for key,items in occurrences.items() if len(items)==1}
    boundary={index:tuple(sorted(face)) for index,face in enumerate(mesh.boundary_facets,start=1)}
    if len(set(boundary.values()))!=len(boundary) or set(boundary.values())!=exterior: raise OpenFOAMMeshError("BOUNDARY_TOPOLOGY_MISMATCH","Boundary facets do not exactly match exterior faces")
    known={mapping.semantic_region for mapping in mesh.metadata.semantic_mappings}
    if set(semantic_categories)!=known: raise OpenFOAMMeshError("PATCH_CATEGORY_MISMATCH","Every and only semantic regions require categories")
    facet_patch={}; records=[]
    for mapping in sorted(mesh.metadata.semantic_mappings,key=lambda item:item.semantic_region):
        category=semantic_categories[mapping.semantic_region]; name=_patch_name(mapping.semantic_region,category); ids=tuple(sorted(mapping.boundary_facet_ids))
        if not ids: raise OpenFOAMMeshError("EMPTY_PATCH","Patch cannot be empty")
        for facet_id in ids:
            if facet_id not in boundary: raise OpenFOAMMeshError("PATCH_REFERENCES_NON_BOUNDARY","Patch references non-boundary face")
            if facet_id in facet_patch: raise OpenFOAMMeshError("OVERLAPPING_PATCH","Boundary face belongs to multiple patches")
            facet_patch[facet_id]=name
        records.append(BoundaryPatchProvenance(mapping.semantic_region,name,category,ids))
    if set(facet_patch)!=set(boundary): raise OpenFOAMMeshError("UNMAPPED_BOUNDARY_FACET","Every exterior face requires exactly one patch")
    patch_by_key={boundary[facet_id]:name for facet_id,name in facet_patch.items()}; internal=[]; external=[]
    for key in sorted(occurrences):
        items=occurrences[key]
        if len(items)==2:
            (owner,raw),(neighbour,_)=sorted(items,key=lambda item:item[0]); internal.append((_orient(raw,points,centroids[neighbour]-centroids[owner]),owner,neighbour))
        else:
            owner,raw=items[0]; external.append((patch_by_key[key],_orient(raw,points,points[list(raw)].mean(axis=0)-centroids[owner]),owner))
    external.sort(key=lambda item:(item[0],tuple(sorted(item[1])))); faces=[item[0] for item in internal]+[item[1] for item in external]; owners=[item[1] for item in internal]+[item[2] for item in external]; neighbours=[item[2] for item in internal]
    poly=destination/"constant"/"polyMesh"; poly.mkdir(parents=True,exist_ok=True)
    files={
      "points":_header("vectorField","points")+f"{len(points)}\n(\n"+"\n".join(f"({x:.17g} {y:.17g} {z:.17g})" for x,y,z in points)+"\n)\n",
      "faces":_header("faceList","faces")+f"{len(faces)}\n(\n"+"\n".join(f"3({a} {b} {c})" for a,b,c in faces)+"\n)\n",
      "owner":_header("labelList","owner")+f"{len(owners)}\n(\n"+"\n".join(map(str,owners))+"\n)\n",
      "neighbour":_header("labelList","neighbour")+f"{len(neighbours)}\n(\n"+"\n".join(map(str,neighbours))+"\n)\n"}
    offset=len(internal); blocks=[]
    for record in sorted(records,key=lambda item:item.patch_name):
        count=sum(item[0]==record.patch_name for item in external); foam_type={"wall":"wall","symmetry":"symmetryPlane","inlet":"patch","outlet":"patch"}[record.category]
        blocks.append(f"{record.patch_name}\n{{\n    type {foam_type};\n    nFaces {count};\n    startFace {offset};\n}}"); offset+=count
    files["boundary"]=_header("polyBoundaryMesh","boundary")+f"{len(blocks)}\n(\n"+"\n".join(blocks)+"\n)\n"
    if any(token in content for content in files.values() for token in _FORBIDDEN): raise OpenFOAMMeshError("DYNAMIC_CODE_FORBIDDEN","Forbidden directive generated")
    for name,content in files.items(): (poly/name).write_text(content,encoding="utf-8",newline="\n")
    canonical={"exporter":EXPORTER_VERSION,"points":points.tolist(),"cells":cells,"faces":faces,"owners":owners,"neighbours":neighbours,"patches":[asdict(record) for record in records]}
    return PolyMeshExport(mesh.metadata.mesh_id,mesh.metadata.mesh_hash,mesh.metadata.design_hash,_digest(canonical),_digest({"ordering":"asre_tetrahedron_index","count":len(cells)}),_digest([asdict(record) for record in records]),EXPORTER_VERSION,len(cells),len(internal),len(external),"OpenFOAM cell i equals ASRE tetrahedron i",tuple(records))
