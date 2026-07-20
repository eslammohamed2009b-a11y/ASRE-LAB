"""Unit proof of the public OpenAPI license metadata.

ASRE-LAB is proprietary/source-available, not Apache-2.0 licensed. This test
generates the real OpenAPI schema (the same schema served at /openapi.json
and rendered by Swagger UI at /docs) and asserts:

- the false "Apache-2.0" declaration is nowhere in the schema's license info;
- the accurate proprietary/source-available license name is present, with a
  URL pointing at the repository's actual LICENSE file (not an open-source
  SPDX identifier).
"""
import pytest

pytestmark = pytest.mark.unit


def test_openapi_license_is_proprietary_not_apache():
    from app.main import app

    schema = app.openapi()
    license_info = schema["info"]["license"]

    assert license_info["name"] != "Apache-2.0"
    assert "apache" not in license_info["name"].lower()
    assert license_info["name"] == "Proprietary Source-Available License"
    assert license_info["url"] == (
        "https://github.com/eslammohamed2009b-a11y/ASRE-LAB/blob/main/LICENSE"
    )
