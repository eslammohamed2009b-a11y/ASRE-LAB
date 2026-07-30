from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.main import app

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _resolve(spec: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    if not schema:
        return {}
    if "$ref" in schema:
        node: Any = spec
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        return node
    return schema


def _media_schema(operation: dict[str, Any], location: str, status: str | None = None):
    node = operation.get(location, {})
    if status is not None:
        node = node.get(status, {})
    content = node.get("content", {})
    if "application/json" in content:
        return content["application/json"].get("schema", {})
    return next((value.get("schema", {}) for value in content.values()), {})


def _assert_shared_shape(
    baseline_spec: dict[str, Any],
    current_spec: dict[str, Any],
    baseline_schema: dict[str, Any],
    current_schema: dict[str, Any],
    *,
    request: bool,
    location: str,
) -> None:
    baseline_schema = _resolve(baseline_spec, baseline_schema)
    current_schema = _resolve(current_spec, current_schema)
    for key in ("type", "format"):
        if key in baseline_schema:
            assert current_schema.get(key) == baseline_schema[key], (location, key)
    if "enum" in baseline_schema:
        assert set(baseline_schema["enum"]) <= set(current_schema.get("enum", [])), location
    baseline_required = set(baseline_schema.get("required", []))
    current_required = set(current_schema.get("required", []))
    if request:
        assert current_required <= baseline_required, (location, "new required request field")
    else:
        assert baseline_required <= current_required, (location, "missing required response field")
    baseline_properties = baseline_schema.get("properties", {})
    current_properties = current_schema.get("properties", {})
    for name, child in baseline_properties.items():
        assert name in current_properties, (location, "removed property", name)
        _assert_shared_shape(
            baseline_spec, current_spec, child, current_properties[name],
            request=request, location=f"{location}.{name}",
        )
    for keyword in ("allOf", "anyOf", "oneOf"):
        baseline_items = baseline_schema.get(keyword, [])
        current_items = current_schema.get(keyword, [])
        assert len(current_items) >= len(baseline_items), (location, keyword)
        for index, child in enumerate(baseline_items):
            _assert_shared_shape(
                baseline_spec, current_spec, child, current_items[index],
                request=request, location=f"{location}.{keyword}[{index}]",
            )
    if "items" in baseline_schema:
        _assert_shared_shape(
            baseline_spec, current_spec, baseline_schema["items"], current_schema.get("items", {}),
            request=request, location=f"{location}.items",
        )


def test_backend_v2_openapi_is_strictly_backward_compatible() -> None:
    baseline = json.loads((ROOT / "openapi-baseline-v2.json").read_text())
    current = app.openapi()
    assert set(baseline["paths"]) <= set(current["paths"])
    for path, baseline_path in baseline["paths"].items():
        for method, baseline_operation in baseline_path.items():
            if method not in METHODS:
                continue
            assert method in current["paths"][path], (path, method)
            current_operation = current["paths"][path][method]
            assert current_operation["operationId"] == baseline_operation["operationId"], (path, method)
            baseline_statuses = set(baseline_operation["responses"])
            assert baseline_statuses <= set(current_operation["responses"]), (path, method, "status codes")

            baseline_request = _media_schema(baseline_operation, "requestBody")
            current_request = _media_schema(current_operation, "requestBody")
            if baseline_request:
                assert current_request, (path, method, "request body removed")
                _assert_shared_shape(
                    baseline, current, baseline_request, current_request,
                    request=True, location=f"{method.upper()} {path} request",
                )

            for status in baseline_statuses:
                baseline_response = _media_schema(baseline_operation["responses"], status)
                current_response = _media_schema(current_operation["responses"], status)
                if baseline_response:
                    assert current_response, (path, method, status, "response body removed")
                    _assert_shared_shape(
                        baseline, current, baseline_response, current_response,
                        request=False, location=f"{method.upper()} {path} response {status}",
                    )
