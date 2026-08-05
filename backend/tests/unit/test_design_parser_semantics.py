import math

import pytest

from app.module1_design import nl_parser
from app.module1_design.schemas import DesignParameters, GeometryType


@pytest.fixture(autouse=True)
def deterministic_parser(monkeypatch):
    monkeypatch.setattr(nl_parser, "client", None)


@pytest.mark.parametrize(
    "prompt,geometry,height,base",
    [
        ("pyramid with a 2 m by 2 m base and 4 m height", "pyramid", 4.0, 2.0),
        ("4 m high pyramid with a 2 m square base", "pyramid", 4.0, 2.0),
        ("pyramid height 4 meters base length 2 meters", "pyramid", 4.0, 2.0),
        ("tower 10 m high with 4 m width", "tower", 10.0, 4.0),
        ("a 2.5 metre height pyramid with a base of 3 metres", "pyramid", 2.5, 3.0),
    ],
)
def test_semantic_dimensions_are_not_positional(prompt, geometry, height, base):
    result = nl_parser.parse_design_request(prompt)
    assert result.geometry_type.value == geometry
    assert result.height_m == pytest.approx(height)
    assert result.base_length_m == pytest.approx(base)


def test_pyramid_derives_slope_from_explicit_base_and_height():
    result = nl_parser.parse_design_request("pyramid with a 2 m base and 4 m height")
    assert result.slope_angle_deg == pytest.approx(math.degrees(math.atan2(4, 1)))


def test_pyramid_rejects_three_inconsistent_authoritative_dimensions():
    with pytest.raises(ValueError, match="Inconsistent pyramid dimensions"):
        DesignParameters(
            geometry_type=GeometryType.PYRAMID,
            base_length_m=2,
            height_m=4,
            slope_angle_deg=45,
        )


def test_pyramid_derives_missing_height_from_base_and_slope():
    result = DesignParameters(
        geometry_type=GeometryType.PYRAMID,
        base_length_m=2,
        slope_angle_deg=45,
    )
    assert result.height_m == pytest.approx(1.0)
