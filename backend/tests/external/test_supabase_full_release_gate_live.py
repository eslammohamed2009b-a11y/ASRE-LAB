"""Complete live two-user staging lifecycle and RLS release gate."""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.external

_REQUIRED = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
    "SUPABASE_TEST_USER_A_ID",
    "SUPABASE_TEST_USER_A_JWT",
    "SUPABASE_TEST_USER_B_ID",
    "SUPABASE_TEST_USER_B_JWT",
)
_READY = all(os.environ.get(name) for name in _REQUIRED)


def _client(jwt: str):
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
    client.postgrest.auth(jwt)
    return client


@pytest.mark.skipif(not _READY, reason="BLOCKED: complete live staging credentials are not configured.")
def test_complete_two_user_lifecycle_rls_and_reconnect() -> None:
    from postgrest.exceptions import APIError
    from supabase import create_client

    owner_a = str(uuid.UUID(os.environ["SUPABASE_TEST_USER_A_ID"]))
    owner_b = str(uuid.UUID(os.environ["SUPABASE_TEST_USER_B_ID"]))
    service = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    client_a = _client(os.environ["SUPABASE_TEST_USER_A_JWT"])
    client_b = _client(os.environ["SUPABASE_TEST_USER_B_JWT"])

    service.table("profiles").upsert({"id": owner_a, "full_name": "ASRE live gate A"}).execute()
    service.table("profiles").upsert({"id": owner_b, "full_name": "ASRE live gate B"}).execute()

    experiment_id = None
    try:
        experiment = client_a.table("experiments").insert(
            {
                "user_id": owner_a,
                "name": "Complete staging release lifecycle",
                "status": "running",
                "input_specification": {"gate": "live-staging"},
            }
        ).execute().data[0]
        experiment_id = experiment["id"]

        design = client_a.table("design_models").insert(
            {
                "experiment_id": experiment_id,
                "user_id": owner_a,
                "geometry_family": "pyramid",
                "parameters": {"height_m": 50},
                "units": {"length": "m"},
                "variation_index": 0,
                "generation_status": "completed",
            }
        ).execute().data[0]
        design_id = design["id"]

        object_key = f"users/{owner_a}/experiments/{experiment_id}/designs/{design_id}/model.stl"
        design_file = client_a.table("design_files").insert(
            {
                "design_model_id": design_id,
                "experiment_id": experiment_id,
                "user_id": owner_a,
                "file_format": "stl",
                "storage_provider": "supabase",
                "object_key": object_key,
                "file_size_bytes": 4,
                "checksum_sha256": "a" * 64,
                "media_type": "model/stl",
            }
        ).execute().data[0]

        generation_job = client_a.table("generation_jobs").insert(
            {
                "experiment_id": experiment_id,
                "user_id": owner_a,
                "job_type": "design_batch",
                "status": "completed",
                "requested_count": 1,
                "completed_count": 1,
                "progress_percent": 100,
                "idempotency_key": f"live-{uuid.uuid4()}",
            }
        ).execute().data[0]

        simulation = client_a.table("simulation_jobs").insert(
            {
                "experiment_id": experiment_id,
                "design_id": design_id,
                "user_id": owner_a,
                "solver_id": "thermal_conduction_v1",
                "status": "completed",
                "progress_percent": 100,
                "idempotency_key": f"live-sim-{uuid.uuid4()}",
            }
        ).execute().data[0]
        simulation_id = simulation["id"]

        client_a.table("simulation_inputs").insert(
            {
                "simulation_id": simulation_id,
                "material_name": "steel",
                "material_properties": {"conductivity_w_mk": 50},
                "units": {"temperature": "C"},
                "initial_conditions": {"temperature_c": 20},
                "boundary_conditions": {"temperature_c": 100},
                "numerical_settings": {"elements": 10},
            }
        ).execute()
        client_a.table("simulation_results").insert(
            {
                "simulation_id": simulation_id,
                "solver_id": "thermal_conduction_v1",
                "solver_version": "release-gate",
                "governing_equations": ["steady heat equation"],
                "assumptions": ["one dimensional"],
                "converged": True,
                "residual": 1e-9,
                "iteration_count": 2,
                "tolerance": 1e-6,
                "summary_metrics": {"maximum_temperature_c": 100},
                "field_values": [20, 100],
                "status": "completed",
                "numerical_method": "finite_difference",
                "residual_history": [1e-3, 1e-9],
                "validation_metadata": {"gate": "live"},
                "reproducibility_hash": "b" * 64,
                "source_design_id": design_id,
            }
        ).execute()
        field = client_a.table("simulation_field_results").insert(
            {
                "simulation_id": simulation_id,
                "user_id": owner_a,
                "variable_name": "temperature",
                "unit": "degC",
                "format": "numpy_npz",
                "format_version": "1",
                "dimensions": 1,
                "axes": [{"name": "x", "unit": "m"}],
                "array_shape": [2],
                "grid_metadata": {"spacing_m": 1},
                "storage_object_key": object_key.replace("model.stl", "temperature.npz"),
                "checksum_sha256": "c" * 64,
                "byte_size": 128,
                "minimum": 20,
                "maximum": 100,
                "mean": 60,
                "preview": [20, 100],
                "reproducibility_hash": "d" * 64,
            }
        ).execute().data[0]

        analysis = client_a.table("experiment_analyses").insert(
            {
                "experiment_id": experiment_id,
                "user_id": owner_a,
                "analysis_type": "thermal_summary",
                "status": "completed",
                "dataset_hash": "e" * 64,
                "configuration": {"source": simulation_id},
                "result": {"maximum_temperature_c": 100},
                "warnings": [],
                "source_design_ids": [design_id],
                "source_simulation_ids": [simulation_id],
                "data_quality": {"complete": True},
                "engine_version": "release-gate",
                "reproducibility_hash": "f" * 64,
            }
        ).execute().data[0]
        coupled = client_a.table("experiment_analyses").insert(
            {
                "experiment_id": experiment_id,
                "user_id": owner_a,
                "analysis_type": "thermal_structural_coupling",
                "status": "completed",
                "dataset_hash": "1" * 64,
                "configuration": {"coupled": True},
                "result": {"validated": True},
                "warnings": [],
                "source_design_ids": [design_id],
                "source_simulation_ids": [simulation_id],
                "data_quality": {"complete": True},
                "engine_version": "release-gate",
                "reproducibility_hash": "2" * 64,
            }
        ).execute().data[0]

        proposal = client_a.table("design_improvement_proposals").insert(
            {
                "experiment_id": experiment_id,
                "analysis_id": coupled["id"],
                "user_id": owner_a,
                "status": "generated",
                "modifications": [{"parameter": "height_m", "from": 50, "to": 52}],
                "evidence": [{"analysis_id": analysis["id"]}],
                "source_design_ids": [design_id],
                "expected_tradeoffs": [{"temperature": "lower"}],
                "confidence_limitations": ["staging validation only"],
                "constraint_checks": {"height_m": {"within_bounds": True}},
            }
        ).execute().data[0]
        proposal_id = proposal["id"]
        assert client_a.table("design_improvement_proposals").update(
            {"status": "accepted"}
        ).eq("id", proposal_id).execute().data[0]["status"] == "accepted"

        child_design = client_a.table("design_models").insert(
            {
                "experiment_id": experiment_id,
                "user_id": owner_a,
                "geometry_family": "pyramid",
                "parameters": {"height_m": 52},
                "units": {"length": "m"},
                "variation_index": 1,
                "generation_status": "completed",
            }
        ).execute().data[0]
        iteration = client_a.table("design_iterations").insert(
            {
                "experiment_id": experiment_id,
                "proposal_id": proposal_id,
                "user_id": owner_a,
                "parent_design_ids": [design_id],
                "child_design_ids": [child_design["id"]],
                "status": "completed",
            }
        ).execute().data[0]
        assert client_a.table("design_improvement_proposals").update(
            {"status": "executed"}
        ).eq("id", proposal_id).execute().data[0]["status"] == "executed"

        owner_rows = {
            "design_models": design_id,
            "design_files": design_file["id"],
            "generation_jobs": generation_job["id"],
            "simulation_jobs": simulation_id,
            "simulation_inputs": simulation_id,
            "simulation_results": simulation_id,
            "simulation_field_results": field["id"],
            "experiment_analyses": analysis["id"],
            "design_improvement_proposals": proposal_id,
            "design_iterations": iteration["id"],
        }
        for table, row_id in owner_rows.items():
            id_column = "simulation_id" if table in {"simulation_inputs", "simulation_results"} else "id"
            assert client_b.table(table).select("*").eq(id_column, row_id).execute().data == []

        assert client_b.table("experiments").update(
            {"name": "forbidden"}
        ).eq("id", experiment_id).execute().data == []
        assert client_b.table("design_improvement_proposals").update(
            {"status": "accepted"}
        ).eq("id", proposal_id).execute().data == []
        assert client_b.table("design_iterations").update(
            {"status": "failed"}
        ).eq("id", iteration["id"]).execute().data == []
        with pytest.raises(APIError):
            client_b.table("generation_jobs").insert(
                {
                    "experiment_id": experiment_id,
                    "user_id": owner_a,
                    "requested_count": 1,
                }
            ).execute()

        fresh_a = _client(os.environ["SUPABASE_TEST_USER_A_JWT"])
        assert fresh_a.table("experiments").select("id").eq("id", experiment_id).execute().data
        assert fresh_a.table("generation_jobs").select("id").eq(
            "id", generation_job["id"]
        ).execute().data
        lineage = fresh_a.table("design_iterations").select("*").eq(
            "id", iteration["id"]
        ).execute().data[0]
        assert lineage["parent_design_ids"] == [design_id]
        assert lineage["child_design_ids"] == [child_design["id"]]
    finally:
        if experiment_id:
            service.table("experiments").delete().eq("id", experiment_id).execute()
