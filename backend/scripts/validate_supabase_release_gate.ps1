Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

if (-not (Get-Command supabase -ErrorAction SilentlyContinue)) {
    throw "Supabase CLI is required."
}

$backend = Split-Path -Parent $PSScriptRoot

Push-Location $backend
try {
    python -m pytest -p no:cacheprovider tests/unit/test_migration_009_contract.py -q
    if ($LASTEXITCODE -ne 0) { throw "Migration contract validation failed." }

    supabase db push --db-url $env:SUPABASE_DB_URL --include-all
    if ($LASTEXITCODE -ne 0) { throw "Supabase migration push failed." }

    python -m pytest -p no:cacheprovider -m external -q
    if ($LASTEXITCODE -ne 0) { throw "External Supabase validation failed." }
}
finally {
    Pop-Location
}
