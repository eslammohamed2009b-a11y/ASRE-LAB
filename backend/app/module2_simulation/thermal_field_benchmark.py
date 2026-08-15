"""Server-bound analytical benchmarks for authoritative CAD TET4 thermal results."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np

from app.module2_simulation.field_results import load_field_artifact
from app.v2.evidence_models import BenchmarkCaseBinding, EvidenceType
from app.v2.repository import EvidenceRepository

LINEAR_BENCHMARK_ID = "thermal_fem_linear_prism"
QUADRATIC_BENCHMARK_ID = "thermal_fem_uniform_generation_prism"
BENCHMARK_VERSION = "2"
QUADRATURE_RULE_ID = "duffy_gauss_legendre_4x4x4_v1"
QUADRATURE_DEGREE = 4


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def tetra_quadrature_degree4():
    """Positive 4x4x4 Duffy rule on the reference tetrahedron (volume 1/6)."""
    roots, weights = np.polynomial.legendre.leggauss(4)
    roots = (roots + 1.0) / 2.0; weights = weights / 2.0
    for u, wu in zip(roots, weights):
        for v, wv in zip(roots, weights):
            for w, ww in zip(roots, weights):
                yield np.array((u, (1-u)*v, (1-u)*(1-v)*w)), wu*wv*ww*(1-u)**2*(1-v)


def _property(snapshot: dict, name: str) -> float:
    for item in snapshot.get("properties", []):
        if item.get("name") == name:
            value = float(item["value"])
            if math.isfinite(value): return value
    raise ValueError(f"Persisted material snapshot lacks {name}")


def _field_and_evidence(repository, user_id: str, simulation_id: str):
    fields = [item for item in repository.list_field_results(simulation_id) if item.variable_name == "temperature"]
    if len(fields) != 1 or fields[0].user_id != user_id:
        raise ValueError("Exactly one owned persisted temperature field is required")
    field = fields[0]
    records = EvidenceRepository(repository=repository).list_scientific_for_simulation(user_id, simulation_id)
    matches = [item for item in records if item["record_type"] == "scientific_field_result"
               and item["payload"].get("variable_name") == "temperature"
               and item["payload"].get("checksum_sha256") == field.checksum_sha256]
    if len(matches) != 1: raise ValueError("Temperature field evidence is missing or ambiguous")
    return field, matches[0]


def _rectangular_prism(mesh, persisted_geometry: dict) -> None:
    if mesh.metadata.mesh_hash != persisted_geometry.get("mesh_hash") or mesh.metadata.mesh_id != persisted_geometry.get("mesh_id"):
        raise ValueError("Persisted input and authoritative mesh identities differ")
    nodes = np.asarray(mesh.nodes_m, dtype=float); lengths = nodes.max(axis=0)-nodes.min(axis=0)
    if np.any(lengths <= 0) or mesh.metadata.fallback_provenance:
        raise ValueError("Benchmark requires a certified rectangular-prism mesh")
    volume = sum(abs(float(np.linalg.det(np.stack((nodes[t[1]]-nodes[t[0]], nodes[t[2]]-nodes[t[0]], nodes[t[3]]-nodes[t[0]]))) / 6.0)) for t in mesh.tetrahedra)
    if not math.isclose(volume, float(np.prod(lengths)), rel_tol=1e-9, abs_tol=1e-15):
        raise ValueError("Mesh does not certify the full rectangular-prism volume")
    stored = persisted_geometry.get("mesh_geometry", {})
    if (stored.get("node_count") != len(mesh.nodes_m) or stored.get("tetrahedron_count") != len(mesh.tetrahedra)
            or not math.isclose(float(stored.get("element_volume_m3", -1)), volume, rel_tol=1e-12, abs_tol=1e-18)):
        raise ValueError("Persisted mesh geometry summary is inconsistent")


def _region_facets(mesh, tag: str) -> set[int]:
    mapping = next((item for item in mesh.metadata.semantic_mappings if item.semantic_region == tag), None)
    if mapping is None: raise ValueError(f"Boundary condition region '{tag}' is absent from the authoritative mesh")
    return set(mapping.boundary_facet_ids)


def _plane_axis(mesh, facet_ids: set[int]) -> tuple[int, float]:
    ids = {node for facet_id in facet_ids for node in mesh.boundary_facets[facet_id-1]}
    coordinates = np.asarray([mesh.nodes_m[node] for node in ids], dtype=float)
    axes = np.flatnonzero(np.ptp(coordinates, axis=0) <= 1e-12)
    if len(axes) != 1: raise ValueError("Dirichlet benchmark boundary is not one certified planar prism end")
    axis = int(axes[0]); return axis, float(coordinates[0, axis])


def derive_benchmark_case_binding(*, repository, user_id: str, simulation_id: str, mesh,
                                  benchmark_id: str) -> BenchmarkCaseBinding:
    job = repository.get_simulation_job(simulation_id); result = repository.get_simulation_result(simulation_id)
    simulation_input = repository.get_simulation_input(simulation_id)
    if job is None or job.user_id != user_id or result is None or simulation_input is None:
        raise LookupError("Owned persisted FEM simulation state is unavailable")
    if result.solver_id != "thermal_fem_3d_v1" or result.status != "completed":
        raise ValueError("Benchmark requires a completed thermal_fem_3d_v1 result")
    geometry = simulation_input.geometry; fingerprint = _canonical_hash(geometry)
    if fingerprint != result.validation_metadata.get("input_fingerprint"):
        raise ValueError("Persisted FEM input fingerprint is invalid")
    if result.validation_metadata.get("mesh_hash") != mesh.metadata.mesh_hash:
        raise ValueError("Persisted result mesh identity does not match authoritative mesh")
    _rectangular_prism(mesh, geometry)
    bcs = geometry.get("boundary_conditions", []); temperature_bcs = [x for x in bcs if x.get("bc_type") == "temperature"]
    flux_bcs = [x for x in bcs if x.get("bc_type") == "heat_flux"]
    sources = [x for x in bcs if x.get("bc_type") == "volumetric_heat_source"]
    incompatible = [x for x in bcs if x.get("bc_type") not in {"temperature", "heat_flux", "volumetric_heat_source"}]
    if len(temperature_bcs) != 2 or incompatible or any(float(x.get("heat_flux_w_m2", math.nan)) != 0 for x in flux_bcs):
        raise ValueError("Thermal prism benchmark BC eligibility failed")
    temp_facets = [_region_facets(mesh, x["semantic_region"]) for x in temperature_bcs]
    planes = [_plane_axis(mesh, x) for x in temp_facets]
    if planes[0][0] != planes[1][0] or math.isclose(planes[0][1], planes[1][1], abs_tol=1e-12):
        raise ValueError("Temperature boundaries are not opposite prism ends")
    axis = planes[0][0]; order = np.argsort([planes[0][1], planes[1][1]])
    ordered_bcs = [temperature_bcs[int(index)] for index in order]
    coordinate_min, coordinate_max = sorted([planes[0][1], planes[1][1]])
    covered = set().union(*temp_facets, *[_region_facets(mesh, x["semantic_region"]) for x in flux_bcs])
    if covered != set(range(1, len(mesh.boundary_facets)+1)):
        raise ValueError("All remaining prism boundaries must be explicitly zero-flux")
    if benchmark_id == LINEAR_BENCHMARK_ID:
        if sources: raise ValueError("Linear prism benchmark forbids volumetric heat sources")
        derived: dict[str, float | int | str] = {"axis": axis, "coordinate_min_m": coordinate_min,
            "coordinate_max_m": coordinate_max, "length_m": coordinate_max-coordinate_min,
            "temperature_at_min_k": float(ordered_bcs[0]["temperature_k"]),
            "temperature_at_max_k": float(ordered_bcs[1]["temperature_k"])}
    elif benchmark_id == QUADRATIC_BENCHMARK_ID:
        if len(sources) != 1 or sources[0].get("domain_id") is None:
            raise ValueError("Uniform-generation benchmark requires exactly one domain heat source")
        first, second = (float(x["temperature_k"]) for x in ordered_bcs)
        if not math.isclose(first, second, rel_tol=0, abs_tol=1e-12):
            raise ValueError("Uniform-generation benchmark requires equal end temperatures")
        assignments = geometry.get("material_assignments", []); snapshots = geometry.get("material_snapshots", {})
        assignment = next((x for x in assignments if x.get("domain_id") == sources[0]["domain_id"]), None)
        if assignment is None or assignment.get("material_name") not in snapshots:
            raise ValueError("Persisted source-domain material assignment is unavailable")
        derived = {"axis": axis, "coordinate_min_m": coordinate_min, "coordinate_max_m": coordinate_max,
            "length_m": coordinate_max-coordinate_min, "temperature_k": first,
            "source_w_m3": float(sources[0]["heat_source_w_m3"]),
            "conductivity_w_m_k": _property(snapshots[assignment["material_name"]], "thermal_conductivity")}
    else: raise ValueError("Unknown authoritative thermal FEM benchmark case")
    field, field_evidence = _field_and_evidence(repository, user_id, simulation_id)
    if field.grid_metadata.get("mesh_hash") != mesh.metadata.mesh_hash: raise ValueError("Temperature field mesh identity is invalid")
    unsigned = {"schema_version":"1.0", "benchmark_id":benchmark_id, "benchmark_version":BENCHMARK_VERSION,
        "authoritative":True, "eligibility_status":"eligible", "simulation_id":simulation_id,
        "solver_id":result.solver_id, "solver_version":result.solver_version, "input_fingerprint":fingerprint,
        "mesh_id":mesh.metadata.mesh_id, "mesh_hash":mesh.metadata.mesh_hash, "result_hash":result.reproducibility_hash,
        "field_evidence_id":field_evidence["id"], "field_checksum_sha256":field.checksum_sha256,
        "derived_parameters":derived}
    return BenchmarkCaseBinding.model_validate({**unsigned, "binding_hash":_canonical_hash(unsigned)})


def _validate_expected(expected: dict[str, float] | None, derived: dict[str, Any]) -> None:
    for key, value in (expected or {}).items():
        if key not in derived or not math.isclose(float(value), float(derived[key]), rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"Expected benchmark parameter '{key}' contradicts persisted scientific state")


def _temperature_field(repository, storage, user_id, simulation_id, binding, mesh):
    field, _ = _field_and_evidence(repository, user_id, simulation_id)
    if field.checksum_sha256 != binding.field_checksum_sha256: raise ValueError("Temperature field checksum differs from binding")
    values = load_field_artifact(storage, field.storage_object_key, field.checksum_sha256)
    if values.shape != (len(mesh.nodes_m),): raise ValueError("Temperature field shape does not match mesh nodes")
    return values


def linear_prism_field_error(*, repository, storage, user_id: str, simulation_id: str, mesh,
                             cold_k: float | None = None, hot_k: float | None = None,
                             expected_parameters: dict[str, float] | None = None):
    binding = derive_benchmark_case_binding(repository=repository, user_id=user_id, simulation_id=simulation_id,
        mesh=mesh, benchmark_id=LINEAR_BENCHMARK_ID)
    expected = dict(expected_parameters or {})
    if cold_k is not None: expected["temperature_at_min_k"] = cold_k
    if hot_k is not None: expected["temperature_at_max_k"] = hot_k
    _validate_expected(expected, binding.derived_parameters)
    values = _temperature_field(repository, storage, user_id, simulation_id, binding, mesh)
    nodes = np.asarray(mesh.nodes_m); p = binding.derived_parameters; axis = int(p["axis"])
    local = nodes[:,axis]-float(p["coordinate_min_m"]); length = float(p["length_m"])
    reference = float(p["temperature_at_min_k"])+(float(p["temperature_at_max_k"])-float(p["temperature_at_min_k"]))*local/length
    error = values-reference
    return ({"field_checksum_sha256":binding.field_checksum_sha256, "mesh_hash":binding.mesh_hash,
        "node_count":int(values.size), "max_absolute_error_k":float(np.abs(error).max()),
        "normalized_l2_error":float(np.linalg.norm(error)/max(np.linalg.norm(reference),1e-15)),
        "formula":"linear_prism_temperature_v2", "formula_version":BENCHMARK_VERSION}, binding)


def quadratic_prism_field_error(*, repository, storage, user_id: str, simulation_id: str, mesh,
                                temperature_k: float | None = None, source_w_m3: float | None = None,
                                conductivity_w_m_k: float | None = None,
                                expected_parameters: dict[str, float] | None = None):
    binding = derive_benchmark_case_binding(repository=repository, user_id=user_id, simulation_id=simulation_id,
        mesh=mesh, benchmark_id=QUADRATIC_BENCHMARK_ID)
    expected = dict(expected_parameters or {})
    if temperature_k is not None: expected["temperature_k"] = temperature_k
    if source_w_m3 is not None: expected["source_w_m3"] = source_w_m3
    if conductivity_w_m_k is not None: expected["conductivity_w_m_k"] = conductivity_w_m_k
    _validate_expected(expected, binding.derived_parameters)
    values = _temperature_field(repository, storage, user_id, simulation_id, binding, mesh)
    nodes=np.asarray(mesh.nodes_m); p=binding.derived_parameters; axis=int(p["axis"]); x=nodes[:,axis]
    local=x-float(p["coordinate_min_m"]); length=float(p["length_m"]); t0=float(p["temperature_k"])
    source=float(p["source_w_m3"]); conductivity=float(p["conductivity_w_m_k"])
    reference=t0+source/(2*conductivity)*local*(length-local); error=values-reference
    error_squared=reference_squared=0.0
    for tetrahedron in mesh.tetrahedra:
        coordinates=nodes[list(tetrahedron)]; nodal=values[list(tetrahedron)]
        volume=abs(float(np.linalg.det(np.stack((coordinates[1]-coordinates[0],coordinates[2]-coordinates[0],coordinates[3]-coordinates[0])))/6.0))
        for barycentric, reference_weight in tetra_quadrature_degree4():
            shape=np.array((1.0-barycentric.sum(),*barycentric)); point=shape@coordinates
            local_x=point[axis]-float(p["coordinate_min_m"]); exact=t0+source/(2*conductivity)*local_x*(length-local_x)
            difference=float(shape@nodal-exact); physical_weight=6.0*volume*reference_weight
            error_squared+=physical_weight*difference*difference; reference_squared+=physical_weight*exact*exact
    return ({"field_checksum_sha256":binding.field_checksum_sha256,"mesh_hash":binding.mesh_hash,"node_count":int(values.size),
        "max_nodal_absolute_error_k":float(np.abs(error).max()),
        "absolute_integrated_l2_error_k_sqrt_m3":float(np.sqrt(error_squared)),
        "normalized_l2_error":float(np.sqrt(error_squared/max(reference_squared,1e-30))),
        "quadrature_rule":QUADRATURE_RULE_ID,"quadrature_degree":QUADRATURE_DEGREE,"element_count":len(mesh.tetrahedra),
        "formula":"quadratic_prism_temperature_v2","formula_version":BENCHMARK_VERSION},binding)


def _persist(*, repository,user_id,simulation_id,details,binding,tolerance,absolute_error,limitations):
    job=repository.get_simulation_job(simulation_id); result=repository.get_simulation_result(simulation_id)
    evidence=EvidenceRepository(repository=repository)
    source_ids=[x["id"] for x in evidence.list_scientific_for_simulation(user_id,simulation_id)
                if x["record_type"]=="scientific_numerical_result"]+[binding.field_evidence_id]
    passed=details["normalized_l2_error"]<=tolerance
    return evidence.create_scientific_evidence(user_id,{"evidence_type":EvidenceType.BENCHMARK.value,
        "experiment_id":job.experiment_id,"design_id":job.design_id,"simulation_id":simulation_id,
        "solver_id":result.solver_id,"solver_version":result.solver_version,"input_fingerprint":binding.input_fingerprint,
        "result_hash":binding.result_hash,"source_ids":source_ids,"status":"pass" if passed else "fail",
        "benchmark_id":binding.benchmark_id,"metric_name":"normalized_l2_error","computed_value":details["normalized_l2_error"],
        "reference_value":0.0,"absolute_error":absolute_error,"relative_error":details["normalized_l2_error"],
        "tolerance":tolerance,"passed":passed,"source_simulation_id":simulation_id,"benchmark_details":details,
        "case_binding":binding.model_dump(mode="json"),"limitations":limitations})


def persist_linear_prism_benchmark(*,repository,storage,user_id,simulation_id,mesh,cold_k=None,hot_k=None,
                                   expected_parameters=None,tolerance=1e-8):
    details,binding=linear_prism_field_error(repository=repository,storage=storage,user_id=user_id,
        simulation_id=simulation_id,mesh=mesh,cold_k=cold_k,hot_k=hot_k,expected_parameters=expected_parameters)
    return _persist(repository=repository,user_id=user_id,simulation_id=simulation_id,details=details,binding=binding,
        tolerance=tolerance,absolute_error=details["max_absolute_error_k"],
        limitations=["Server-recognized opposite-Dirichlet, zero-flux rectangular prism only."])


def persist_quadratic_prism_benchmark(*,repository,storage,user_id,simulation_id,mesh,temperature_k=None,
                                      source_w_m3=None,conductivity_w_m_k=None,expected_parameters=None,tolerance=1e-2):
    details,binding=quadratic_prism_field_error(repository=repository,storage=storage,user_id=user_id,
        simulation_id=simulation_id,mesh=mesh,temperature_k=temperature_k,source_w_m3=source_w_m3,
        conductivity_w_m_k=conductivity_w_m_k,expected_parameters=expected_parameters)
    return _persist(repository=repository,user_id=user_id,simulation_id=simulation_id,details=details,binding=binding,
        tolerance=tolerance,absolute_error=details["absolute_integrated_l2_error_k_sqrt_m3"],
        limitations=["Server-recognized equal-end-temperature, zero-flux, uniform-source rectangular prism only."])


def validate_persisted_binding(model, repository, user_id: str) -> bool:
    binding=model.case_binding
    if binding is None or binding.benchmark_id!=model.benchmark_id or binding.simulation_id!=model.simulation_id:return False
    if _canonical_hash(binding.model_dump(mode="json",exclude={"binding_hash"}))!=binding.binding_hash:return False
    job=repository.get_simulation_job(binding.simulation_id); result=repository.get_simulation_result(binding.simulation_id)
    simulation_input=repository.get_simulation_input(binding.simulation_id)
    if job is None or job.user_id!=user_id or result is None or simulation_input is None:return False
    if (_canonical_hash(simulation_input.geometry)!=binding.input_fingerprint or result.reproducibility_hash!=binding.result_hash
        or result.solver_id!=binding.solver_id or result.solver_version!=binding.solver_version
        or result.validation_metadata.get("mesh_hash")!=binding.mesh_hash or simulation_input.geometry.get("mesh_id")!=binding.mesh_id
        or model.input_fingerprint!=binding.input_fingerprint or model.result_hash!=binding.result_hash):return False
    fields=[x for x in repository.list_field_results(binding.simulation_id) if x.variable_name=="temperature" and x.user_id==user_id]
    dependency=EvidenceRepository(repository=repository).get(binding.field_evidence_id,user_id)
    if (len(fields)!=1 or fields[0].checksum_sha256!=binding.field_checksum_sha256 or binding.field_evidence_id not in model.source_ids
        or dependency is None or dependency["record_type"]!="scientific_field_result"
        or dependency["payload"].get("checksum_sha256")!=binding.field_checksum_sha256):return False
    bcs=simulation_input.geometry.get("boundary_conditions",[]); p=binding.derived_parameters
    temps=sorted(float(x["temperature_k"]) for x in bcs if x.get("bc_type")=="temperature")
    fluxes=[x for x in bcs if x.get("bc_type")=="heat_flux"]
    incompatible=[x for x in bcs if x.get("bc_type") not in {"temperature","heat_flux","volumetric_heat_source"}]
    if incompatible or any(float(x.get("heat_flux_w_m2",math.nan))!=0 for x in fluxes):return False
    mesh_geometry=simulation_input.geometry.get("mesh_geometry",{}); axis=int(p.get("axis",-1))
    low=mesh_geometry.get("bounds_min_m",[]); high=mesh_geometry.get("bounds_max_m",[])
    if (axis not in (0,1,2) or len(low)!=3 or len(high)!=3
        or not math.isclose(float(p["coordinate_min_m"]),float(low[axis]),abs_tol=1e-12)
        or not math.isclose(float(p["coordinate_max_m"]),float(high[axis]),abs_tol=1e-12)
        or not math.isclose(float(p["length_m"]),float(high[axis])-float(low[axis]),abs_tol=1e-12)):return False
    dependencies=[EvidenceRepository(repository=repository).get(source_id,user_id) for source_id in model.source_ids]
    if not any(item and item["record_type"]=="scientific_numerical_result" for item in dependencies):return False
    if binding.benchmark_id==LINEAR_BENCHMARK_ID:
        if (len(temps)!=2 or any(x.get("bc_type")=="volumetric_heat_source" for x in bcs)
            or sorted([float(p["temperature_at_min_k"]),float(p["temperature_at_max_k"])])!=temps):return False
    elif binding.benchmark_id==QUADRATIC_BENCHMARK_ID:
        sources=[x for x in bcs if x.get("bc_type")=="volumetric_heat_source"]
        if len(temps)!=2 or len(sources)!=1 or not math.isclose(temps[0],temps[1],abs_tol=1e-12):return False
        assignments=simulation_input.geometry.get("material_assignments",[]); snapshots=simulation_input.geometry.get("material_snapshots",{})
        assignment=next((x for x in assignments if x.get("domain_id")==sources[0].get("domain_id")),None)
        if assignment is None or not math.isclose(float(p["source_w_m3"]),float(sources[0]["heat_source_w_m3"]),rel_tol=1e-12):return False
        if not math.isclose(float(p["conductivity_w_m_k"]),_property(snapshots[assignment["material_name"]],"thermal_conductivity"),rel_tol=1e-12):return False
    else:return False
    return True
