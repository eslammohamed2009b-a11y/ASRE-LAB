Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$backend = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $backend
$envFile = Join-Path $backend "supabase\.env"

Push-Location $root
try {
    git check-ignore -q -- backend/supabase/.env
    if ($LASTEXITCODE -ne 0) {
        throw "Refusing to load staging secrets: backend/supabase/.env is not gitignored."
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing gitignored staging environment file."
}

$values = [ordered]@{}
foreach ($line in [IO.File]::ReadAllLines($envFile)) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
        continue
    }
    $separator = $line.IndexOf("=")
    if ($separator -lt 1) { continue }
    $values[$line.Substring(0, $separator)] = $line.Substring($separator + 1)
}

$required = @(
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_TEST_USER_A_EMAIL",
    "SUPABASE_TEST_USER_A_PASSWORD",
    "SUPABASE_TEST_USER_B_EMAIL",
    "SUPABASE_TEST_USER_B_PASSWORD"
)
foreach ($name in $required) {
    if (-not $values[$name]) { throw "Missing required staging session input: $name" }
}

$headers = @{
    apikey = $values["SUPABASE_ANON_KEY"]
    "Content-Type" = "application/json"
}
try {
    $sessionA = Invoke-RestMethod -Method Post `
        -Uri ($values["SUPABASE_URL"] + "/auth/v1/token?grant_type=password") `
        -Headers $headers `
        -Body (@{
            email = $values["SUPABASE_TEST_USER_A_EMAIL"]
            password = $values["SUPABASE_TEST_USER_A_PASSWORD"]
        } | ConvertTo-Json)
    $sessionB = Invoke-RestMethod -Method Post `
        -Uri ($values["SUPABASE_URL"] + "/auth/v1/token?grant_type=password") `
        -Headers $headers `
        -Body (@{
            email = $values["SUPABASE_TEST_USER_B_EMAIL"]
            password = $values["SUPABASE_TEST_USER_B_PASSWORD"]
        } | ConvertTo-Json)
}
catch {
    throw "Failed to refresh disposable staging test-user sessions."
}

$values["SUPABASE_TEST_USER_A_JWT"] = $sessionA.access_token
$values["SUPABASE_TEST_USER_B_JWT"] = $sessionB.access_token
$output = foreach ($entry in $values.GetEnumerator()) {
    "$($entry.Key)=$($entry.Value)"
}
[IO.File]::WriteAllLines($envFile, $output, (New-Object Text.UTF8Encoding($false)))

Write-Output "Disposable staging sessions refreshed (values withheld)."
