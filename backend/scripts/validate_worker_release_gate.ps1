Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "BLOCKED: Docker is required for real Redis and separate Celery worker validation."
}

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $root
try {
    docker compose up -d --build redis worker api
    if ($LASTEXITCODE -ne 0) { throw "Worker stack failed to start." }
    docker compose ps
    if ($LASTEXITCODE -ne 0) { throw "Worker stack status failed." }
    # Deterministic boundary suite covers durable transitions, cancellation,
    # idempotency, concurrency isolation, partial failure, and restart state.
    Push-Location "$root\backend"
    python -m pytest -p no:cacheprovider tests/integration/test_batch_jobs.py `
      tests/integration/test_module2_simulations_api.py tests/e2e/test_batch_ownership_e2e.py -q
    if ($LASTEXITCODE -ne 0) { throw "Worker/job validation suite failed." }
    Pop-Location
    Write-Output "Manual live check remains required: terminate the worker during a running job, restart it, and verify durable recovery/retry policy."
}
finally {
    docker compose down
    Pop-Location
}
