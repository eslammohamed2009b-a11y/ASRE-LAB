Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "BLOCKED: Docker is required for real worker-loss recovery validation."
}

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:COMPOSE_PROJECT_NAME = "asre-worker-loss-$PID"
$env:CELERY_BROKER_VISIBILITY_TIMEOUT = "10"
$exitCode = 1
$jobId = $null
$taskId = $null

function Write-Evidence([string]$Event, [object]$Data) {
    [ordered]@{
        timestamp = [DateTimeOffset]::UtcNow.ToString("o")
        event = $Event
        data = $Data
    } | ConvertTo-Json -Compress -Depth 8 | Write-Output
}

function Invoke-ProbeStatus {
    $raw = docker compose exec -T api python scripts/worker_loss_probe.py status $jobId $taskId
    if ($LASTEXITCODE -ne 0) { throw "Probe status command failed (exit $LASTEXITCODE)." }
    return ($raw | Select-Object -Last 1 | ConvertFrom-Json)
}

Push-Location $root
try {
    docker compose up -d --build redis worker api
    if ($LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE; throw "Stack startup failed." }
    docker compose ps
    if ($LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE; throw "Stack status failed." }

    docker compose cp backend/scripts api:/app/scripts
    if ($LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE; throw "Probe copy failed." }

    $dispatchRaw = docker compose exec -T api python scripts/worker_loss_probe.py dispatch
    if ($LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE; throw "Dispatch failed." }
    $dispatch = $dispatchRaw | Select-Object -Last 1 | ConvertFrom-Json
    $jobId = $dispatch.job_id
    $taskId = $dispatch.task_id
    Write-Evidence "dispatched" $dispatch

    $active = $null
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $active = Invoke-ProbeStatus
        if ($active.durable_state -eq "running" -and $active.completed_count -ge 1) { break }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $active -or $active.durable_state -ne "running" -or $active.completed_count -lt 1) {
        throw "Job did not reach the deterministic active checkpoint."
    }
    Write-Evidence "active_checkpoint" $active

    docker compose kill -s SIGKILL worker
    $killExitCode = $LASTEXITCODE
    Write-Evidence "worker_sigkill" @{ exit_code = $killExitCode; containers = (docker compose ps -a --format json) }
    if ($killExitCode -ne 0) { $exitCode = $killExitCode; throw "Worker SIGKILL failed." }

    $afterLoss = Invoke-ProbeStatus
    Write-Evidence "after_worker_loss" $afterLoss
    if ($afterLoss.durable_state -ne "running" -or $afterLoss.completed_count -lt 1) {
        throw "Durable checkpoint was not retained after worker loss."
    }

    docker compose up -d worker
    $restartExitCode = $LASTEXITCODE
    Write-Evidence "worker_restarted" @{ exit_code = $restartExitCode; containers = (docker compose ps --format json) }
    if ($restartExitCode -ne 0) { $exitCode = $restartExitCode; throw "Worker restart failed." }

    $terminal = $null
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(180)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $terminal = Invoke-ProbeStatus
        if ($terminal.durable_state -in @("completed", "failed", "partial_failure", "cancelled")) { break }
        Start-Sleep -Milliseconds 500
    }
    Write-Evidence "terminal" $terminal
    if ($null -eq $terminal -or $terminal.durable_state -ne "completed") {
        throw "Redelivered job did not reach completed state."
    }

    $expected = [int]$terminal.requested_count
    if (
        $terminal.completed_count -ne $expected -or
        $terminal.failed_count -ne 0 -or
        $terminal.model_count -ne $expected -or
        $terminal.file_count -ne (2 * $expected) -or
        $terminal.unique_model_ids -ne $expected -or
        $terminal.unique_file_ids -ne (2 * $expected) -or
        $terminal.unique_object_keys -ne (2 * $expected) -or
        (@($terminal.variation_indices | Select-Object -Unique).Count -ne $expected)
    ) {
        throw "Duplicate or missing durable records/artifacts detected after recovery."
    }

    docker compose restart api
    $apiRestartExitCode = $LASTEXITCODE
    if ($apiRestartExitCode -ne 0) { $exitCode = $apiRestartExitCode; throw "API restart failed." }
    $persisted = Invoke-ProbeStatus
    Write-Evidence "after_api_restart" $persisted
    if (
        $persisted.durable_state -ne "completed" -or
        $persisted.model_count -ne $expected -or
        $persisted.file_count -ne (2 * $expected)
    ) {
        throw "Final durable state did not survive API/repository restart."
    }

    $exitCode = 0
}
catch {
    [Console]::Error.WriteLine($_)
}
finally {
    docker compose logs --timestamps --no-color worker 2>&1 | Select-Object -Last 80
    docker compose down -v
    $cleanupExitCode = $LASTEXITCODE
    Write-Evidence "cleanup" @{ exit_code = $cleanupExitCode }
    Pop-Location
}

exit $exitCode
