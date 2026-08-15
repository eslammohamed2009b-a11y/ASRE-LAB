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
        assert abs(volume - solid.Volume()) <= max(5**3, .15 * solid.Volume())


def test_cavity_is_not_silently_filled_or_is_rejected():
    cavity = cq.Solid.makeBox(30, 30, 30).cut(cq.Solid.makeBox(10, 10, 10, cq.Vector(10, 10, 10)))
    try:
        points, tetrahedra, _ = _mesh(cavity)
    except MeshingError as exc:
        assert exc.code == "UNSUPPORTED_VOLUME_TOPOLOGY"
        return
    center = np.array((15., 15., 15.))
    assert all(not np.allclose(points[list(tet)].mean(axis=0), center, atol=4.0) for tet in tetrahedra)
