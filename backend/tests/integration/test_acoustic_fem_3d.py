from __future__ import annotations

import json
import math

import numpy as np
import pytest
from pydantic import ValidationError
from scipy import sparse
from scipy.sparse import linalg as sparse_linalg

from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.acoustic_benchmark import (
    AcousticBenchmarkResultV1,
    BENCHMARK_ID,
    COMPLEX_L2_ERROR_LIMIT,
    benchmark_design,
    authoritative_speed_of_sound,
    analytical_plane_wave_pressure,
    benchmark_model,
    benchmark_mesh,
    run_server_owned_benchmark,
    run_server_owned_refinement,
    validate_benchmark_identity,
)
from app.module2_simulation.acoustic_fem_solvers import (
    AcousticFEMError,
    MIN_RECIPROCAL_CONDITION_ESTIMATE,
    _assemble,
    impedance_boundary_matrix,
    solve_acoustic_fem_3d,
)
from app.module2_simulation.fem_core import tet4_geometry
from app.module2_simulation.geometry_physics_schemas import AcousticWallBC, DomainKind, HarmonicAcousticSettings


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def compiled():
    return compile_design(benchmark_design())


@pytest.fixture(scope="module")
def mesh(compiled):
    return benchmark_mesh(compiled)


@pytest.fixture(scope="module")
def model(mesh):
    return benchmark_model(mesh)


@pytest.fixture(scope="module")
def benchmark_run():
    return run_server_owned_benchmark()


def test_real_acoustic_3d_plane_wave_accuracy(compiled, mesh, model, benchmark_run):
    result, solution = benchmark_run
    validate_benchmark_identity(compiled, mesh, model)
    assert result.benchmark_id == BENCHMARK_ID
    assert result.solver_id == "acoustic_helmholtz_fem_3d_v1"
    assert result.nodes == len(mesh.nodes_m) and result.tetrahedra == len(mesh.tetrahedra)
    assert result.k_h_max <= 0.5
    assert result.normalized_algebraic_residual <= 1e-8
    assert result.reciprocal_condition_estimate >= MIN_RECIPROCAL_CONDITION_ESTIMATE
    assert result.normalized_complex_l2_error <= COMPLEX_L2_ERROR_LIMIT
    assert solution.pressure_imag_pa[0] == pytest.approx(0.0)
    assert len(solution.pressure_real_pa) == len(mesh.nodes_m)
    print("ACOUSTIC_3D=" + json.dumps(result.model_dump(mode="json"), sort_keys=True))
    print("PASS")


def test_analytical_reference_uses_the_bound_material_snapshot(mesh, model, benchmark_run):
    speed = authoritative_speed_of_sound(model)
    result, _ = benchmark_run
    assert result.speed_of_sound_m_s == speed
    with pytest.raises(TypeError):
        analytical_plane_wave_pressure(np.asarray(mesh.nodes_m)[:, 0])
    assert not np.allclose(
        analytical_plane_wave_pressure(np.asarray(mesh.nodes_m)[:, 0], speed),
        analytical_plane_wave_pressure(np.asarray(mesh.nodes_m)[:, 0], speed * 0.99),
    )


def test_real_acoustic_3d_plane_wave_refinement():
    result = run_server_owned_refinement()
    assert [item.target_size_mm for item in result.levels] == [20.0, 15.0, 10.0]
    assert len({item.mesh_id for item in result.levels}) == 3
    assert len({item.mesh_hash for item in result.levels}) == 3
    assert all(item.passed and item.finite_fields for item in result.levels)
    assert result.refinement_monotonic
    assert result.observed_order is not None and result.observed_order > 0.5
    assert result.levels[-1].normalized_complex_l2_error <= COMPLEX_L2_ERROR_LIMIT
    assert result.passed
    print("ACOUSTIC_3D_REFINEMENT=" + json.dumps(result.model_dump(mode="json"), sort_keys=True))
    print("PASS")


