"""Windows regression for the CadQuery/NLopt/CasADi shutdown crash."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_cadquery_export_child_process_exits_cleanly(tmp_path: Path) -> None:
    stl_path = tmp_path / "clean-exit.stl"
    step_path = tmp_path / "clean-exit.step"
    code = (
        "from app.core import native_runtime;"
        "import cadquery as cq,sys;"
        "shape=cq.Workplane('XY').box(1,1,1);"
        "cq.exporters.export(shape,sys.argv[1]);"
        "cq.exporters.export(shape,sys.argv[2]);"
        "print(cq.__version__)"
    )

    completed = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code, str(stl_path), str(step_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, (
        f"CadQuery child exited {completed.returncode}; stdout={completed.stdout!r}; "
        f"stderr={completed.stderr!r}"
    )
    assert stl_path.stat().st_size > 0
    assert step_path.stat().st_size > 0
