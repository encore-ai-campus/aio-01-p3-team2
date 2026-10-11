param(
    [int]$ApiPort = 8015,
    [int]$UiPort = 5195,
    [string]$TestName,
    [switch]$CanonicalMcp503,
    [switch]$VerifyRestart,
    [ValidateSet("chromium", "edge")]
    [string]$Browser = "chromium",
    [ValidateSet("eventquest_auto_test")]
    [string]$TestDatabase = "eventquest_auto_test"
)

$ErrorActionPreference = "Stop"
$postgresName = "ikonly-test-postgres-1"
$databaseUser = "eventquest_test"
$runnerImage = "eventquest-test-runner:local"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = New-TemporaryFile
$apiName = "ikonly-e2e-api-$PID"
$mcpFailureName = "ikonly-e2e-mcp-503-$PID"
$apiPort = $ApiPort
$uiPort = $UiPort
$playwrightConfig = if ($Browser -eq "edge") { "playwright.edge.config.js" } else { "playwright.config.js" }
$apiStarted = $false
$nativeEnvBefore = @{}
$mcpFailureStarted = $false

try {
    $postgres = (docker inspect $postgresName | ConvertFrom-Json)[0]
    if (-not $postgres.State.Running) {
        throw "Dedicated test PostgreSQL container is not running: $postgresName"
    }

    $postgresEnv = $postgres.Config.Env
    $database = ($postgresEnv | Where-Object { $_ -like "POSTGRES_DB=*" } | Select-Object -First 1).Substring("POSTGRES_DB=".Length)
    $user = ($postgresEnv | Where-Object { $_ -like "POSTGRES_USER=*" } | Select-Object -First 1).Substring("POSTGRES_USER=".Length)
    $password = ($postgresEnv | Where-Object { $_ -like "POSTGRES_PASSWORD=*" } | Select-Object -First 1).Substring("POSTGRES_PASSWORD=".Length)
    if ($database -ne "eventquest_test" -or $user -ne $databaseUser -or -not $password) {
        throw "Refusing browser E2E: database container is not the expected isolated test database."
    }
    if ($postgres.Config.Labels.'com.docker.compose.project' -ne "ikonly-test") {
        throw "Refusing browser E2E: PostgreSQL is not owned by the ikonly-test Compose project."
    }
    $targetExists = docker exec $postgresName psql -U $databaseUser -d $database -Atqc "SELECT 1 FROM pg_database WHERE datname = '$TestDatabase'"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the isolated browser E2E database catalog."
    }
    if (-not $targetExists) {
        docker exec $postgresName createdb -U $databaseUser --owner=$databaseUser $TestDatabase
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the isolated browser E2E database."
        }
    }
    $databaseOwner = docker exec $postgresName psql -U $databaseUser -d $database -Atqc "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$TestDatabase'"
    if ($LASTEXITCODE -ne 0 -or $databaseOwner -ne $databaseUser) {
        throw "Refusing browser E2E: target database must exist and be owned by $databaseUser."
    }

    $network = ($postgres.NetworkSettings.Networks.PSObject.Properties.Name | Select-Object -First 1)
    $aliases = $postgres.NetworkSettings.Networks.$network.Aliases
    if ($aliases -notcontains "postgres") {
        throw "Could not verify the test database's Docker DNS alias."
    }
    if (Get-NetTCPConnection -LocalPort $apiPort, $uiPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Browser E2E ports $apiPort or $uiPort are already in use."
    }

    $encodedPassword = [uri]::EscapeDataString($password)
    $databaseUrl = "postgresql+psycopg://${databaseUser}:${encodedPassword}@postgres:5432/${TestDatabase}"
    @(
        "DATABASE_URL=$databaseUrl"
        "POSTGRES_TEST_URL=$databaseUrl"
        "WORKFLOW_ENGINE=backend_b_mock"
        "CANONICAL_PLANNING_MODE=deterministic"
        # A nonempty whitespace value prevents .env from injecting a live MCP URL.
        "MCP_URL= "
        "ENABLE_FAULT_INJECTION=true"
        "APPROVED_ACTOR_IDS=liveops01"
        "OPERATOR_ACTOR_ID=liveops01"
        "APP_ALLOWED_ORIGINS=http://127.0.0.1:${uiPort}"
        # Docker bridge requests reach the test API from a non-loopback container address.
        # The API is published to the host on 127.0.0.1 only and uses only the disposable test DB.
        "GAME_RUNTIME_ALLOW_REMOTE=true"
        "LLM_MAX_OUTPUT_TOKENS=512"
    ) | Set-Content -LiteralPath $envFile.FullName -Encoding ascii

    if ($CanonicalMcp503) {
        "MCP_URL=http://mcp-failure:8010/mcp" | Add-Content -LiteralPath $envFile.FullName -Encoding ascii
        $mcpArgs = @(
            "run", "--detach", "--rm", "--name", $mcpFailureName,
            "--network", $network, "--network-alias", "mcp-failure",
            "-p", "127.0.0.1:8014:8010",
            "-v", "${repositoryRoot}:/workspace", "-w", "/workspace",
            $runnerImage, "python", "-m", "backend.tests.support.mcp_503_server",
            "--host", "0.0.0.0", "--port", "8010"
        )
        & docker @mcpArgs | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not start the isolated HTTP 503 MCP fixture."
        }
        $mcpFailureStarted = $true
        $mcpReady = $false
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            $health = & docker exec $mcpFailureName python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=2).read().decode())" 2>$null
            if ($LASTEXITCODE -eq 0 -and $health -eq "ok") {
                $mcpReady = $true
                break
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $mcpReady) {
            docker logs $mcpFailureName
            throw "Isolated HTTP 503 MCP fixture did not become ready."
        }
    }

    $dockerArgs = @(
        "run", "--rm", "--network", $network,
        "--env-file", $envFile.FullName,
        "-v", "${repositoryRoot}:/workspace",
        "-w", "/workspace",
        $runnerImage
    )
    & docker @dockerArgs python -m backend.scripts.seed_game_runtime
    if ($LASTEXITCODE -ne 0) {
        throw "Could not apply migrations and seed the isolated browser E2E database."
    }

    # Native backend keeps request.client loopback under the common local guard.
    $hostPort = $postgres.NetworkSettings.Ports.'5432/tcp'[0].HostPort
    if (-not $hostPort) { throw "Isolated PostgreSQL has no published local port." }
    foreach ($entry in Get-Content -LiteralPath $envFile.FullName) {
        $pair = $entry -split "=", 2
        $nativeEnvBefore[$pair[0]] = [Environment]::GetEnvironmentVariable($pair[0], "Process")
        [Environment]::SetEnvironmentVariable($pair[0], $pair[1], "Process")
    }
    $env:DATABASE_URL = "postgresql+psycopg://${databaseUser}:${encodedPassword}@127.0.0.1:${hostPort}/${TestDatabase}"
    $env:POSTGRES_TEST_URL = $env:DATABASE_URL
    if ($CanonicalMcp503) { $env:MCP_URL = "http://127.0.0.1:8014/mcp" }
    $pythonPath = Join-Path $repositoryRoot "backend/.venv/Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Backend local virtual environment is missing." }
    & $pythonPath -c "from backend.tests.conftest import align_isolated_test_policy; align_isolated_test_policy.__wrapped__()"
    if ($LASTEXITCODE -ne 0) { throw "Could not align the isolated E2E policy contract." }
    $apiLog = Join-Path ([IO.Path]::GetTempPath()) "ikonly-e2e-$PID-api.log"
    $apiErrorLog = Join-Path ([IO.Path]::GetTempPath()) "ikonly-e2e-$PID-api-error.log"
    $apiArgs = @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "$apiPort")
    $apiProcess = Start-Process -FilePath $pythonPath -ArgumentList $apiArgs -WorkingDirectory $repositoryRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $apiLog -RedirectStandardError $apiErrorLog
    $apiStarted = $true

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:${apiPort}/health/ready" -TimeoutSec 2
            if ($health.status -eq "ready") {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) {
        Get-Content -LiteralPath $apiErrorLog -ErrorAction SilentlyContinue
        throw "Isolated browser E2E API did not become ready."
    }

    $env:EVENTQUEST_API_TARGET = "http://127.0.0.1:${apiPort}"
    $env:EVENTQUEST_E2E_API_PORT = "$apiPort"
    $env:EVENTQUEST_E2E_UI_PORT = "$uiPort"
    if ($CanonicalMcp503) {
        $env:EVENTQUEST_E2E_CANONICAL_MCP_503 = "1"
    }
    Push-Location (Join-Path $repositoryRoot 'frontend-web')
    try {
        $browserArgs = @('node_modules/@playwright/test/cli.js', 'test', "--config=$playwrightConfig")
        if ($TestName) { $browserArgs += @('--grep', $TestName) }
        & node @browserArgs
        if ($LASTEXITCODE -ne 0) { throw "Browser E2E failed." }
    } finally { Pop-Location }
    if (-not $TestName -and -not $CanonicalMcp503) {
        Push-Location (Join-Path $repositoryRoot 'frontend-web')
        try {
            node node_modules/@playwright/test/cli.js test --config=playwright.game.config.js
            if ($LASTEXITCODE -ne 0) { throw "Game UI smoke verification failed." }
        } finally { Pop-Location }
    }

    if ($TestName -and -not $VerifyRestart) {
        return
    }

    $restoreStateFile = Join-Path $repositoryRoot "frontend-web/test-results/e2e-restore-state.json"
    if (-not (Test-Path -LiteralPath $restoreStateFile)) {
        throw "Browser E2E did not leave persisted entity IDs for restart verification."
    }
    $restoreState = Get-Content -LiteralPath $restoreStateFile -Raw | ConvertFrom-Json
    if ($restoreState.workflowId -notmatch "^WF-[A-F0-9]{12}$" -or $restoreState.runtimeEventId -notmatch "^[0-9a-fA-F-]{36}$") {
        throw "Browser E2E wrote invalid Workflow or Runtime Event IDs for restart verification."
    }

    Stop-Process -Id $apiProcess.Id -ErrorAction Stop
    $apiStarted = $false
    $apiProcess.WaitForExit(5000) | Out-Null
    $apiProcess = Start-Process -FilePath $pythonPath -ArgumentList $apiArgs -WorkingDirectory $repositoryRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $apiLog -RedirectStandardError $apiErrorLog
    $apiStarted = $true
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:${apiPort}/health/ready" -TimeoutSec 2
            if ($health.status -eq "ready") {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) {
        Get-Content -LiteralPath $apiErrorLog -ErrorAction SilentlyContinue
        throw "Restarted isolated browser E2E API did not become ready."
    }

    $env:EVENTQUEST_E2E_RESTORE_STATE = Get-Content -LiteralPath $restoreStateFile -Raw
    if ($restoreState.contractKind -ne "space_workflow") {
        throw "Restart verification requires the current persisted space workflow contract."
    }
    $restartCase = "restores durable workflow after backend restart"
    Push-Location (Join-Path $repositoryRoot 'frontend-web')
    try { node node_modules/@playwright/test/cli.js test "--config=$playwrightConfig" --grep $restartCase
        if ($LASTEXITCODE -ne 0) { throw "Backend restart browser verification failed." }
    } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) {
        throw "Workflow did not restore after API process restart."
    }
}
finally {
    if ($apiStarted) {
        Stop-Process -Id $apiProcess.Id -ErrorAction SilentlyContinue
    }
    if ($mcpFailureStarted) {
        docker stop $mcpFailureName | Out-Null
    }
    Remove-Item -LiteralPath $envFile.FullName -Force -ErrorAction SilentlyContinue
    foreach ($entry in $nativeEnvBefore.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
    Remove-Variable password, encodedPassword, databaseUrl -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_API_TARGET -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_E2E_API_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_E2E_UI_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_E2E_CANONICAL_MCP_503 -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_E2E_RESTORE_STATE -ErrorAction SilentlyContinue
}