def test_near_discrete_resonance_fails_conditioning_gate(compiled):
    resonant_mesh = benchmark_mesh(compiled, 20.0)
    resonant_model = benchmark_model(resonant_mesh)
    nodes = np.asarray(resonant_mesh.nodes_m, dtype=float)
    stiffness_entries = []
    mass_entries = []
    for tet in resonant_mesh.tetrahedra:
        geometry = tet4_geometry(nodes, tet)
        stiffness = geometry.volume_m3 * (geometry.gradients_m_inv @ geometry.gradients_m_inv.T)
        mass = geometry.volume_m3 / 20.0 * np.array((
            (2.0, 1.0, 1.0, 1.0), (1.0, 2.0, 1.0, 1.0),
            (1.0, 1.0, 2.0, 1.0), (1.0, 1.0, 1.0, 2.0),
        ))
        stiffness_entries.append((tuple(tet), stiffness))
        mass_entries.append((tuple(tet), mass))
    constrained = sorted({
        node for mapping in resonant_mesh.metadata.semantic_mappings
        if mapping.semantic_region in {"inlet", "outlet"}
        for facet_id in mapping.boundary_facet_ids
        for node in resonant_mesh.boundary_facets[facet_id - 1]
    })
    free = np.setdiff1d(np.arange(len(nodes)), constrained)
    stiffness = _assemble(len(nodes), stiffness_entries)[free][:, free]
    mass = _assemble(len(nodes), mass_entries)[free][:, free]
    eigenvalue = float(sparse_linalg.eigsh(
        sparse.csr_matrix(stiffness.real), k=1, M=sparse.csr_matrix(mass.real),
        sigma=math.pi ** 2, which="LM", return_eigenvectors=False,
    )[0])
    speed = authoritative_speed_of_sound(resonant_model)
    frequency = speed * math.sqrt(eigenvalue) / (2.0 * math.pi)
    resonant_model = resonant_model.model_copy(update={
        "numerical_settings": HarmonicAcousticSettings(frequency_hz=frequency),
    })
    with pytest.raises(AcousticFEMError) as exc:
        solve_acoustic_fem_3d(resonant_mesh, resonant_model)
    assert exc.value.code in {"ACOUSTIC_SYSTEM_SINGULAR", "ACOUSTIC_SYSTEM_ILL_CONDITIONED"}


def test_impedance_weak_form_sign_matches_exp_i_omega_t_convention():
    matrix = impedance_boundary_matrix(2.0, 10.0, 4.0, 8.0)
    assert matrix[0, 0] == pytest.approx(1j * 10.0 * 4.0 / 8.0 * 2.0 / 6.0)
    assert matrix[0, 1] == pytest.approx(1j * 10.0 * 4.0 / 8.0 * 1.0 / 6.0)


def test_solid_domain_is_rejected(mesh, model):
    invalid = model.model_copy(update={"domains": [model.domains[0].model_copy(update={"domain_kind": DomainKind.SOLID})]})
    with pytest.raises(AcousticFEMError, match="acoustic domain") as exc:
        solve_acoustic_fem_3d(mesh, invalid)
    assert exc.value.code == "ACOUSTIC_DOMAIN_REQUIRED"


def test_missing_acoustic_material_properties_are_rejected(mesh, model):
    missing = model.model_copy(update={"materials": [model.materials[0].model_copy(update={"properties": []})]})
    with pytest.raises(AcousticFEMError) as exc:
        solve_acoustic_fem_3d(mesh, missing)
    assert exc.value.code == "MATERIAL_PROPERTY_MISSING"


def test_frequency_and_dispersion_fail_closed(mesh, model):
    with pytest.raises(ValidationError):
        HarmonicAcousticSettings(frequency_hz=0.0)
    coarse = model.model_copy(update={"numerical_settings": HarmonicAcousticSettings(frequency_hz=100000.0)})
    with pytest.raises(AcousticFEMError) as exc:
        solve_acoustic_fem_3d(mesh, coarse)
    assert exc.value.code == "ACOUSTIC_MESH_TOO_COARSE"


