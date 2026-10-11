param([switch]$Execute, [switch]$AllowLiveLlm, [switch]$ProbeWire, [string]$Case, [string]$CampaignId, [int]$MaxCalls=90, [string]$MaxCost="3.00")
$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($Execute -and (-not $AllowLiveLlm -or -not $Case -or -not $CampaignId)) {
    throw "Execute requires AllowLiveLlm, Case and CampaignId. Default is preparation without provider calls."
}
$postgres = (docker inspect ikonly-test-postgres-1 | ConvertFrom-Json)[0]
if (-not $postgres.State.Running -or $postgres.Config.Labels.'com.docker.compose.project' -ne 'ikonly-test') {
    throw "Owned test PostgreSQL required."
}
$testUser = ($postgres.Config.Env | Where-Object { $_ -like 'POSTGRES_USER=*' }).Substring(14)
$testPassword = ($postgres.Config.Env | Where-Object { $_ -like 'POSTGRES_PASSWORD=*' }).Substring(18)
if ($testUser -ne 'eventquest_test' -or -not $testPassword) { throw "Unexpected test database owner." }
$testUrl = "postgresql+psycopg://${testUser}:$([uri]::EscapeDataString($testPassword))@127.0.0.1:5434/eventquest_fault_test"
$arguments = @('-m','backend.scripts.live_recovery_experiment','--max-calls',"$MaxCalls",'--max-cost',$MaxCost)
if ($Execute) { $arguments += @('--execute','--allow-live-llm','--case',$Case,'--campaign-id',$CampaignId) }
if ($ProbeWire) { $arguments += '--probe-wire' }
# Scope environment changes to this process; no existing server is restarted.
$settings = @{DATABASE_URL=$testUrl;POSTGRES_TEST_URL=$testUrl;MCP_DATABASE_URL=$testUrl;MCP_TEST_DATABASE_URL=$testUrl;MCP_URL='http://127.0.0.1:8014/mcp';FAULT_LAB_MODE='isolated';ENABLE_FAULT_INJECTION='true';CANONICAL_PLANNING_MODE='live';WORKFLOW_ENGINE='agent_mock';PYTHONPATH=$repositoryRoot;PYTHONIOENCODING='utf-8'}
$prior = @{}
foreach ($name in $settings.Keys) { $prior[$name] = [Environment]::GetEnvironmentVariable($name,'Process'); [Environment]::SetEnvironmentVariable($name,$settings[$name],'Process') }
Push-Location $repositoryRoot
try {
    & (Join-Path $repositoryRoot 'backend/.venv/Scripts/python.exe') @arguments
    if ($LASTEXITCODE -ne 0) { throw "Live recovery preparation/execution failed; inspect safe report evidence." }
} finally {
    Pop-Location
    foreach ($name in $prior.Keys) { [Environment]::SetEnvironmentVariable($name,$prior[$name],'Process') }
}
