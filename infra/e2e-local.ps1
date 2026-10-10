param(
    [int]$ApiPort = 8004,
    [int]$UiPort = 5177
)

$ErrorActionPreference = "Stop"
$postgresName = "ikonly-test-postgres-1"
$databaseName = "eventquest_test"
$databaseUser = "eventquest_test"
$runnerImage = "eventquest-test-runner:local"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = New-TemporaryFile
$apiName = "ikonly-e2e-api-$PID"
$apiPort = $ApiPort
$uiPort = $UiPort
$apiStarted = $false

try {
    $postgres = (docker inspect $postgresName | ConvertFrom-Json)[0]
    if (-not $postgres.State.Running) {
        throw "Dedicated test PostgreSQL container is not running: $postgresName"
    }

    $postgresEnv = $postgres.Config.Env
    $database = ($postgresEnv | Where-Object { $_ -like "POSTGRES_DB=*" } | Select-Object -First 1).Substring("POSTGRES_DB=".Length)
    $user = ($postgresEnv | Where-Object { $_ -like "POSTGRES_USER=*" } | Select-Object -First 1).Substring("POSTGRES_USER=".Length)
    $password = ($postgresEnv | Where-Object { $_ -like "POSTGRES_PASSWORD=*" } | Select-Object -First 1).Substring("POSTGRES_PASSWORD=".Length)
    if ($database -ne $databaseName -or $user -ne $databaseUser -or -not $password) {
        throw "Refusing browser E2E: database container is not the expected isolated test database."
    }
    if ($postgres.Config.Labels.'com.docker.compose.project' -ne "ikonly-test") {
        throw "Refusing browser E2E: PostgreSQL is not owned by the ikonly-test Compose project."
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
    $databaseUrl = "postgresql+psycopg://${databaseUser}:${encodedPassword}@postgres:5432/${databaseName}"
    @(
        "DATABASE_URL=$databaseUrl"
        "POSTGRES_TEST_URL=$databaseUrl"
        "WORKFLOW_ENGINE=backend_b_mock"
        "CANONICAL_PLANNING_MODE=deterministic"
        "ENABLE_FAULT_INJECTION=true"
        "APPROVED_ACTOR_IDS=liveops01"
        "OPERATOR_ACTOR_ID=liveops01"
        "APP_ALLOWED_ORIGINS=http://127.0.0.1:${uiPort}"
        # Docker bridge requests reach the test API from a non-loopback container address.
        # The API is published to the host on 127.0.0.1 only and uses only the disposable test DB.
        "GAME_RUNTIME_ALLOW_REMOTE=true"
        "LLM_MAX_OUTPUT_TOKENS=512"
    ) | Set-Content -LiteralPath $envFile.FullName -Encoding ascii

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

    $apiArgs = @(
        "run", "--detach", "--rm", "--name", $apiName,
        "--network", $network,
        "-p", "127.0.0.1:${apiPort}:8000",
        "--env-file", $envFile.FullName,
        "-v", "${repositoryRoot}:/workspace",
        "-w", "/workspace",
        $runnerImage,
        "python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"
    )
    & docker @apiArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not start the isolated browser E2E API."
    }
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
        docker logs $apiName
        throw "Isolated browser E2E API did not become ready."
    }

    $env:EVENTQUEST_API_TARGET = "http://127.0.0.1:${apiPort}"
    $env:EVENTQUEST_E2E_API_PORT = "$apiPort"
    $env:EVENTQUEST_E2E_UI_PORT = "$uiPort"
    pnpm --dir frontend-web test:e2e
    if ($LASTEXITCODE -ne 0) {
        throw "Browser E2E failed."
    }

    $restoreStateFile = Join-Path $repositoryRoot "frontend-web/test-results/e2e-restore-state.json"
    if (-not (Test-Path -LiteralPath $restoreStateFile)) {
        throw "Browser E2E did not leave persisted entity IDs for restart verification."
    }
    $restoreState = Get-Content -LiteralPath $restoreStateFile -Raw | ConvertFrom-Json
    if ($restoreState.workflowId -notmatch "^WF-[A-F0-9]{12}$" -or $restoreState.runtimeEventId -notmatch "^[0-9a-fA-F-]{36}$") {
        throw "Browser E2E wrote invalid Workflow or Runtime Event IDs for restart verification."
    }

    docker stop $apiName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not stop the isolated test API for restart verification."
    }
    $apiStarted = $false
    & docker @apiArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restart the isolated test API."
    }
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
        docker logs $apiName
        throw "Restarted isolated browser E2E API did not become ready."
    }

    $env:EVENTQUEST_E2E_RESTORE_STATE = Get-Content -LiteralPath $restoreStateFile -Raw
    pnpm --dir frontend-web exec playwright test --config=playwright.config.js --grep "restores workflow after backend restart"
    if ($LASTEXITCODE -ne 0) {
        throw "Workflow did not restore after API process restart."
    }
}
finally {
    if ($apiStarted) {
        docker stop $apiName | Out-Null
    }
    Remove-Item -LiteralPath $envFile.FullName -Force -ErrorAction SilentlyContinue
    Remove-Variable password, encodedPassword, databaseUrl -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_API_TARGET -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_E2E_API_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_E2E_UI_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_E2E_RESTORE_STATE -ErrorAction SilentlyContinue
}
