param([switch]$PrepareOnly, [switch]$Stop, [switch]$CleanupHistory, [int]$ApiPort=8013, [int]$UiPort=5183, [int]$McpPort=8014)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repositoryRoot "backend/.venv/Scripts/python.exe"
$statePath = Join-Path $repositoryRoot "backend/test-results/fault-lab/servers.json"
if ($Stop) {
    if (Test-Path -LiteralPath $statePath) {
        $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $environment = Invoke-RestMethod "$($saved.api)/api/test/fault-environment"
        if ($environment.state -ne "CLEAN") { throw "Refusing to stop a BUSY/DIRTY fault lab. Inspect the owner and wait for cleanup first." }
        foreach ($entry in $saved.processes) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.id)"
            if ($process -and $process.CommandLine.Contains($entry.marker)) {
                $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($entry.id)"
                foreach ($child in $children) {
                    if ($child.CommandLine -and $child.CommandLine.Contains($entry.marker)) { Stop-Process -Id $child.ProcessId }
                }
                Stop-Process -Id $entry.id
            }
        }
        Remove-Item -LiteralPath $statePath
    }
    exit
}
$containerName = "ikonly-test-postgres-1"
$postgres = (docker inspect $containerName | ConvertFrom-Json)[0]
if (-not $postgres.State.Running -or $postgres.Config.Labels.'com.docker.compose.project' -ne "ikonly-test") {
    throw "Isolated ikonly-test PostgreSQL ownership verification failed."
}
$user = ($postgres.Config.Env | Where-Object { $_ -like 'POSTGRES_USER=*' }).Substring(14)
$password = ($postgres.Config.Env | Where-Object { $_ -like 'POSTGRES_PASSWORD=*' }).Substring(18)
if ($user -ne "eventquest_test" -or -not $password) { throw "Unexpected test PostgreSQL owner." }
$exists = docker exec $containerName psql -U $user -d eventquest_test -Atqc "SELECT 1 FROM pg_database WHERE datname='eventquest_fault_test'"
if ($LASTEXITCODE -ne 0) { throw "Could not inspect PostgreSQL." }
if (-not $exists) {
    docker exec $containerName createdb -U $user --owner=$user eventquest_fault_test
    if ($LASTEXITCODE -ne 0) { throw "Could not create isolated fault database." }
}
$labTable = docker exec $containerName psql -U $user -d eventquest_fault_test -Atqc "SELECT to_regclass('fault_lab_environment')"
if ($labTable) {
    $labState = docker exec $containerName psql -U $user -d eventquest_fault_test -Atqc "SELECT state FROM fault_lab_environment WHERE id=1"
    if ($labState -ne "CLEAN") { throw "Fault lab is BUSY/DIRTY; baseline preparation is blocked." }
}
$databaseUrl = "postgresql+psycopg://${user}:$([uri]::EscapeDataString($password))@127.0.0.1:5434/eventquest_fault_test"
$env:DATABASE_URL = $databaseUrl
$env:POSTGRES_TEST_URL = $databaseUrl
$env:MCP_DATABASE_URL = $databaseUrl
$env:MCP_TEST_DATABASE_URL = $databaseUrl
$env:MCP_GAME_ID = "tiny_heroes"
$env:ENABLE_FAULT_INJECTION = "true"
$env:FAULT_LAB_MODE = "isolated"
$env:CANONICAL_PLANNING_MODE = "deterministic"
$env:WORKFLOW_ENGINE = "agent_mock"
$env:MCP_HOST = "127.0.0.1"
$env:MCP_PORT = "$McpPort"
$env:MCP_URL = "http://127.0.0.1:$McpPort/mcp"
$env:EVENTQUEST_API_TARGET = "http://127.0.0.1:$ApiPort"
$env:APP_ALLOWED_ORIGINS = "http://127.0.0.1:$UiPort,http://localhost:$UiPort"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $repositoryRoot
Push-Location $repositoryRoot
try {
    & $python -c "import os; from backend.scripts.apply_game_data_catalog import apply_catalog; from backend.scripts.seed_game_runtime import seed; from backend.app.repositories.fault_lab import initialize; apply_catalog(os.environ['DATABASE_URL']); seed(os.environ['DATABASE_URL']); initialize()"
    if ($LASTEXITCODE -ne 0) { throw "Fault database preparation failed." }
    if ($CleanupHistory) {
        & $python -m backend.tests.support.fault_lab_runner --cleanup-history
        if ($LASTEXITCODE -ne 0) { throw "Fault history cleanup failed." }
        return
    }
    if ($PrepareOnly) { return }
    foreach ($port in @($ApiPort,$UiPort,$McpPort)) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "Port $port is already in use; existing servers were preserved." }
    }
    $logs = Split-Path $statePath
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $mcp = Start-Process $python -ArgumentList @('-m','mcp_server.server') -WorkingDirectory $repositoryRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs/mcp.out.log" -RedirectStandardError "$logs/mcp.err.log"
    $api = Start-Process $python -ArgumentList @('-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',"$ApiPort") -WorkingDirectory $repositoryRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs/api.out.log" -RedirectStandardError "$logs/api.err.log"
    $node = (Get-Command node.exe).Source
    $ui = Start-Process $node -ArgumentList @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',"$UiPort",'--strictPort') -WorkingDirectory "$repositoryRoot/frontend-web" -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs/ui.out.log" -RedirectStandardError "$logs/ui.err.log"
    @{ processes=@(@{id=$mcp.Id;marker='mcp_server.server'},@{id=$api.Id;marker='--port '+$ApiPort},@{id=$ui.Id;marker='--port '+$UiPort}); api="http://127.0.0.1:$ApiPort"; ui="http://127.0.0.1:$UiPort" } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath
    Write-Output "Isolated fault lab: http://127.0.0.1:$UiPort (DB eventquest_fault_test, deterministic provider, real MCP/DB)"
} finally { Pop-Location }
