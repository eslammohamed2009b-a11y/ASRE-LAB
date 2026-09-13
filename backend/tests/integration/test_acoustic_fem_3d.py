from __future__ import annotations

import json

import numpy as np
import pytest
from pydantic import ValidationError

from app.module1_design.cad_v2_compiler import compile_design
from app.module2_simulation.acoustic_benchmark import (
    BENCHMARK_ID,
    COMPLEX_L2_ERROR_LIMIT,
    benchmark_design,
    benchmark_model,
    benchmark_mesh,
    run_server_owned_benchmark,
    validate_benchmark_identity,
)
from app.module2_simulation.acoustic_fem_solvers import (
    AcousticFEMError,
    impedance_boundary_matrix,
    solve_acoustic_fem_3d,
)
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


def test_real_acoustic_3d_plane_wave_accuracy(compiled, mesh, model):
    result, solution = run_server_owned_benchmark()
    validate_benchmark_identity(compiled, mesh, model)
    assert result.benchmark_id == BENCHMARK_ID
    assert result.solver_id == "acoustic_helmholtz_fem_3d_v1"
    assert result.nodes == len(mesh.nodes_m) and result.tetrahedra == len(mesh.tetrahedra)
    assert result.k_h_max <= 0.5
    assert result.normalized_algebraic_residual <= 1e-8
    assert result.normalized_complex_l2_error <= COMPLEX_L2_ERROR_LIMIT
    assert solution.pressure_imag_pa[0] == pytest.approx(0.0)
    assert len(solution.pressure_real_pa) == len(mesh.nodes_m)
    print("ACOUSTIC_3D=" + json.dumps(result.model_dump(mode="json"), sort_keys=True))
    print("PASS")


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
