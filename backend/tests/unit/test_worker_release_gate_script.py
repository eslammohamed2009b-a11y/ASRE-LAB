"""Regression coverage for the Windows-host/Docker dependency boundary."""

from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "validate_worker_release_gate.ps1"
)


def test_dependency_sensitive_worker_suite_runs_inside_api_container():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "docker compose up -d --build redis worker api" in script
    assert "docker compose cp backend/tests api:/app/tests" in script
    assert "docker compose cp backend/scripts api:/app/scripts" in script
    assert "docker compose exec -T api python -m pytest" in script
    assert "python -m pytest -p no:cacheprovider" not in script.replace(
        "docker compose exec -T api python -m pytest -p no:cacheprovider", ""
    )


def test_container_pytest_exit_code_survives_finally_cleanup():
    script = SCRIPT.read_text(encoding="utf-8")

    pytest_command = script.index("docker compose exec -T api python -m pytest")
    capture = script.index("$exitCode = $LASTEXITCODE", pytest_command)
    finally_block = script.index("finally {", capture)
    cleanup = script.index("docker compose down", finally_block)
    propagated_exit = script.index("exit $exitCode", cleanup)

    assert pytest_command < capture < finally_block < cleanup < propagated_exit
