Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$backend = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $backend
$envFile = Join-Path $backend "supabase\.env"

& (Join-Path $PSScriptRoot "refresh_supabase_test_sessions.ps1")
if (-not $?) { throw "Staging session refresh failed." }

foreach ($line in [IO.File]::ReadAllLines($envFile)) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
        continue
    }
    $separator = $line.IndexOf("=")
    if ($separator -lt 1) { continue }
    [Environment]::SetEnvironmentVariable(
        $line.Substring(0, $separator),
        $line.Substring($separator + 1),
        "Process"
    )
}

if ($env:ASRE_SUPABASE_TARGET -notin @("staging", "disposable")) {
    throw "Set ASRE_SUPABASE_TARGET to staging or disposable. Production is refused."
}

$required = @(
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "SUPABASE_DB_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
    "SUPABASE_TEST_USER_A_ID",
    "SUPABASE_TEST_USER_A_JWT",
    "SUPABASE_TEST_USER_B_ID",
    "SUPABASE_TEST_USER_B_JWT"
)
foreach ($name in $required) {
    if (-not (Test-Path "Env:$name")) {
        throw "Missing required environment variable: $name"
    }
}

if (-not (Get-Command npx.cmd -ErrorAction SilentlyContinue)) {
    throw "Node npx is required for the project-local Supabase CLI."
}

Push-Location $root
try {
    docker build -t asre-supabase-release-gate backend
    if ($LASTEXITCODE -ne 0) { throw "Backend Python 3.11 image build failed." }

    docker run --rm -v "${root}:/workspace" -w /workspace/backend `
        asre-supabase-release-gate python -m pytest -p no:cacheprovider `
        tests/unit/test_migration_009_contract.py -q
    if ($LASTEXITCODE -ne 0) { throw "Migration contract validation failed." }

    $env:NODE_USE_SYSTEM_CA = "1"
    npx.cmd --yes supabase@2.109.1 db push --linked --include-all --workdir backend --yes
    if ($LASTEXITCODE -ne 0) { throw "Supabase migration push failed." }

    docker run --rm --env-file backend/supabase/.env `
        -v "${root}:/workspace" -w /workspace/backend `
        asre-supabase-release-gate python -m pytest -p no:cacheprovider -m external -q
    if ($LASTEXITCODE -ne 0) { throw "External Supabase validation failed." }
}
finally {
    Pop-Location
}
