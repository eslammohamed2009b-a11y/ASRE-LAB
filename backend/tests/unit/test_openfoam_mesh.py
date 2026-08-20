from __future__ import annotations

import os, subprocess
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.meshing import generate_mesh
from app.module2_simulation.meshing import GeneratedMesh
from app.module2_simulation.openfoam_mesh import OpenFOAMMeshError, export_poly_mesh
from tests.integration.test_geometry_physics_foundation import authoritative_box, domain, mesh_spec

def _mesh(*, malicious: bool=False, overlap: bool=False, unmapped: bool=False, nonmanifold: bool=False, degenerate: bool=False):
    points=((0.,0.,0.),(1.,0.,0.),(0.,1.,0.),(0.,0.,1.),(0.,0.,-1.),(0.,0.,2.))
    if degenerate: points=((0.,0.,0.),(1.,0.,0.),(0.,1.,0.),(1.,1.,0.))
    cells=((0,1,2,3),) if degenerate else ((0,1,2,3),(0,2,1,4))
    if nonmanifold: cells=(*cells,(0,1,2,5))
    faces=((0,3,2),(0,1,3),(1,2,3),(0,2,4),(0,4,1),(1,4,2)) if len(cells)>1 else ((0,2,1),(0,1,3),(1,2,3),(2,0,3))
    region="../../bad;{} #codeStream" if malicious else "fluid_boundary"
    ids=list(range(1,len(faces)+(0 if unmapped else 1)))
    mappings=[SimpleNamespace(semantic_region=region,boundary_facet_ids=ids)]
    if overlap: mappings.append(SimpleNamespace(semantic_region="overlap",boundary_facet_ids=[1]))
    metadata=SimpleNamespace(mesh_id="mesh-1",mesh_hash="hash-1",design_hash="design-1",semantic_mappings=mappings)
    return GeneratedMesh(metadata=metadata,nodes_m=points,tetrahedra=cells,boundary_facets=faces), region

def _categories(mesh, region, *, overlap=False):
    result={region:"wall"}
    if overlap: result["overlap"]="wall"
    return result

def test_two_tetra_topology_orientation_determinism_and_hash_changes(tmp_path):
    mesh,region=_mesh(); first=export_poly_mesh(mesh,tmp_path/"a",_categories(mesh,region)); second=export_poly_mesh(mesh,tmp_path/"b",_categories(mesh,region))
    assert (first.cell_count,first.internal_face_count,first.boundary_face_count)==(2,1,6)
    assert first.poly_mesh_hash==second.poly_mesh_hash and first.cell_ordering.endswith("tetrahedron i")
    for name in ("points","faces","owner","neighbour","boundary"):
        assert (tmp_path/"a"/"constant"/"polyMesh"/name).read_bytes()==(tmp_path/"b"/"constant"/"polyMesh"/name).read_bytes()
    changed=GeneratedMesh(metadata=mesh.metadata,nodes_m=tuple((x+0.1,y,z) if index==0 else (x,y,z) for index,(x,y,z) in enumerate(mesh.nodes_m)),tetrahedra=mesh.tetrahedra,boundary_facets=mesh.boundary_facets)
    assert export_poly_mesh(changed,tmp_path/"changed",_categories(mesh,region)).poly_mesh_hash!=first.poly_mesh_hash
    remapped=export_poly_mesh(mesh,tmp_path/"remapped",{region:"inlet"})
    assert remapped.poly_mesh_hash!=first.poly_mesh_hash and remapped.boundary_mapping_hash!=first.boundary_mapping_hash
    single, single_region=_mesh(); single=GeneratedMesh(metadata=single.metadata,nodes_m=single.nodes_m[:4],tetrahedra=(single.tetrahedra[0],),boundary_facets=((0,2,1),(0,1,3),(1,2,3),(2,0,3)))
    single.metadata.semantic_mappings[0].boundary_facet_ids=[1,2,3,4]
    assert export_poly_mesh(single,tmp_path/"single",{single_region:"wall"}).poly_mesh_hash!=first.poly_mesh_hash
    faces=(tmp_path/"a"/"constant"/"polyMesh"/"faces").read_text()
    owners=(tmp_path/"a"/"constant"/"polyMesh"/"owner").read_text()
    neighbours=(tmp_path/"a"/"constant"/"polyMesh"/"neighbour").read_text()
    assert "3(0 2 1)" in faces and "\n0\n" in owners and "\n1\n" in neighbours
    points=np.asarray(mesh.nodes_m)
    face=(0,2,1); normal=np.cross(points[face[1]]-points[face[0]],points[face[2]]-points[face[0]])
    assert np.dot(normal,points[list(face)].mean(axis=0)-points[list(mesh.tetrahedra[0])].mean(axis=0))>0

