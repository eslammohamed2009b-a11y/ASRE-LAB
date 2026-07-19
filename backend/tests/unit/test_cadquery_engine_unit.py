"""Unit tests for the Module 1 Design Accelerator business logic.

These tests use a stubbed `cadquery` module (see tests/unit/conftest.py) and
therefore prove ONLY that `generate_model`'s control flow, parameter
handling, and response shape are correct. They are NOT evidence that the
real CAD kernel produces valid geometry — see
tests/integration/test_design_accelerator_integration.py for that.
"""
from pathlib import Path

import pytest

from app.module1_design.schemas import DesignParameters, GeometryType, MaterialType

pytestmark = pytest.mark.unit


def test_generate_model_pyramid_returns_expected_shape(stubbed_cadquery_engine, tmp_path, monkeypatch):
    engine = stubbed_cadquery_engine
    monkeypatch.setattr(engine, "EXPORT_DIR", tmp_path)

    params = DesignParameters(
        geometry_type=GeometryType.PYRAMID,
        height_m=100.0,
    )

    result = engine.generate_model(params)

    assert set(result.keys()) == {"design_id", "params", "stl_path", "step_path"}
    assert result["params"]["geometry_type"] == GeometryType.PYRAMID.value
    assert result["params"]["material"] == MaterialType.LIMESTONE.value

    stl_path = Path(result["stl_path"])
    step_path = Path(result["step_path"])
    assert stl_path.exists() and stl_path.read_text(encoding="utf-8") == "stub-export"
    assert step_path.exists() and step_path.read_text(encoding="utf-8") == "stub-export"


def test_generate_model_unknown_geometry_raises(stubbed_cadquery_engine):
    engine = stubbed_cadquery_engine
    with pytest.raises(Exception):
        engine.generate_model(
            DesignParameters.model_construct(geometry_type="not-a-real-type")
        )
