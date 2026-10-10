$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stateFile = Join-Path $repositoryRoot "frontend-web/test-results/edge-review-processes.json"
if (-not (Test-Path -LiteralPath $stateFile)) {
    Write-Output "No managed Edge review environment is running."
    return
}

$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
if ($state.apiContainer -ne "ikonly-edge-review-api" -or $state.databaseContainer -ne "ikonly-test-postgres-1") {
    throw "Refusing cleanup: review state file does not identify the managed test services."
}

$process = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.viteProcessId)"
if ($process) {
    if ($process.Name -notin @("cmd.exe", "pnpm.exe", "node.exe")) {
        throw "Refusing cleanup: saved Vite process ID has an unexpected executable."
    }
    & taskkill.exe /PID $state.viteProcessId /T /F | Out-Null
}

$container = docker inspect $state.apiContainer 2>$null | ConvertFrom-Json
if ($container) {
    if ($container[0].Config.Labels.'com.docker.compose.project') {
        throw "Refusing cleanup: API container unexpectedly belongs to a Compose project."
    }
    docker stop $state.apiContainer | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not stop the managed Edge review API."
    }
}

Remove-Item -LiteralPath $stateFile -Force
Write-Output "Stopped Edge review UI and test API. The dedicated PostgreSQL test container remains running."
