from __future__ import annotations

import cadquery as cq
import numpy as np
import pytest

from app.module2_simulation.meshing import (
    MeshingError, _candidate_points, _contained_tetrahedron, _tet_volume, _tetrahedralize,
)


def _mesh(solid, size=5.0):
    points = _candidate_points(solid, size, 10_000)
    tetrahedra = _tetrahedralize(solid, points, size)
    volume = sum(abs(_tet_volume(points[list(tet)])) for tet in tetrahedra)
    return points, tetrahedra, volume


def test_occ_boolean_containment_for_convex_cylinder_and_concave_notch():
    cylinder = cq.Solid.makeCylinder(10, 20)
    notch = cq.Solid.makeBox(20, 20, 20).cut(cq.Solid.makeBox(10, 10, 20, cq.Vector(10, 10, 0)))
    for solid in (cylinder, notch):
        points, tetrahedra, volume = _mesh(solid)
        assert tetrahedra
        assert all(_contained_tetrahedron(solid, points[list(tet)], absolute_tolerance_mm3=5**3 * 1e-12) for tet in tetrahedra)
        assert abs(volume - solid.Volume()) <= max(1e-12, .15 * solid.Volume())


def test_cavity_is_not_silently_filled_or_is_rejected():
    cavity = cq.Solid.makeBox(30, 30, 30).cut(cq.Solid.makeBox(10, 10, 10, cq.Vector(10, 10, 10)))
    try:
        points, tetrahedra, _ = _mesh(cavity)
    except MeshingError as exc:
        assert exc.code == "UNSUPPORTED_VOLUME_TOPOLOGY"
        return
    center = np.array((15., 15., 15.))
    assert all(not np.allclose(points[list(tet)].mean(axis=0), center, atol=4.0) for tet in tetrahedra)


def test_small_nonrectangular_coarse_mesh_cannot_use_cell_volume_to_evade_15_percent_limit():
    solid = cq.Solid.makeCylinder(1, 2)
    # Sparse authoritative boundary samples form an inscribed square prism:
    # every retained cell is inside OCC, but roughly 36% of the cylinder is
    # absent. The former size**3 allowance incorrectly certified this case.
    points = np.asarray([
        (1,0,0),(0,1,0),(-1,0,0),(0,-1,0),
        (1,0,2),(0,1,2),(-1,0,2),(0,-1,2),
    ],dtype=float)
    with pytest.raises(MeshingError, match="aggregate authoritative volume") as error:
        _tetrahedralize(solid, points, 5.0)
    assert error.value.code == "UNSUPPORTED_VOLUME_TOPOLOGY"
