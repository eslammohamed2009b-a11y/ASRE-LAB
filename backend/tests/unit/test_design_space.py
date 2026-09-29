import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
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


def test_paired_explicit_values_preserve_order_and_derive_pyramid_height():
    variants = build_design_space(DesignSpaceRequest(
        base_params=pyramid(),
        parameters=[
            SweepParameter(field="base_length_m", method="explicit", values=[200, 220, 240]),
            SweepParameter(field="slope_angle_deg", method="explicit", values=[40, 50, 60]),
        ],
        pair_explicit_values=True,
    ))
    assert len(variants) == 3
    assert [(item["varied_values"]["base_length_m"], item["varied_values"]["slope_angle_deg"]) for item in variants] == [
        (200.0, 40.0), (220.0, 50.0), (240.0, 60.0)
    ]
    assert [item["variation_index"] for item in variants] == [0, 1, 2]
    assert [item["parameters"]["height_m"] for item in variants] == [83.909963, 131.092895, 207.846097]


def test_ten_paired_explicit_values_resolve_to_ten_variants_not_one_hundred():
    variants = build_design_space(DesignSpaceRequest(
        base_params=pyramid(),
        parameters=[
            SweepParameter(field="base_length_m", method="explicit", values=list(range(200, 300, 10))),
            SweepParameter(field="slope_angle_deg", method="explicit", values=list(range(40, 50))),
        ],
        pair_explicit_values=True,
    ))
    assert len(variants) == 10
    assert [(item["varied_values"]["base_length_m"], item["varied_values"]["slope_angle_deg"]) for item in variants] == [
        (float(base), float(slope)) for base, slope in zip(range(200, 300, 10), range(40, 50))
    ]


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ([
            SweepParameter(field="base_length_m", method="explicit", values=[2, 4]),
            SweepParameter(field="height_m", method="explicit", values=[1]),
        ], "equal-length"),
        ([SweepParameter(field="height_m", minimum=1, maximum=2, count=2)], "exactly two"),
        ([
            SweepParameter(field="base_length_m", method="explicit", values=[2, 4]),
            SweepParameter(field="height_m", minimum=1, maximum=2, count=2),
        ], "both parameters to use explicit"),
    ],
)
def test_paired_explicit_values_reject_invalid_configuration(parameters, message):
    with pytest.raises(ValueError, match=message):
        DesignSpaceRequest(base_params=pyramid(), parameters=parameters, pair_explicit_values=True)


def test_single_parameter_explicit_behavior_is_unchanged():
    variants = build_design_space(DesignSpaceRequest(
        base_params=pyramid(),
        parameters=[SweepParameter(field="height_m", method="explicit", values=[1, 3, 5])],
    ))
    assert [item["parameters"]["height_m"] for item in variants] == [1, 3, 5]


def test_preview_rejects_invalid_paired_explicit_values_with_http_422():
    app.dependency_overrides[get_current_user] = lambda: {"id": "paired-explicit-user"}
    try:
        response = TestClient(app).post("/api/design/design-space/preview", json={
            "base_params": pyramid().model_dump(mode="json"),
            "parameters": [
                {"field": "base_length_m", "method": "explicit", "values": [2, 4]},
                {"field": "height_m", "method": "explicit", "values": [1]},
            ],
            "pair_explicit_values": True,
        })
        assert response.status_code == 422
        assert "equal-length" in response.json()["detail"][0]["msg"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_design_space_is_bounded():
    with pytest.raises(ValueError, match="maximum is 500"):
        build_design_space(DesignSpaceRequest(
            base_params=pyramid(),
            parameters=[
                SweepParameter(field="height_m", minimum=1, maximum=30, count=30),
                SweepParameter(field="base_length_m", minimum=1, maximum=30, count=30),
            ],
        ))
