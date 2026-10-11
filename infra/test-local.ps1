param(
    [ValidateSet("core", "postgres", "local", "compatibility")]
    [string]$Profile = "local",
    [switch]$RestartPostgres,
    [string]$TestPath,
    [string]$TestName,
    [string]$McpUrl,
    [ValidateSet("eventquest_auto_test")]
    [string]$TestDatabase = "eventquest_auto_test"
)

$ErrorActionPreference = "Stop"
$containerName = "ikonly-test-postgres-1"
$testUser = "eventquest_test"
$runnerImage = "eventquest-test-runner:local"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($Profile -in @("core", "compatibility")) {
    if ($RestartPostgres) { throw "Native core/compatibility tests do not restart PostgreSQL." }
    $python = Join-Path $repositoryRoot "backend/.venv/Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $python)) { throw "Install requirements-dev.txt in backend/.venv." }
    $names = @('DATABASE_URL','POSTGRES_TEST_URL','MCP_DATABASE_URL','MCP_TEST_DATABASE_URL','MCP_TEST_URL','MCP_LEGACY_TEST_URL','LLM_TEST_MODEL')
    $before = @{}
    foreach ($name in $names) {
        $before[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    Push-Location $repositoryRoot
    try {
        $argsCore = @('-m','pytest',"--test-profile=$Profile",'-q','-rs','--durations=10')
        if ($TestPath) { $argsCore += $TestPath }
        if ($TestName) { $argsCore += @('-k', $TestName) }
        & $python @argsCore
        if ($LASTEXITCODE -ne 0) { throw "Native $Profile tests failed." }
        & $python -m backend.scripts.export_planning_contract_schemas --check
        if ($LASTEXITCODE -ne 0) { throw "Schema export check failed." }
        & $python -m ruff check .
        if ($LASTEXITCODE -ne 0) { throw "Ruff check failed." }
        & $python -m ruff format --check .
        if ($LASTEXITCODE -ne 0) { throw "Ruff format failed." }
    } finally {
        Pop-Location
        foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $before[$name], 'Process') }
    }
    return
}
$ruffPython = Join-Path $repositoryRoot ".venv/Scripts/python.exe"
$envFile = New-TemporaryFile

try {
    $postgres = (docker inspect $containerName | ConvertFrom-Json)[0]
    if (-not $postgres.State.Running) {
        throw "Dedicated test PostgreSQL container is not running: $containerName"
    }

    $postgresEnv = $postgres.Config.Env
    $database = ($postgresEnv | Where-Object { $_ -like "POSTGRES_DB=*" } | Select-Object -First 1).Substring("POSTGRES_DB=".Length)
    $user = ($postgresEnv | Where-Object { $_ -like "POSTGRES_USER=*" } | Select-Object -First 1).Substring("POSTGRES_USER=".Length)
    $password = ($postgresEnv | Where-Object { $_ -like "POSTGRES_PASSWORD=*" } | Select-Object -First 1).Substring("POSTGRES_PASSWORD=".Length)
    if ($database -ne "eventquest_test" -or $user -ne $testUser -or -not $password) {
        throw "Refusing to run destructive tests: container is not the expected isolated test database."
    }
    if ($postgres.Config.Labels.'com.docker.compose.project' -ne "ikonly-test") {
        throw "Refusing to run destructive tests: PostgreSQL is not owned by the ikonly-test Compose project."
    }

    $targetExists = docker exec $containerName psql -U $testUser -d $database -Atqc "SELECT 1 FROM pg_database WHERE datname = '$TestDatabase'"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the isolated test database catalog."
    }
    if (-not $targetExists) {
        docker exec $containerName createdb -U $testUser --owner=$testUser $TestDatabase
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the isolated automation test database."
        }
    }
    $databaseOwner = docker exec $containerName psql -U $testUser -d $database -Atqc "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$TestDatabase'"
    if ($LASTEXITCODE -ne 0 -or $databaseOwner -ne $testUser) {
        throw "Refusing to run tests: target database must exist and be owned by $testUser."
    }
    if ($RestartPostgres) {
        $edgeReview = docker inspect ikonly-edge-review-api 2>$null | ConvertFrom-Json
        if ($edgeReview -and $edgeReview[0].State.Running) {
            throw "Do not restart the shared PostgreSQL service while the Edge review API is using it."
        }
        docker restart $containerName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not restart dedicated test PostgreSQL."
        }
    }

    docker build -f infra/Dockerfile.test -t $runnerImage .
    if ($LASTEXITCODE -ne 0) {
        throw "Could not build the local test runner image."
    }

    $encodedPassword = [uri]::EscapeDataString($password)
    $databaseUrl = "postgresql+psycopg://${testUser}:${encodedPassword}@127.0.0.1:5432/${TestDatabase}"
    $runnerEnvironment = @(
        "DATABASE_URL=$databaseUrl"
        "POSTGRES_TEST_URL=$databaseUrl"
        "MCP_TEST_DATABASE_URL=$databaseUrl"
        "MCP_DATABASE_URL=$databaseUrl"
    )
    if ($McpUrl) {
        $runnerEnvironment += "MCP_TEST_URL=$McpUrl"
    }
    $runnerEnvironment | Set-Content -LiteralPath $envFile.FullName -Encoding ascii

    $dockerArgs = @(
        "run", "--rm", "--network", "container:$containerName",
        "--env-file", $envFile.FullName,
        "-v", "${repositoryRoot}:/workspace",
        "-w", "/workspace",
        $runnerImage
    )

    & docker @dockerArgs python -m backend.scripts.seed_game_runtime
    if ($LASTEXITCODE -ne 0) {
        throw "Could not apply migrations and seed the isolated test database."
    }

    $pytestArgs = @("python", "-m", "pytest", "--test-profile=$Profile", "-q", "--tb=short", "-rs", "--durations=10")
    if ($TestPath) {
        $pytestArgs += $TestPath
    }
    if ($TestName) {
        $pytestArgs += @("-k", $TestName)
    }
    & docker @dockerArgs @pytestArgs
    if ($LASTEXITCODE -ne 0) {
        throw "The local test suite failed."
    }

    & docker @dockerArgs python -m backend.scripts.export_planning_contract_schemas --check
    if ($LASTEXITCODE -ne 0) {
        throw "Planning contract schema export check failed."
    }

    & docker @dockerArgs python -m compileall -q backend/app backend/tests backend/scripts
    if ($LASTEXITCODE -ne 0) {
        throw "Python compile check failed."
    }

    if (-not (Test-Path -LiteralPath $ruffPython)) {
        throw "Install requirements-dev.txt in .venv before running Ruff checks."
    }
    & $ruffPython -m ruff check .
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff check failed."
    }

    & $ruffPython -m ruff format --check .
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff format check failed."
    }
}
finally {
    Remove-Item -LiteralPath $envFile.FullName -Force -ErrorAction SilentlyContinue
    Remove-Variable password, encodedPassword, databaseUrl -ErrorAction SilentlyContinue
}
