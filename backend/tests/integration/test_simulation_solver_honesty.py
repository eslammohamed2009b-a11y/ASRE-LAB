"""Confirms the API never fabricates a structural/CFD simulation result.

Only the thermal analysis has a validated numerical solver today (see
app.module2_simulation.solver_registry). Requesting structural or wind_load
analyses through the real, real-CadQuery-backed FastAPI app must return a
clear HTTP 501 rather than a fake success payload.
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("analysis_type", ["structural", "wind_load"])
def test_unsupported_analysis_returns_501_not_fabricated_result(authorized_client, analysis_type):
    response = authorized_client.post(
        "/api/simulate/run",
        json={
            "design_id": "d-1",
            "geometry_type": "tower",
            "analysis_type": analysis_type,
            "material": "concrete",
            "boundary_conditions": {},
        },
    )
    assert response.status_code == 501
    assert analysis_type in response.json()["detail"]


def test_thermal_analysis_still_returns_real_result(authorized_client):
    response = authorized_client.post(
        "/api/simulate/run",
        json={
            "design_id": "d-1",
            "geometry_type": "tower",
            "analysis_type": "thermal",
            "material": "concrete",
            "boundary_conditions": {"grid_resolution": 8, "max_iterations": 50},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_type"] == "thermal"
    assert "max_temperature_c" in payload["summary_metrics"]


def test_advisor_reports_supported_subset(authorized_client):
    response = authorized_client.post("/api/simulate/advisor", json={"model_type": "bridge"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["recommended"] == ["structural", "vibration", "thermal"]
    assert payload["supported"] == ["thermal"]
