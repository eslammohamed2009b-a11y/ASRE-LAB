from __future__ import annotations

import math

import pytest

from app.module2_simulation.thermal_field_benchmark import tetra_quadrature_degree4


def test_duffy_reference_weights_map_constant_to_physical_tetra_volume():
    physical_volume = 2.75
    integral = sum(6.0 * physical_volume * weight for _point, weight in tetra_quadrature_degree4())
    assert integral == pytest.approx(physical_volume, rel=1e-14, abs=1e-14)


@pytest.mark.parametrize("powers", [
    (0,0,0),(1,0,0),(0,1,0),(0,0,1),(2,0,0),(1,1,0),(0,2,1),(4,0,0),(2,1,1),
])
def test_duffy_rule_integrates_reference_tetra_monomials_through_degree_four(powers):
    a,b,c=powers
    computed=sum(weight*point[0]**a*point[1]**b*point[2]**c for point,weight in tetra_quadrature_degree4())
    exact=math.factorial(a)*math.factorial(b)*math.factorial(c)/math.factorial(a+b+c+3)
    assert computed==pytest.approx(exact,rel=2e-13,abs=2e-15)
