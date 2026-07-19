"""Fixtures for unit tests.

Only unit tests are allowed to run against a stubbed `cadquery` module. The
stub is installed and torn down explicitly, scoped to a single test via the
`stubbed_cadquery_engine` fixture below, and it forces a fresh import of
`app.module1_design.cadquery_engine` so the module-level `cq` name is bound
to the stub (not any real `cadquery` that may already be cached in
`sys.modules` from an integration test run earlier in the same session).

A unit test using this fixture proves the business logic (parameter
handling, control flow, response shape) but is NEVER evidence that the real
CAD kernel works — see tests/integration/ and tests/e2e/ for that.
"""
import importlib
import sys
import types
from typing import Iterator

import pytest

_ENGINE_MODULE = "app.module1_design.cadquery_engine"


class _StubWorkplane:
    def __init__(self, *args, **kwargs):
        pass

    def rect(self, *args, **kwargs):
        return self

    def workplane(self, *args, **kwargs):
        return self

    def loft(self, *args, **kwargs):
        return self

    def extrude(self, *args, **kwargs):
        return self

    def box(self, *args, **kwargs):
        return self


def _stub_export(result, path, *args, **kwargs):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("stub-export")


@pytest.fixture
def stubbed_cadquery_engine() -> Iterator[types.ModuleType]:
    """Yield `cadquery_engine` freshly imported against a fake `cadquery`."""
    real_cadquery = sys.modules.pop("cadquery", None)

    stub = types.ModuleType("cadquery")
    stub.Workplane = _StubWorkplane
    stub.exporters = types.SimpleNamespace(export=_stub_export)
    sys.modules["cadquery"] = stub

    sys.modules.pop(_ENGINE_MODULE, None)
    engine = importlib.import_module(_ENGINE_MODULE)
    assert engine.cq is stub, "cadquery_engine did not bind to the stub module"

    try:
        yield engine
    finally:
        sys.modules.pop(_ENGINE_MODULE, None)
        sys.modules.pop("cadquery", None)
        if real_cadquery is not None:
            sys.modules["cadquery"] = real_cadquery
            importlib.import_module(_ENGINE_MODULE)
