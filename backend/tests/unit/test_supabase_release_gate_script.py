"""Regression contracts for the Windows-host/live-Supabase release gate."""

from pathlib import Path


BACKEND = Path(__file__).resolve().parents[2]


def test_gate_refreshes_sessions_and_runs_python_inside_docker():
    script = (BACKEND / "scripts/validate_supabase_release_gate.ps1").read_text(
        encoding="utf-8"
    )

    assert "refresh_supabase_test_sessions.ps1" in script
    assert "docker build -t asre-supabase-release-gate backend" in script
    assert "docker run --rm --env-file backend/supabase/.env" in script
    assert "python -m pytest -p no:cacheprovider -m external -q" in script
    assert "npx.cmd --yes supabase@2.109.1 db push --linked" in script


def test_session_refresh_refuses_non_ignored_secret_file_and_withholds_values():
    script = (BACKEND / "scripts/refresh_supabase_test_sessions.ps1").read_text(
        encoding="utf-8"
    )

    assert "git check-ignore -q -- backend/supabase/.env" in script
    assert "Refusing to load staging secrets" in script
    assert "SUPABASE_TEST_USER_A_JWT" in script
    assert "SUPABASE_TEST_USER_B_JWT" in script
    assert "values withheld" in script
