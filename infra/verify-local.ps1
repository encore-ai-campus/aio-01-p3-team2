param(
    [switch]$RestartPostgres,
    [int]$ApiPort = 8015,
    [int]$UiPort = 5195,
    [ValidateSet("eventquest_auto_test")]
    [string]$TestDatabase = "eventquest_auto_test"
)

$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot

$testArgs = @("-NoProfile", "-File", (Join-Path $scriptRoot "test-local.ps1"))
if ($RestartPostgres) {
    $testArgs += "-RestartPostgres"
}
$testArgs += @("-TestDatabase", $TestDatabase)
& pwsh @testArgs
if ($LASTEXITCODE -ne 0) {
    throw "Backend tests, schema check, compile check, or Ruff gate failed."
}

node frontend-web/node_modules/vite/bin/vite.js build frontend-web
if ($LASTEXITCODE -ne 0) {
    throw "Frontend production build failed."
}

node demo-game/node_modules/vite/bin/vite.js build demo-game
if ($LASTEXITCODE -ne 0) { throw "Game production build failed." }
Push-Location (Join-Path $scriptRoot '../frontend-web')
try {
    node --test tests/proxy-routing.test.js
    if ($LASTEXITCODE -ne 0) { throw "Frontend API isolation test failed." }
    node node_modules/@playwright/test/cli.js test --config=playwright.workflow.config.js
    if ($LASTEXITCODE -ne 0) { throw "Workflow UI fixture tests failed." }
} finally { Pop-Location }

$e2eArgs = @(
    "-NoProfile",
    "-File",
    (Join-Path $scriptRoot "e2e-local.ps1"),
    "-ApiPort",
    "$ApiPort",
    "-UiPort",
    "$UiPort",
    "-TestDatabase",
    $TestDatabase
)
& pwsh @e2eArgs
if ($LASTEXITCODE -ne 0) {
    throw "Browser E2E or backend restart restoration failed."
}

Write-Output "Local verification passed: backend, schema, compile, Ruff, both frontend builds, proxy routing, Workflow UI fixtures, browser E2E, and API restart restoration."