def test_single_tetra_and_secure_patch_name(tmp_path):
    mesh,region=_mesh(malicious=True); mesh=GeneratedMesh(metadata=mesh.metadata,nodes_m=mesh.nodes_m[:4],tetrahedra=(mesh.tetrahedra[0],),boundary_facets=((0,2,1),(0,1,3),(1,2,3),(2,0,3)))
    mesh.metadata.semantic_mappings[0].boundary_facet_ids=[1,2,3,4]
    result=export_poly_mesh(mesh,tmp_path,{region:"wall"}); content="".join(path.read_text() for path in (tmp_path/"constant"/"polyMesh").iterdir())
    assert result.cell_count==1 and result.internal_face_count==0 and result.boundary_face_count==4
    assert region not in content and "#code" not in content and "dynamicCode" not in content
    assert result.patches[0].semantic_region==region and result.patches[0].patch_name.startswith("asre_wall_")

@pytest.mark.parametrize(("kwargs","code"),[
    ({"overlap":True},"OVERLAPPING_PATCH"),({"unmapped":True},"UNMAPPED_BOUNDARY_FACET"),
    ({"nonmanifold":True},"NON_MANIFOLD_FACE"),({"degenerate":True},"DEGENERATE_CELL")])
def test_invalid_topology_fails_closed(tmp_path,kwargs,code):
    mesh,region=_mesh(**kwargs)
    with pytest.raises(OpenFOAMMeshError) as exc: export_poly_mesh(mesh,tmp_path,_categories(mesh,region,overlap=kwargs.get("overlap",False)))
    assert exc.value.code==code

@pytest.mark.skipif(os.getenv("ASRE_RUN_OPENFOAM_REAL")!="1",reason="explicit real OpenFOAM gate")
def test_real_openfoam_checkmesh_on_exported_asre_mesh(tmp_path):
    mesh=generate_mesh(compile_design(authoritative_box()),[domain()],mesh_spec())
    result=export_poly_mesh(mesh,tmp_path,{"low_end":"inlet","high_end":"outlet","walls":"wall"})
    system=tmp_path/"system"; system.mkdir(); (system/"controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application foamRun; startFrom startTime; startTime 0; stopAt endTime; endTime 1; deltaT 1; "
        "writeControl timeStep; writeInterval 1; writeFormat ascii; runTimeModifiable false;\n",encoding="utf-8")
    completed=subprocess.run(["docker","run","--rm","-v",f"{tmp_path.resolve()}:/case:ro","asre-openfoam14:20260724","bash","-lc",". /opt/openfoam14/etc/bashrc && checkMesh -case /case"],shell=False,capture_output=True,text=True,timeout=120)
    assert completed.returncode==0,completed.stdout+completed.stderr
    assert "Mesh OK" in completed.stdout and f"cells:            {result.cell_count}" in completed.stdout
    assert f"faces:            {result.internal_face_count + result.boundary_face_count}" in completed.stdout
    assert "Failed 0 mesh checks" not in completed.stdout  # v14 success is reported as Mesh OK

@pytest.mark.skipif(os.getenv("ASRE_RUN_OPENFOAM_REAL")!="1",reason="explicit real OpenFOAM gate")
def test_real_openfoam14_runtime_identity():
    command=". /opt/openfoam14/etc/bashrc && dpkg-query -W openfoam14 && foamRun -help"
    completed=subprocess.run(["docker","run","--rm","asre-openfoam14:20260724","bash","-lc",command],
        shell=False,capture_output=True,text=True,timeout=120)
    output=completed.stdout+completed.stderr
    assert completed.returncode==0 and "openfoam14\t20260724" in output
    assert "OpenFOAM-14" in output and "-solver <name>" in output
