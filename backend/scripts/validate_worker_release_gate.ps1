Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "BLOCKED: Docker is required for real Redis and separate Celery worker validation."
}

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$exitCode = 1
Push-Location $root
try {
    docker compose up -d --build redis worker api
    if ($LASTEXITCODE -ne 0) {
        $exitCode = $LASTEXITCODE
        throw "Worker stack failed to start."
    }
    docker compose ps
    if ($LASTEXITCODE -ne 0) {
        $exitCode = $LASTEXITCODE
        throw "Worker stack status failed."
    }

    # The production image contains the complete Python 3.11 dependency stack
    # but does not bake test sources into the image. Copy them into the already
    # running API service so Windows never imports backend dependencies.
    docker compose cp backend/tests api:/app/tests
    if ($LASTEXITCODE -ne 0) {
        $exitCode = $LASTEXITCODE
        throw "Failed to copy worker/job tests into the API container."
    }
    docker compose cp backend/scripts api:/app/scripts
    if ($LASTEXITCODE -ne 0) {
        $exitCode = $LASTEXITCODE
        throw "Failed to copy release-gate scripts into the API container."
    }
    docker compose cp backend/pytest.ini api:/app/pytest.ini
    if ($LASTEXITCODE -ne 0) {
        $exitCode = $LASTEXITCODE
        throw "Failed to copy pytest configuration into the API container."
    }
    docker compose cp docker-compose.yml api:/app/docker-compose.yml
    if ($LASTEXITCODE -ne 0) {
        $exitCode = $LASTEXITCODE
        throw "Failed to copy Compose configuration into the API container."
    }

    # Deterministic boundary suite covers durable transitions, cancellation,
    # idempotency, concurrency isolation, partial failure, and restart state.
    docker compose exec -T api python -m pytest -p no:cacheprovider `
      tests/unit/test_worker_release_gate_script.py `
      tests/unit/test_worker_loss_recovery_script.py `
      tests/integration/test_batch_jobs.py `
      tests/integration/test_module2_simulations_api.py tests/e2e/test_batch_ownership_e2e.py -q
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        [Console]::Error.WriteLine(
            "Worker/job validation suite failed inside the API container (exit code $exitCode)."
        )
    }
    else {
        Write-Output "Worker/job container suite passed. Run validate_worker_loss_recovery.ps1 for the destructive SIGKILL/redelivery release probe."
    }
}
catch {
    [Console]::Error.WriteLine($_)
}
finally {
    docker compose down
    Pop-Location
}

exit $exitCode