def test_pressure_reference_is_required_for_rigid_cavity(mesh, model):
    rigid = model.model_copy(update={
        "boundary_conditions": [
            item.model_copy(update={"condition": "rigid"})
            for item in model.boundary_conditions if isinstance(item, AcousticWallBC)
        ]
    })
    with pytest.raises(AcousticFEMError) as exc:
        solve_acoustic_fem_3d(mesh, rigid)
    assert exc.value.code == "ACOUSTIC_SYSTEM_SINGULAR"


def test_mesh_identity_and_benchmark_identity_fail_closed(compiled, mesh, model):
    mismatched = model.model_copy(update={"mesh_hash": "0" * 64})
    with pytest.raises(AcousticFEMError) as exc:
        solve_acoustic_fem_3d(mesh, mismatched)
    assert exc.value.code == "MESH_PHYSICS_MISMATCH"
    changed_frequency = model.model_copy(update={"numerical_settings": {"settings_type": "harmonic_acoustic", "frequency_hz": 101.0}})
    with pytest.raises(Exception):
        validate_benchmark_identity(compiled, mesh, changed_frequency)


def test_server_owned_benchmark_rejects_identity_spoofing(compiled, mesh, model, benchmark_run):
    changed_document = benchmark_design().model_dump(mode="json")
    changed_document["sketches"][0]["entities"][0]["width"] = {"value": 900, "unit": "mm"}
    with pytest.raises(Exception):
        validate_benchmark_identity(compile_design(type(benchmark_design()).model_validate(changed_document)), mesh, model)

    material = model.materials[0]
    changed_properties = [
        item.model_copy(update={"value": 340.0}) if item.name == "speed_of_sound" else item
        for item in material.properties
    ]
    changed_material = model.model_copy(update={
        "materials": [material.model_copy(update={"properties": changed_properties, "snapshot_hash": "0" * 64})],
    })
    with pytest.raises(Exception):
        validate_benchmark_identity(compiled, mesh, changed_material)

    changed_boundary = model.model_copy(update={
        "boundary_conditions": [
            model.boundary_conditions[0].model_copy(update={"pressure_pa": 2.0}),
            *model.boundary_conditions[1:],
        ],
    })
    with pytest.raises(Exception):
        validate_benchmark_identity(compiled, mesh, changed_boundary)

    changed_family = mesh.metadata.model_copy(update={"element_types": ["triangle3"]})
    with pytest.raises(Exception):
        validate_benchmark_identity(compiled, mesh.__class__(
            metadata=changed_family, nodes_m=mesh.nodes_m,
            tetrahedra=mesh.tetrahedra, boundary_facets=mesh.boundary_facets,
        ), model)

    result, _ = benchmark_run
    spoofed = result.model_dump(mode="json")
    spoofed["solver_id"] = "client_solver"
    with pytest.raises(ValidationError):
        AcousticBenchmarkResultV1.model_validate(spoofed)


def test_unsupported_acoustic_boundary_and_malformed_mapping_fail_closed(mesh, model):
    unsupported = model.model_copy(update={
        "boundary_conditions": [model.boundary_conditions[0].model_copy(update={"bc_type": "unsupported"})]
    })
    with pytest.raises(AcousticFEMError) as exc:
        solve_acoustic_fem_3d(mesh, unsupported)
    assert exc.value.code == "UNSUPPORTED_ACOUSTIC_BC"
    malformed = model.model_copy(update={"semantic_mappings": []})
    with pytest.raises(AcousticFEMError) as exc:
        solve_acoustic_fem_3d(mesh, malformed)
    assert exc.value.code == "INVALID_SEMANTIC_MAPPING"


def test_invalid_impedance_value_is_typed_and_rejected():
    with pytest.raises(ValidationError):
        AcousticWallBC(bc_id="wall", semantic_region="walls", condition="impedance", impedance_pa_s_m=-1.0)
    with pytest.raises(AcousticFEMError):
        impedance_boundary_matrix(1.0, 1.0, 1.0, 0.0)
