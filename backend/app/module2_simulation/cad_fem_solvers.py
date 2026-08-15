"""Genuine sparse TET4 solvers consuming Phase 3A GeneratedMesh/PhysicsModelV1."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import numpy as np
from scipy import linalg, sparse
from scipy.sparse import linalg as sparse_linalg

from app.module2_simulation.fem_core import (
    FEMError, MAX_STRUCTURAL_DOFS, apply_dirichlet, assemble_sparse, consistent_tet4_mass,
    expand_dirichlet, isotropic_elasticity_matrix, structural_b_matrix, structural_tet4_matrix,
    tet4_geometry, thermal_tet4_matrix, thermal_volume_load, triangle_area_and_outward_normal,
    triangle_convection_matrix, triangle_scalar_load, validate_mesh_arrays,
)
from app.module2_simulation.geometry_physics_schemas import PhysicsModelV1
from app.module2_simulation.meshing import GeneratedMesh


@dataclass(frozen=True)
class FEMSolution:
    solver_id: str
    summary: dict[str, float]
    fields: dict[str, tuple[str, np.ndarray]]
    diagnostics: dict[str, float | str | int]
    warnings: tuple[str, ...] = ()


# Authoritative execution map intentionally separate from legacy
# EngineeringSolver classes: these functions require PhysicsModelV1 plus a
# GeneratedMesh and will never accept/reconstruct scalar legacy geometry.
CAD_FEM_SOLVERS = {
    "thermal_fem_3d_v1": "solve_thermal_fem_3d",
    "structural_linear_elasticity_3d_v1": "solve_structural_fem_3d",
    "modal_fem_3d_v1": "solve_modal_fem_3d",
}


def _verify(mesh: GeneratedMesh, model: PhysicsModelV1, family: str) -> None:
    if model.mesh_hash != mesh.metadata.mesh_hash or model.mesh_id != mesh.metadata.mesh_id:
        raise FEMError("MESH_PHYSICS_MISMATCH", "PhysicsModel mesh identity does not match the supplied authoritative mesh")
    if model.design_hash != mesh.metadata.design_hash:
        raise FEMError("DESIGN_MESH_MISMATCH", "PhysicsModel design identity does not match the authoritative mesh")
    if model.analysis_family.value != family:
        raise FEMError("SOLVER_FAMILY_MISMATCH", "PhysicsModel is incompatible with the requested FEM solver")
    validate_mesh_arrays(np.asarray(mesh.nodes_m), mesh.tetrahedra)


def _property(model: PhysicsModelV1, domain_id: str, name: str) -> float:
    assignment = next((item for item in model.material_assignments if item.domain_id == domain_id), None)
    if assignment is None:
        raise FEMError("MATERIAL_ASSIGNMENT_MISSING", f"No material assigned to domain '{domain_id}'")
    snapshot = next((item for item in model.materials if item.material_name == assignment.material_name.lower().strip()), None)
    if snapshot is None:
        raise FEMError("MATERIAL_SNAPSHOT_MISSING", "Immutable material snapshot is missing")
    property_ = next((item for item in snapshot.properties if item.name == name), None)
    if property_ is None:
        raise FEMError("MATERIAL_PROPERTY_MISSING", f"Material property '{name}' is required")
    return property_.value


def _element_domains(mesh: GeneratedMesh) -> dict[int, str]:
    return {element_id - 1: mapping.domain_id for mapping in mesh.metadata.domains for element_id in mapping.volume_element_ids}


def _facet_opposites(mesh: GeneratedMesh) -> dict[int, int]:
    result: dict[int, int] = {}
    for facet_id, facet in enumerate(mesh.boundary_facets, start=1):
        key = set(facet)
        matches = [tet for tet in mesh.tetrahedra if key.issubset(tet)]
        if len(matches) != 1:
            raise FEMError("INVALID_BOUNDARY_FACET", "Boundary facet must belong to exactly one TET4")
        result[facet_id] = next(node for node in matches[0] if node not in key)
    return result


def _facets_by_semantic(mesh: GeneratedMesh) -> dict[str, list[int]]:
    return {item.semantic_region: item.boundary_facet_ids for item in mesh.metadata.semantic_mappings}


def solve_thermal_fem_3d(mesh: GeneratedMesh, model: PhysicsModelV1) -> FEMSolution:
    _verify(mesh, model, "thermal")
    started = perf_counter(); nodes = np.asarray(mesh.nodes_m); count = len(nodes)
    domains = _element_domains(mesh); entries = []; load = np.zeros(count); source_total = 0.0
    for element_id, tet in enumerate(mesh.tetrahedra):
        geometry = tet4_geometry(nodes, tet); conductivity = _property(model, domains[element_id], "thermal_conductivity")
        entries.append((np.asarray(tet), thermal_tet4_matrix(geometry, conductivity)))
    matrix = assemble_sparse(count, entries).tolil(); facets = _facets_by_semantic(mesh); opposites = _facet_opposites(mesh)
    prescribed: dict[int, float] = {}; convection_loss = 0.0; flux_total = 0.0
    for bc in model.boundary_conditions:
        if bc.bc_type == "volumetric_heat_source":
            for element_id, tet in enumerate(mesh.tetrahedra):
                if domains[element_id] == bc.domain_id:
                    geometry = tet4_geometry(nodes, tet); contribution = thermal_volume_load(geometry, bc.heat_source_w_m3)
                    load[list(tet)] += contribution; source_total += float(contribution.sum())
            continue
        for facet_id in facets.get(bc.semantic_region, []):
            facet = mesh.boundary_facets[facet_id - 1]; area, _ = triangle_area_and_outward_normal(nodes, facet, opposites[facet_id])
            if bc.bc_type == "temperature":
                prescribed.update({node: bc.temperature_k for node in facet})
            elif bc.bc_type == "heat_flux":
                contribution = triangle_scalar_load(area, bc.heat_flux_w_m2); load[list(facet)] += contribution; flux_total += float(contribution.sum())
            elif bc.bc_type == "convection":
                local = triangle_convection_matrix(area, bc.coefficient_w_m2_k)
                matrix[np.ix_(facet, facet)] += local
                load[list(facet)] += bc.coefficient_w_m2_k * bc.ambient_temperature_k * area / 3.0
    matrix = matrix.tocsr(); reduced, rhs, free = apply_dirichlet(matrix, load, prescribed)
    try:
        solution = sparse_linalg.spsolve(reduced, rhs)
    except Exception as exc:
        raise FEMError("THERMAL_SOLVE_FAILED", "Sparse thermal solve failed") from exc
    if not np.isfinite(solution).all(): raise FEMError("NONFINITE_RESULT", "Thermal solve returned non-finite values")
    temperature = expand_dirichlet(count, free, solution, prescribed); residual = matrix @ temperature - load
    for bc in model.boundary_conditions:
        if bc.bc_type == "convection":
            for facet_id in facets.get(bc.semantic_region, []):
                facet = mesh.boundary_facets[facet_id - 1]; area, _ = triangle_area_and_outward_normal(nodes, facet, opposites[facet_id])
                convection_loss += bc.coefficient_w_m2_k * area * (float(temperature[list(facet)].mean()) - bc.ambient_temperature_k)
    reaction = float(sum(residual[dof] for dof in prescribed)); applied = source_total + flux_total
    balance = applied - convection_loss + reaction
    gradients = []
    for tet in mesh.tetrahedra:
        geometry = tet4_geometry(nodes, tet); gradients.append(geometry.gradients_m_inv.T @ temperature[list(tet)])
    gradient_array = np.asarray(gradients)
    scale = max(abs(applied), abs(convection_loss), abs(reaction), 1.0)
    heat_flux = np.asarray([-_property(model, domains[element_id], "thermal_conductivity") * gradient for element_id, gradient in enumerate(gradient_array)])
    return FEMSolution("thermal_fem_3d_v1", {
        "min_temperature_k": float(temperature.min()), "max_temperature_k": float(temperature.max()),
        "average_temperature_k": float(temperature.mean()), "temperature_k": float(temperature.mean()), "maximum_temperature_gradient_k_m": float(np.linalg.norm(gradient_array, axis=1).max()),
        "total_applied_heat_w": applied, "convective_boundary_heat_flow_w": convection_loss,
        "energy_balance_error": abs(balance) / scale,
    }, {"temperature": ("K", temperature), "temperature_gradient": ("K/m", gradient_array), "heat_flux": ("W/m2", heat_flux)}, {
        "dof_count": count, "nonzero_count": int(matrix.nnz), "solver_method": "scipy.sparse.linalg.spsolve",
        "algebraic_residual": float(np.linalg.norm(residual[free]) / max(np.linalg.norm(load[free]), 1.0)),
        "energy_balance_w": balance, "solve_time_seconds": perf_counter() - started,
    })


def _structural_system(mesh: GeneratedMesh, model: PhysicsModelV1) -> tuple[sparse.csr_matrix, sparse.csr_matrix, np.ndarray, dict[int, float], dict[str, list[int]]]:
    nodes = np.asarray(mesh.nodes_m); dofs = 3 * len(nodes)
    if dofs > MAX_STRUCTURAL_DOFS: raise FEMError("RESOURCE_LIMIT", "Structural DOFs exceed bounded FEM envelope")
    domains = _element_domains(mesh); stiffness_entries = []; mass_entries = []; load = np.zeros(dofs)
    for element_id, tet in enumerate(mesh.tetrahedra):
        geometry = tet4_geometry(nodes, tet); elasticity = isotropic_elasticity_matrix(_property(model, domains[element_id], "elastic_modulus"), _property(model, domains[element_id], "poisson_ratio"))
        dof_ids = np.asarray([3 * node + component for node in tet for component in range(3)])
        stiffness_entries.append((dof_ids, structural_tet4_matrix(geometry, elasticity)))
        mass_entries.append((dof_ids, consistent_tet4_mass(geometry, _property(model, domains[element_id], "density"))))
    stiffness = assemble_sparse(dofs, stiffness_entries); mass = assemble_sparse(dofs, mass_entries); prescribed: dict[int, float] = {}
    facets = _facets_by_semantic(mesh); opposites = _facet_opposites(mesh)
    for bc in model.boundary_conditions:
        if bc.bc_type == "gravity":
            for element_id, tet in enumerate(mesh.tetrahedra):
                if domains[element_id] == bc.domain_id:
                    weight = _property(model, domains[element_id], "density") * tet4_geometry(nodes, tet).volume_m3 / 4.0
                    for node in tet: load[3 * node:3 * node + 3] += weight * np.asarray(bc.acceleration_m_s2)
            continue
        target_facets = facets.get(getattr(bc, "semantic_region", ""), [])
        target_area = 0.0
        if bc.bc_type == "force":
            target_area = sum(
                triangle_area_and_outward_normal(nodes, mesh.boundary_facets[item - 1], opposites[item])[0]
                for item in target_facets
            )
            if target_area <= 0:
                raise FEMError("EMPTY_LOAD_SURFACE", "Surface force requires a nonempty positive-area semantic surface")
        for facet_id in target_facets:
            facet = mesh.boundary_facets[facet_id - 1]; area, normal = triangle_area_and_outward_normal(nodes, facet, opposites[facet_id])
            if bc.bc_type == "fixed_support":
                for node in facet:
                    prescribed.update({3 * node: 0.0, 3 * node + 1: 0.0, 3 * node + 2: 0.0})
            elif bc.bc_type == "displacement":
                for node in facet:
                    for component, value in enumerate(bc.displacement_m):
                        if value is not None: prescribed[3 * node + component] = value
            elif bc.bc_type == "force":
                # `force_n` is the total resultant, distributed over the
                # selected semantic surface by actual triangle area.
                for node in facet: load[3 * node:3 * node + 3] += np.asarray(bc.force_n) * area / target_area / 3.0
            elif bc.bc_type == "pressure":
                for node in facet: load[3 * node:3 * node + 3] += bc.pressure_pa * normal * area / 3.0
    return stiffness, mass, load, prescribed, facets


def solve_structural_fem_3d(mesh: GeneratedMesh, model: PhysicsModelV1) -> FEMSolution:
    _verify(mesh, model, "structural"); started = perf_counter(); nodes = np.asarray(mesh.nodes_m)
    stiffness, _mass, load, prescribed, _facets = _structural_system(mesh, model)
    reduced, rhs, free = apply_dirichlet(stiffness, load, prescribed)
    try: solved = sparse_linalg.spsolve(reduced, rhs)
    except Exception as exc: raise FEMError("STRUCTURAL_SOLVE_FAILED", "Sparse structural solve failed; check constraints") from exc
    if not np.isfinite(solved).all(): raise FEMError("NONFINITE_RESULT", "Structural solve returned non-finite values")
    displacement = expand_dirichlet(len(load), free, solved, prescribed); residual = stiffness @ displacement - load
    domains = _element_domains(mesh); strain = []; stress = []; von_mises = []
    for element_id, tet in enumerate(mesh.tetrahedra):
        geometry = tet4_geometry(nodes, tet); b = structural_b_matrix(geometry); local = np.asarray([displacement[3 * node:3 * node + 3] for node in tet]).ravel()
        epsilon = b @ local; sigma = isotropic_elasticity_matrix(_property(model, domains[element_id], "elastic_modulus"), _property(model, domains[element_id], "poisson_ratio")) @ epsilon
        strain.append(epsilon); stress.append(sigma); sx, sy, sz, txy, tyz, tzx = sigma
        von_mises.append(float(np.sqrt(0.5*((sx-sy)**2+(sy-sz)**2+(sz-sx)**2)+3*(txy*txy+tyz*tyz+tzx*tzx))))
    reaction = np.zeros_like(residual); reaction[list(prescribed)] = residual[list(prescribed)]
    equilibrium = np.linalg.norm(reaction.reshape(-1, 3).sum(axis=0) + load.reshape(-1, 3).sum(axis=0)) / max(np.linalg.norm(load.reshape(-1, 3).sum(axis=0)), 1.0)
    summary = {"max_displacement_m": float(np.linalg.norm(displacement.reshape(-1, 3), axis=1).max()), "displacement_m": float(np.linalg.norm(displacement.reshape(-1, 3), axis=1).max()), "max_von_mises_stress_pa": max(von_mises), "strain_energy_j": float(0.5 * displacement @ (stiffness @ displacement)), "equilibrium_residual": float(equilibrium)}
    yields = []
    for assignment in model.material_assignments:
        try: yields.append(_property(model, assignment.domain_id, "yield_strength"))
        except FEMError: pass
    diagnostics = {"dof_count": len(load), "nonzero_count": int(stiffness.nnz), "solver_method": "scipy.sparse.linalg.spsolve", "algebraic_residual": float(np.linalg.norm(residual[free]) / max(np.linalg.norm(load[free]), 1.0)), "equilibrium_residual": float(equilibrium), "solve_time_seconds": perf_counter() - started}
    warnings: tuple[str, ...] = ()
    max_stress = max(von_mises)
    if yields and max_stress > 1e-12:
        summary["factor_of_safety"] = min(yields) / max_stress
    elif yields:
        diagnostics["factor_of_safety_applicability"] = "no nonzero stress; finite FOS not applicable"
        warnings = ("No nonzero stress; finite factor of safety is not applicable.",)
    return FEMSolution("structural_linear_elasticity_3d_v1", summary, {"displacement": ("m", displacement.reshape(-1, 3)), "strain": ("dimensionless", np.asarray(strain)), "stress": ("Pa", np.asarray(stress)), "von_mises_stress": ("Pa", np.asarray(von_mises))}, diagnostics, warnings)


def solve_modal_fem_3d(mesh: GeneratedMesh, model: PhysicsModelV1) -> FEMSolution:
    _verify(mesh, model, "modal"); started = perf_counter(); stiffness, mass, _load, prescribed, _facets = _structural_system(mesh, model)
    if not prescribed: raise FEMError("UNCONSTRAINED_MODAL_SYSTEM", "Modal FEM requires explicit support constraints; rigid-body modes are not removed silently")
    free = np.setdiff1d(np.arange(stiffness.shape[0]), np.fromiter(prescribed, dtype=int)); kff = stiffness[free][:, free]; mff = mass[free][:, free]
    requested = model.numerical_settings.requested_modes
    if len(free) <= requested: raise FEMError("INSUFFICIENT_MODAL_DOFS", "Requested modes must be fewer than free degrees of freedom")
    try:
        if len(free) <= 160:
            values, vectors = linalg.eigh(kff.toarray(), mff.toarray(), subset_by_index=[0, requested - 1])
        else:
            values, vectors = sparse_linalg.eigsh(kff, k=requested, M=mff, sigma=0.0, which="LM")
    except Exception as exc: raise FEMError("EIGENSOLVER_FAILED", "Generalized sparse modal eigensolve failed") from exc
    order = np.argsort(values); values = values[order]; vectors = vectors[:, order]
    if np.any(values <= 0): raise FEMError("RIGID_BODY_OR_UNSTABLE_MODE", "Non-positive eigenvalue detected; constraints are insufficient or system is unstable")
    modes = np.zeros((requested, stiffness.shape[0])); residuals = []
    for index, value in enumerate(values):
        vector = vectors[:, index]; vector /= np.sqrt(float(vector.T @ (mff @ vector)))
        modes[index, free] = vector
        residuals.append(float(np.linalg.norm(kff @ vector - value * (mff @ vector)) / max(np.linalg.norm(kff @ vector), 1.0)))
    frequencies = np.sqrt(values) / (2 * np.pi)
    return FEMSolution("modal_fem_3d_v1", {"first_natural_frequency_hz": float(frequencies[0]), "frequency_hz": float(frequencies[0]), "maximum_natural_frequency_hz": float(frequencies[-1])}, {"mode_shapes": ("kg^-1/2", modes.reshape(requested, -1, 3)), "natural_frequencies": ("Hz", frequencies), "eigenvalues": ("rad2/s2", values)}, {"dof_count": stiffness.shape[0], "nonzero_count": int(stiffness.nnz), "solver_method": "scipy.sparse.linalg.eigsh/eigh", "maximum_eigenpair_residual": max(residuals), "normalization": "phi^T M phi = 1", "mode_shape_quantity": "mass_normalized_mode_shape", "solve_time_seconds": perf_counter() - started})
