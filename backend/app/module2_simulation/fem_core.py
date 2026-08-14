"""Small, explicit shared TET4 finite-element primitives in SI units."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
from scipy import sparse


class FEMError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


MAX_NODES = 5_000
MAX_ELEMENTS = 20_000
MAX_STRUCTURAL_DOFS = 15_000


@dataclass(frozen=True)
class Tet4Geometry:
    node_ids: tuple[int, int, int, int]
    volume_m3: float
    gradients_m_inv: np.ndarray  # (4, 3), one constant grad(N_i) per node


def validate_mesh_arrays(nodes_m: np.ndarray, tetrahedra: tuple[tuple[int, int, int, int], ...]) -> None:
    if nodes_m.ndim != 2 or nodes_m.shape[1] != 3 or not np.isfinite(nodes_m).all():
        raise FEMError("INVALID_MESH", "Mesh nodes must be finite SI xyz coordinates")
    if len(nodes_m) > MAX_NODES or len(tetrahedra) > MAX_ELEMENTS:
        raise FEMError("RESOURCE_LIMIT", "Mesh exceeds the current bounded FEM resource envelope")
    for tet in tetrahedra:
        if len(tet) != 4 or len(set(tet)) != 4 or any(node < 0 or node >= len(nodes_m) for node in tet):
            raise FEMError("INVALID_CONNECTIVITY", "TET4 connectivity must contain four distinct valid node IDs")


def tet4_geometry(nodes_m: np.ndarray, tet: tuple[int, int, int, int]) -> Tet4Geometry:
    points = nodes_m[list(tet)]
    matrix = np.column_stack((np.ones(4), points))
    determinant = float(np.linalg.det(matrix))
    if not np.isfinite(determinant) or determinant <= 1e-18:
        raise FEMError("INVALID_TETRAHEDRON", "TET4 has zero or inverted volume")
    coefficients = np.linalg.inv(matrix)
    return Tet4Geometry(tet, determinant / 6.0, coefficients[1:, :].T.copy())


def thermal_tet4_matrix(geometry: Tet4Geometry, conductivity_w_mk: float) -> np.ndarray:
    if not np.isfinite(conductivity_w_mk) or conductivity_w_mk <= 0:
        raise FEMError("INVALID_CONDUCTIVITY", "Thermal conductivity must be finite and positive")
    return conductivity_w_mk * geometry.volume_m3 * (geometry.gradients_m_inv @ geometry.gradients_m_inv.T)


def thermal_volume_load(geometry: Tet4Geometry, source_w_m3: float) -> np.ndarray:
    if not np.isfinite(source_w_m3):
        raise FEMError("INVALID_VOLUME_SOURCE", "Volumetric heat source must be finite")
    return np.full(4, source_w_m3 * geometry.volume_m3 / 4.0)


def triangle_area_and_outward_normal(nodes_m: np.ndarray, facet: tuple[int, int, int], opposite_node: int) -> tuple[float, np.ndarray]:
    a, b, c = nodes_m[list(facet)]
    normal = np.cross(b - a, c - a)
    norm = float(np.linalg.norm(normal))
    if norm <= 1e-18:
        raise FEMError("DEGENERATE_FACET", "Boundary facet has zero area")
    if float(np.dot(normal, nodes_m[opposite_node] - a)) > 0:
        normal = -normal
    return norm / 2.0, normal / norm


def triangle_scalar_load(area_m2: float, value: float) -> np.ndarray:
    return np.full(3, area_m2 * value / 3.0)


def triangle_convection_matrix(area_m2: float, coefficient_w_m2k: float) -> np.ndarray:
    if coefficient_w_m2k <= 0 or not np.isfinite(coefficient_w_m2k):
        raise FEMError("INVALID_CONVECTION", "Convection coefficient must be finite and positive")
    return coefficient_w_m2k * area_m2 / 12.0 * np.array(((2.0, 1.0, 1.0), (1.0, 2.0, 1.0), (1.0, 1.0, 2.0)))


def structural_b_matrix(geometry: Tet4Geometry) -> np.ndarray:
    b = np.zeros((6, 12), dtype=float)
    for index, (gx, gy, gz) in enumerate(geometry.gradients_m_inv):
        column = 3 * index
        b[:, column:column + 3] = (
            (gx, 0, 0), (0, gy, 0), (0, 0, gz),
            (gy, gx, 0), (0, gz, gy), (gz, 0, gx),
        )
    return b


def isotropic_elasticity_matrix(youngs_modulus_pa: float, poisson_ratio: float) -> np.ndarray:
    if not np.isfinite(youngs_modulus_pa) or youngs_modulus_pa <= 0:
        raise FEMError("INVALID_ELASTIC_MODULUS", "Young's modulus must be finite and positive")
    if not np.isfinite(poisson_ratio) or not (-1.0 < poisson_ratio < 0.5):
        raise FEMError("INVALID_POISSON_RATIO", "Poisson ratio must lie strictly between -1 and 0.5")
    lam = youngs_modulus_pa * poisson_ratio / ((1 + poisson_ratio) * (1 - 2 * poisson_ratio))
    mu = youngs_modulus_pa / (2 * (1 + poisson_ratio))
    return np.array([
        (lam + 2 * mu, lam, lam, 0, 0, 0), (lam, lam + 2 * mu, lam, 0, 0, 0),
        (lam, lam, lam + 2 * mu, 0, 0, 0), (0, 0, 0, mu, 0, 0),
        (0, 0, 0, 0, mu, 0), (0, 0, 0, 0, 0, mu),
    ])


def structural_tet4_matrix(geometry: Tet4Geometry, elasticity: np.ndarray) -> np.ndarray:
    b = structural_b_matrix(geometry)
    return geometry.volume_m3 * (b.T @ elasticity @ b)


def consistent_tet4_mass(geometry: Tet4Geometry, density_kg_m3: float) -> np.ndarray:
    if not np.isfinite(density_kg_m3) or density_kg_m3 <= 0:
        raise FEMError("INVALID_DENSITY", "Density must be finite and positive")
    scalar = density_kg_m3 * geometry.volume_m3 / 20.0 * np.array(
        ((2.0, 1.0, 1.0, 1.0), (1.0, 2.0, 1.0, 1.0), (1.0, 1.0, 2.0, 1.0), (1.0, 1.0, 1.0, 2.0))
    )
    return np.kron(scalar, np.eye(3))


def assemble_sparse(size: int, entries: list[tuple[np.ndarray, np.ndarray]]) -> sparse.csr_matrix:
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    for dofs, matrix in entries:
        for local_row, global_row in enumerate(dofs):
            for local_col, global_col in enumerate(dofs):
                value = float(matrix[local_row, local_col])
                if value:
                    rows.append(int(global_row)); cols.append(int(global_col)); data.append(value)
    return sparse.coo_matrix((data, (rows, cols)), shape=(size, size)).tocsr()


def apply_dirichlet(matrix: sparse.csr_matrix, vector: np.ndarray, prescribed: dict[int, float]) -> tuple[sparse.csr_matrix, np.ndarray, np.ndarray]:
    """Return reduced free-DOF system and ordered free DOFs; exact elimination."""
    if not prescribed:
        raise FEMError("MISSING_DIRICHLET_CONSTRAINT", "At least one Dirichlet constraint is required")
    constrained = np.array(sorted(prescribed), dtype=int)
    if np.any(constrained < 0) or np.any(constrained >= matrix.shape[0]):
        raise FEMError("INVALID_CONSTRAINT", "Constraint references an invalid DOF")
    values = np.array([prescribed[index] for index in constrained], dtype=float)
    free_mask = np.ones(matrix.shape[0], dtype=bool); free_mask[constrained] = False
    free = np.flatnonzero(free_mask)
    if not len(free):
        raise FEMError("OVERCONSTRAINED", "All degrees of freedom are constrained")
    rhs = vector[free] - matrix[free][:, constrained] @ values
    return matrix[free][:, free].tocsr(), np.asarray(rhs).reshape(-1), free


def expand_dirichlet(size: int, free: np.ndarray, solution: np.ndarray, prescribed: dict[int, float]) -> np.ndarray:
    full = np.zeros(size, dtype=float)
    full[free] = solution
    for dof, value in prescribed.items():
        full[dof] = value
    return full

