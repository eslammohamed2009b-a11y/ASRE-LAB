"""Phase 3 integration proof: the real Design Accelerator with the real
CadQuery/OCP kernel (no stub — enforced by tests/integration/conftest.py's
fail-fast import guard).

These tests generate actual geometry, export real STEP/STL files, re-import
the STEP file with CadQuery and inspect its bounding box, and confirm that
varying an input parameter measurably changes the produced geometry. This
is the evidence required to call Module 1's CAD pipeline "real" rather than
mocked.
"""
from pathlib import Path

import cadquery as cq
import pytest

from app.module1_design import cadquery_engine
from app.module1_design.schemas import DesignParameters, GeometryType, MaterialType

pytestmark = pytest.mark.integration


def test_generate_model_pyramid_produces_real_nonempty_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_engine, "EXPORT_DIR", tmp_path)

    params = DesignParameters(geometry_type=GeometryType.PYRAMID, height_m=100.0)
    result = cadquery_engine.generate_model(params)

    stl_path = Path(result["stl_path"])
    step_path = Path(result["step_path"])

    assert stl_path.exists(), "STL file was not written to disk"
    assert step_path.exists(), "STEP file was not written to disk"
    assert stl_path.stat().st_size > 0, "STL file is empty"
    assert step_path.stat().st_size > 500, "STEP file is suspiciously small for real geometry"

    # A stub would write a fixed literal string ("stub-export"); the real
    # kernel writes a binary/text CAD format. Confirm it is not the stub marker.
    assert stl_path.read_bytes() != b"stub-export"


def test_generate_model_step_file_reimports_with_matching_bounding_box(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_engine, "EXPORT_DIR", tmp_path)

    params = DesignParameters(
        geometry_type=GeometryType.PYRAMID,
        base_length_m=50.0,
        height_m=80.0,
    )
    result = cadquery_engine.generate_model(params)
    step_path = result["step_path"]

    reimported = cq.importers.importStep(step_path)
    bbox = reimported.val().BoundingBox()

    # Base is 50m x 50m tapering to a near-zero apex at height 80m.
    assert bbox.xlen == pytest.approx(50.0, abs=1.0)
    assert bbox.ylen == pytest.approx(50.0, abs=1.0)
    assert bbox.zlen == pytest.approx(80.0, abs=1.0)


def test_generate_model_parameter_sensitivity_changes_geometry(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_engine, "EXPORT_DIR", tmp_path)

    short = cadquery_engine.generate_model(
        DesignParameters(geometry_type=GeometryType.PYRAMID, height_m=50.0)
    )
    tall = cadquery_engine.generate_model(
        DesignParameters(geometry_type=GeometryType.PYRAMID, height_m=200.0)
    )

    short_bbox = cq.importers.importStep(short["step_path"]).val().BoundingBox()
    tall_bbox = cq.importers.importStep(tall["step_path"]).val().BoundingBox()

    assert tall_bbox.zlen > short_bbox.zlen
    assert tall_bbox.zlen == pytest.approx(200.0, abs=1.0)
    assert short_bbox.zlen == pytest.approx(50.0, abs=1.0)
    # Different heights must not coincidentally produce byte-identical STL content
    # (file size alone can coincide since triangle count/record size stays fixed
    # for this tessellation regardless of the exact coordinate values).
    assert Path(short["stl_path"]).read_bytes() != Path(tall["stl_path"]).read_bytes()


def test_generate_model_tower_and_bridge_also_produce_real_geometry(tmp_path, monkeypatch):
    monkeypatch.setattr(cadquery_engine, "EXPORT_DIR", tmp_path)

    tower = cadquery_engine.generate_model(
        DesignParameters(geometry_type=GeometryType.TOWER, height_m=60.0, material=MaterialType.CONCRETE)
    )
    bridge = cadquery_engine.generate_model(
        DesignParameters(geometry_type=GeometryType.BRIDGE, material=MaterialType.STEEL)
    )

    for result in (tower, bridge):
        stl_path = Path(result["stl_path"])
        step_path = Path(result["step_path"])
        assert stl_path.stat().st_size > 0
        assert step_path.stat().st_size > 500
        bbox = cq.importers.importStep(str(step_path)).val().BoundingBox()
        assert bbox.xlen > 0 and bbox.ylen > 0 and bbox.zlen > 0
