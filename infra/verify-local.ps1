param(
    [switch]$RestartPostgres,
    [int]$ApiPort = 8004,
    [int]$UiPort = 5177
)

$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot

$testArgs = @("-NoProfile", "-File", (Join-Path $scriptRoot "test-local.ps1"))
if ($RestartPostgres) {
    $testArgs += "-RestartPostgres"
}
& pwsh @testArgs
if ($LASTEXITCODE -ne 0) {
    throw "Backend tests, schema check, compile check, or Ruff gate failed."
}

pnpm --dir frontend-web build
if ($LASTEXITCODE -ne 0) {
    throw "Frontend production build failed."
}

$e2eArgs = @(
    "-NoProfile",
    "-File",
    (Join-Path $scriptRoot "e2e-local.ps1"),
    "-ApiPort",
    "$ApiPort",
    "-UiPort",
    "$UiPort"
)
& pwsh @e2eArgs
if ($LASTEXITCODE -ne 0) {
    throw "Browser E2E or backend restart restoration failed."
}

Write-Output "Local verification passed: backend, schema, compile, Ruff, frontend build, browser E2E, and API restart restoration."
