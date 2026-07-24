"""ASRE-LAB backend package."""

# Must run before importing CadQuery/NLopt from any app submodule on Windows.
from app.core import native_runtime as _native_runtime  # noqa: F401
