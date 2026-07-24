"""Contracts for the disposable public Docker/Supabase staging boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"


def test_staging_overlay_uses_supabase_and_bounded_worker_redelivery():
    overlay = (ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")

    assert "SUPABASE_URL:" in overlay
    assert "SUPABASE_KEY:" in overlay
    assert 'CELERY_BROKER_VISIBILITY_TIMEOUT: "10"' in overlay
    assert 'LOCAL_PERSISTENCE_DB_PATH: ""' in overlay
    assert 'LOCAL_STORAGE_ROOT: ""' in overlay
    assert "ports:" not in overlay


def test_public_journey_covers_contract_security_science_and_restart_reconstruction():
    journey = (BACKEND / "scripts/public_staging_journey.py").read_text(
        encoding="utf-8"
    )

    for evidence in (
        '"/health"',
        '"/openapi.json"',
        '"thermal_conduction_v1"',
        '"structural_linear_1d_v1"',
        '"modal_eigen_1d_v1"',
        '"acoustic_duct_1d_v1"',
        '"/api/couplings/thermal-structural"',
        '"/api/design-feedback/proposals"',
        '"premature_execute": 409',
        '"owner_denial": 404',
        '"stl_checksum"',
        "args.verify_state_b64",
    ):
        assert evidence in journey
