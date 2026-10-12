param([switch]$DemoRecoveryOnce)

$ErrorActionPreference = "Stop"
$postgresName = "ikonly-test-postgres-1"
$expectedDatabase = "eventquest_test"
$expectedProject = "ikonly-test"
$apiName = "ikonly-edge-review-api"
$apiPort = 8004
$uiPort = 5177
$runnerImage = "eventquest-test-runner:local"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$testResults = Join-Path $repositoryRoot "frontend-web/test-results"
$stateFile = Join-Path $testResults "edge-review-processes.json"

if (Test-Path -LiteralPath $stateFile) {
    throw "Edge review state file already exists. Run infra/stop-edge-review.ps1 first."
}
if (Get-NetTCPConnection -LocalPort $apiPort, $uiPort -State Listen -ErrorAction SilentlyContinue) {
    throw "Edge review ports $apiPort or $uiPort are already in use."
}

$postgres = (docker inspect $postgresName | ConvertFrom-Json)[0]
if (-not $postgres.State.Running -or $postgres.Config.Labels.'com.docker.compose.project' -ne $expectedProject) {
    throw "Refusing Edge review: dedicated test PostgreSQL ownership check failed."
}
$network = $postgres.NetworkSettings.Networks.PSObject.Properties.Name | Select-Object -First 1
$aliases = $postgres.NetworkSettings.Networks.$network.Aliases
if ($aliases -notcontains "postgres") {
    throw "Refusing Edge review: test PostgreSQL DNS alias was not verified."
}
$postgresEnv = $postgres.Config.Env
$database = ($postgresEnv | Where-Object { $_ -like "POSTGRES_DB=*" } | Select-Object -First 1).Substring("POSTGRES_DB=".Length)
$user = ($postgresEnv | Where-Object { $_ -like "POSTGRES_USER=*" } | Select-Object -First 1).Substring("POSTGRES_USER=".Length)
$password = ($postgresEnv | Where-Object { $_ -like "POSTGRES_PASSWORD=*" } | Select-Object -First 1).Substring("POSTGRES_PASSWORD=".Length)
if ($database -ne $expectedDatabase -or $user -ne $expectedDatabase -or -not $password) {
    throw "Refusing Edge review: dedicated test database identity mismatch."
}

$envFile = New-TemporaryFile
$viteProcess = $null
$apiStarted = $false
try {
    $encodedPassword = [uri]::EscapeDataString($password)
    $databaseUrl = "postgresql+psycopg://${user}:${encodedPassword}@postgres:5432/${database}"
    $apiEnv = @(
        "DATABASE_URL=$databaseUrl"
        "POSTGRES_TEST_URL=$databaseUrl"
        "WORKFLOW_ENGINE=backend_b_mock"
        "CANONICAL_PLANNING_MODE=deterministic"
        "ENABLE_FAULT_INJECTION=true"
        "APPROVED_ACTOR_IDS=liveops01"
        "OPERATOR_ACTOR_ID=liveops01"
        # Only the test API uses this because Docker bridge requests have non-loopback source IPs.
        # The host binding remains loopback-only and the database is the dedicated test database.
        "GAME_RUNTIME_ALLOW_REMOTE=true"
        "LLM_MAX_OUTPUT_TOKENS=512"
    )
    if ($DemoRecoveryOnce) {
        $apiEnv += "EVENTQUEST_LOCAL_DEMO_HEAL_ONCE=1"
    }
    $apiEnv | Set-Content -LiteralPath $envFile.FullName -Encoding ascii

    $seedArgs = @(
        "run", "--rm", "--network", $network,
        "--env-file", $envFile.FullName,
        "-v", "${repositoryRoot}:/workspace",
        "-w", "/workspace",
        $runnerImage
    )
    & docker @seedArgs python -m backend.scripts.seed_game_runtime
    if ($LASTEXITCODE -ne 0) {
        throw "Could not migrate and seed the dedicated Edge review database."
    }

    $apiArgs = @(
        "run", "--detach", "--rm", "--name", $apiName,
        "--network", $network,
        "-p", "127.0.0.1:${apiPort}:8000",
        "--env-file", $envFile.FullName,
        "-v", "${repositoryRoot}:/workspace",
        "-w", "/workspace",
        $runnerImage,
        "python", "-m", "uvicorn", "backend.app.main:app",
        "--host", "0.0.0.0", "--port", "8000"
    )
    & docker @apiArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not start the Edge review API."
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
        throw "Edge review API did not become ready."
    }

    New-Item -ItemType Directory -Path $testResults -Force | Out-Null
    $env:EVENTQUEST_API_TARGET = "http://127.0.0.1:${apiPort}"
    $env:EVENTQUEST_FAULT_API_TARGET = "http://127.0.0.1:8013"
    $viteProcess = Start-Process -FilePath "pnpm.cmd" `
        -ArgumentList @("--dir", "frontend-web", "dev", "--host", "127.0.0.1", "--port", "$uiPort", "--strictPort") `
        -WorkingDirectory $repositoryRoot `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $testResults "edge-review-vite.stdout.log") `
        -RedirectStandardError (Join-Path $testResults "edge-review-vite.stderr.log")

    $uiReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $response = Invoke-WebRequest "http://127.0.0.1:${uiPort}/" -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $uiReady = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $uiReady) {
        Get-Content (Join-Path $testResults "edge-review-vite.stderr.log") -ErrorAction SilentlyContinue
        throw "Edge review UI did not become ready."
    }

    [ordered]@{
        apiContainer = $apiName
        viteProcessId = $viteProcess.Id
        uiUrl = "http://127.0.0.1:${uiPort}/"
        apiUrl = "http://127.0.0.1:${apiPort}"
        databaseContainer = $postgresName
        databaseName = $database
        startedAt = (Get-Date).ToUniversalTime().ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8

    Write-Output "Edge review UI: http://127.0.0.1:${uiPort}/"
    Write-Output "API: http://127.0.0.1:${apiPort} (loopback only)"
    Write-Output "Database: $postgresName / $database (dedicated test data)"
    Write-Output "Stop with: pwsh -NoProfile -File infra/stop-edge-review.ps1"
}
catch {
    if ($viteProcess) {
        & taskkill.exe /PID $viteProcess.Id /T /F | Out-Null
    }
    if ($apiStarted) {
        docker stop $apiName | Out-Null
    }
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
    throw
}
finally {
    Remove-Item -LiteralPath $envFile.FullName -Force -ErrorAction SilentlyContinue
    Remove-Variable password, encodedPassword, databaseUrl -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_API_TARGET -ErrorAction SilentlyContinue
    Remove-Item Env:EVENTQUEST_FAULT_API_TARGET -ErrorAction SilentlyContinue
}
