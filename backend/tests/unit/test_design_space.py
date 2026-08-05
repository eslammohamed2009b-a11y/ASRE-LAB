import pytest

from app.module1_design.design_space import build_design_space
from app.module1_design.schemas import (
    DesignParameters,
    DesignSpaceRequest,
    GeometryType,
    SweepParameter,
)


def pyramid():
    return DesignParameters(
        geometry_type=GeometryType.PYRAMID,
        base_length_m=2,
        height_m=2,
        material="concrete",
    )


def test_linear_height_sweep_is_ordered_distinct_and_geometrically_consistent():
    variants = build_design_space(DesignSpaceRequest(
        base_params=pyramid(),
        parameters=[SweepParameter(field="height_m", minimum=1, maximum=5, count=5)],
        seed=17,
    ))
    assert [item["variation_index"] for item in variants] == list(range(5))
    assert [item["parameters"]["height_m"] for item in variants] == [1, 2, 3, 4, 5]
    assert len({item["parameters"]["slope_angle_deg"] for item in variants}) == 5
    assert all(item["parameters"]["base_length_m"] == 2 for item in variants)


def test_two_parameter_grid_has_cartesian_product_and_full_parameters():
    variants = build_design_space(DesignSpaceRequest(
        base_params=pyramid(),
        parameters=[
            SweepParameter(field="base_length_m", method="explicit", values=[2, 4]),
            SweepParameter(field="height_m", method="explicit", values=[1, 2, 3]),
        ],
    ))
    assert len(variants) == 6
    assert {(v["parameters"]["base_length_m"], v["parameters"]["height_m"]) for v in variants} == {
        (2, 1), (2, 2), (2, 3), (4, 1), (4, 2), (4, 3)
    }


def test_design_space_is_bounded():
    with pytest.raises(ValueError, match="maximum is 500"):
        build_design_space(DesignSpaceRequest(
            base_params=pyramid(),
            parameters=[
                SweepParameter(field="height_m", minimum=1, maximum=30, count=30),
                SweepParameter(field="base_length_m", minimum=1, maximum=30, count=30),
            ],
        ))
