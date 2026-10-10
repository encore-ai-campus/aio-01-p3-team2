param(
    [switch]$RestartPostgres
)

$ErrorActionPreference = "Stop"
$containerName = "ikonly-test-postgres-1"
$testDatabase = "eventquest_test"
$testUser = "eventquest_test"
$runnerImage = "eventquest-test-runner:local"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
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
    if ($database -ne $testDatabase -or $user -ne $testUser -or -not $password) {
        throw "Refusing to run destructive tests: container is not the expected isolated test database."
    }
    if ($postgres.Config.Labels.'com.docker.compose.project' -ne "ikonly-test") {
        throw "Refusing to run destructive tests: PostgreSQL is not owned by the ikonly-test Compose project."
    }

    if ($RestartPostgres) {
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
    $databaseUrl = "postgresql+psycopg://${testUser}:${encodedPassword}@127.0.0.1:5432/${testDatabase}"
    @(
        "DATABASE_URL=$databaseUrl"
        "POSTGRES_TEST_URL=$databaseUrl"
        "MCP_TEST_DATABASE_URL=$databaseUrl"
    ) | Set-Content -LiteralPath $envFile.FullName -Encoding ascii

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

    & docker @dockerArgs python -m pytest -q --tb=short -rs
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
