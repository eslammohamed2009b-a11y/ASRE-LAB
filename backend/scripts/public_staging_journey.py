"""Deterministic external-HTTP journey for the disposable Docker staging stack."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from typing import Any

import httpx
from jose import jwt


TERMINAL = {"completed", "failed", "partial_failure", "cancelled"}


def require(response: httpx.Response, status: int) -> dict[str, Any]:
    if response.status_code != status:
        raise AssertionError(
            f"{response.request.method} {response.request.url.path}: "
            f"expected {status}, got {response.status_code}: {response.text[:500]}"
        )
    return response.json() if response.content else {}


def headers(user_id: str) -> dict[str, str]:
    token = jwt.encode(
        {"sub": user_id, "role": "authenticated"},
        os.environ["JWT_SECRET_KEY"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def poll(client: httpx.Client, path: str, auth: dict[str, str], timeout: int = 180) -> dict:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = require(client.get(path, headers=auth), 200)
        if last.get("status") in TERMINAL:
            return last
        time.sleep(0.5)
    raise AssertionError(f"Timed out polling {path}; last state={last.get('status')}")


def simulation(
    client: httpx.Client,
    auth: dict[str, str],
    *,
    experiment_id: str,
    design_id: str,
    solver_id: str,
    material: str,
    geometry: dict,
    boundary: dict,
    key: str,
) -> tuple[dict, list[dict]]:
    payload = {
        "solver_id": solver_id,
        "experiment_id": experiment_id,
        "design_id": design_id,
        "material": {"name": material},
        "geometry": geometry,
        "boundary_conditions": boundary,
        "numerical_settings": {"max_iterations": 3000, "tolerance": 1e-7},
    }
    first = require(
        client.post(
            "/api/simulations",
            json=payload,
            headers={**auth, "Idempotency-Key": key},
        ),
        202,
    )
    replay = require(
        client.post(
            "/api/simulations",
            json=payload,
            headers={**auth, "Idempotency-Key": key},
        ),
        202,
    )
    assert first["simulation_id"] == replay["simulation_id"]
    terminal = poll(client, f"/api/simulations/{first['simulation_id']}", auth)
    assert terminal["status"] == "completed", terminal
    result = require(
        client.get(f"/api/simulations/{first['simulation_id']}/results", headers=auth),
        200,
    )
    assert result["result"]["convergence"]["converged"] is True
    fields = require(
        client.get(f"/api/simulations/{first['simulation_id']}/fields", headers=auth),
        200,
    )
    for field in fields:
        downloaded = client.get(
            f"/api/simulations/{first['simulation_id']}/fields/{field['id']}/download",
            headers=auth,
        )
        assert downloaded.status_code == 200
        assert hashlib.sha256(downloaded.content).hexdigest() == field["checksum_sha256"]
    return result, fields


def run(base_url: str) -> dict[str, Any]:
    user_a = os.environ["SUPABASE_TEST_USER_A_ID"]
    user_b = os.environ["SUPABASE_TEST_USER_B_ID"]
    auth_a, auth_b = headers(user_a), headers(user_b)
    contract = json.loads(
        open(os.path.join(os.path.dirname(__file__), "..", "openapi-contract.json"), encoding="utf-8").read()
    )
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=120.0) as client:
        assert require(client.get("/health"), 200)["status"] == "ok"
        schema_response = client.get("/openapi.json")
        schema = require(schema_response, 200)
        canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
        assert hashlib.sha256(canonical.encode()).hexdigest() == contract["openapi_sha256"]
        assert client.get("/api/simulations/capabilities").status_code == 401
        assert client.post("/api/simulations", json={}, headers=auth_a).status_code == 422

        batch_payload = {
            "base_params": {
                "geometry_type": "tower",
                "base_length_m": 10.0,
                "height_m": 20.0,
                "wall_thickness_m": 0.5,
                "slope_angle_deg": 0.0,
                "material": "steel",
            },
            "variation_count": 3,
            "vary_fields": ["height_m"],
            "variation_range_pct": 0.1,
        }
        batch_headers = {**auth_a, "Idempotency-Key": "public-staging-cad-v1"}
        first = require(client.post("/api/design/generate-batch", json=batch_payload, headers=batch_headers), 202)
        replay = require(client.post("/api/design/generate-batch", json=batch_payload, headers=batch_headers), 202)
        assert first["job_id"] == replay["job_id"]
        job = poll(client, f"/api/jobs/{first['job_id']}", auth_a)
        assert job["status"] == "completed" and job["completed_count"] == 3
        results = require(client.get(f"/api/jobs/{first['job_id']}/results", headers=auth_a), 200)
        assert len(results["designs"]) == 3
        assert len({item["design_model_id"] for item in results["designs"]}) == 3
        design = results["designs"][0]
        formats = {item["file_format"]: item for item in design["files"]}
        assert {"stl", "step"} <= formats.keys()
        for item in formats.values():
            downloaded = client.get(f"/api/design/export/{item['design_file_id']}", headers=auth_a)
            assert downloaded.status_code == 200
            assert hashlib.sha256(downloaded.content).hexdigest() == item["checksum_sha256"]

        experiment_id = first["experiment_id"]
        design_id = design["design_model_id"]
        simulation_specs = [
            ("thermal_conduction_v1", "steel", {"dimension": "1d", "length_m": 1, "num_elements": 10},
             {"ambient_temperature_c": 20, "prescribed_temperature_c": 80}),
            ("structural_linear_1d_v1", "steel",
             {"dimension": "1d", "length_m": 1, "cross_section_area_m2": 0.01, "num_elements": 10},
             {"axial_load_n": 1000}),
            ("modal_eigen_1d_v1", "steel", {"dimension": "1d"},
             {"point_mass_kg": 2, "spring_stiffness_n_m": 200}),
            ("acoustic_duct_1d_v1", "air",
             {"dimension": "1d", "length_m": 1, "num_elements": 40},
             {"source_frequency_hz": 80, "source_pressure_pa": 1,
              "acoustic_left_boundary": "driven", "acoustic_right_boundary": "pressure_release"}),
        ]
        simulations = []
        field_count = 0
        for index, (solver, material, geometry, boundary) in enumerate(simulation_specs):
            result, fields = simulation(
                client, auth_a, experiment_id=experiment_id, design_id=design_id,
                solver_id=solver, material=material, geometry=geometry, boundary=boundary,
                key=f"public-staging-simulation-{index}",
            )
            simulations.append(result["simulation_id"])
            field_count += len(fields)
        assert field_count >= 6

        coupling = require(
            client.post(
                "/api/couplings/thermal-structural",
                headers=auth_a,
                json={
                    "experiment_id": experiment_id,
                    "design_id": design_id,
                    "material": "steel",
                    "length_m": 1,
                    "cross_section_area_m2": 0.01,
                    "num_elements": 10,
                    "reference_temperature_c": 20,
                    "hot_end_temperature_c": 120,
                },
            ),
            200,
        )
        assert coupling["status"] == "completed"
        analysis = require(
            client.post(f"/api/analyze/experiments/{experiment_id}", headers=auth_a, json={}),
            201,
        )
        proposal = require(
            client.post(
                "/api/design-feedback/proposals",
                headers=auth_a,
                json={
                    "analysis_id": analysis["id"],
                    "source_design_id": design_id,
                    "parameter_bounds": {"height_m": [15, 25]},
                },
            ),
            200,
        )
        assert client.post(
            f"/api/design-feedback/proposals/{proposal['id']}/execute", headers=auth_a
        ).status_code == 409
        accepted = require(
            client.post(f"/api/design-feedback/proposals/{proposal['id']}/accept", headers=auth_a),
            200,
        )
        assert accepted["status"] == "accepted"
        iteration = require(
            client.post(f"/api/design-feedback/proposals/{proposal['id']}/execute", headers=auth_a),
            200,
        )
        assert iteration["status"] == "completed"
        assert iteration["parent_design_ids"] == [design_id]
        assert len(iteration["child_design_ids"]) == 1
        child_id = iteration["child_design_ids"][0]
        next_result, _ = simulation(
            client, auth_a, experiment_id=experiment_id, design_id=child_id,
            solver_id="thermal_conduction_v1", material="steel",
            geometry={"dimension": "1d", "length_m": 1, "num_elements": 8},
            boundary={"ambient_temperature_c": 20, "prescribed_temperature_c": 30},
            key="public-staging-next-iteration",
        )
        assert next_result["design_id"] == child_id

        denied = [
            f"/api/jobs/{first['job_id']}",
            f"/api/jobs/{first['job_id']}/results",
            f"/api/simulations/{simulations[0]}",
            f"/api/simulations/{simulations[0]}/results",
            f"/api/analyze/{analysis['id']}",
            f"/api/couplings/{coupling['id']}",
            f"/api/design-feedback/proposals/{proposal['id']}",
            f"/api/design-feedback/experiments/{experiment_id}/iterations",
            f"/api/design/export/{formats['stl']['design_file_id']}",
        ]
        for path in denied:
            assert client.get(path, headers=auth_b).status_code == 404, path
        assert client.post(
            f"/api/design-feedback/proposals/{proposal['id']}/accept", headers=auth_b
        ).status_code == 404
        assert client.post(
            f"/api/design-feedback/proposals/{proposal['id']}/execute", headers=auth_b
        ).status_code == 404

        return {
            "job_id": first["job_id"],
            "experiment_id": experiment_id,
            "design_id": design_id,
            "simulation_ids": simulations,
            "analysis_id": analysis["id"],
            "proposal_id": proposal["id"],
            "iteration_id": iteration["id"],
            "child_design_id": child_id,
            "stl_file_id": formats["stl"]["design_file_id"],
            "stl_checksum": formats["stl"]["checksum_sha256"],
            "http_statuses": {
                "health": 200,
                "openapi": 200,
                "unauthorized": 401,
                "malformed": 422,
                "owner_denial": 404,
                "premature_execute": 409,
            },
        }


def verify(base_url: str, state: dict[str, Any]) -> dict[str, Any]:
    auth_a = headers(os.environ["SUPABASE_TEST_USER_A_ID"])
    auth_b = headers(os.environ["SUPABASE_TEST_USER_B_ID"])
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0) as client:
        assert require(client.get("/health"), 200)["status"] == "ok"
        assert require(client.get(f"/api/jobs/{state['job_id']}", headers=auth_a), 200)["status"] == "completed"
        assert require(client.get(f"/api/analyze/{state['analysis_id']}", headers=auth_a), 200)["id"] == state["analysis_id"]
        iterations = require(
            client.get(
                f"/api/design-feedback/experiments/{state['experiment_id']}/iterations",
                headers=auth_a,
            ),
            200,
        )
        assert any(item["id"] == state["iteration_id"] for item in iterations)
        artifact = client.get(f"/api/design/export/{state['stl_file_id']}", headers=auth_a)
        assert artifact.status_code == 200
        assert hashlib.sha256(artifact.content).hexdigest() == state["stl_checksum"]
        assert client.get(f"/api/jobs/{state['job_id']}", headers=auth_b).status_code == 404
    return {"restart_health": 200, "persistence": "verified", "ownership": 404}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--verify-state-b64")
    args = parser.parse_args()
    result = (
        verify(
            args.base_url,
            json.loads(base64.b64decode(args.verify_state_b64).decode()),
        )
        if args.verify_state_b64
        else run(args.base_url)
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
