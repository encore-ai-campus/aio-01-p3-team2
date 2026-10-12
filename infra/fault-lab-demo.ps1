param([switch]$Stop, [int]$ApiPort=8183, [int]$McpPort=8184, [int]$UiPort=5184)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$statePath = Join-Path $repositoryRoot 'backend/test-results/fault-lab/demo-ui.json'
if ($Stop) {
    & "$PSScriptRoot/fault-lab-local.ps1" -Stop
    if (Test-Path -LiteralPath $statePath) {
        $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.id)"
        if ($process -and $process.CommandLine.Contains($saved.marker)) { Stop-Process -Id $saved.id }
        Remove-Item -LiteralPath $statePath
    }
    return
}
if (Get-NetTCPConnection -LocalPort $UiPort -State Listen -ErrorAction SilentlyContinue) { throw "UI port $UiPort is already in use." }
$node = (Get-Command node -ErrorAction Stop).Source
$frontend = Join-Path $repositoryRoot 'frontend-web'
if (-not (Test-Path -LiteralPath "$frontend/node_modules/vite/bin/vite.js")) { throw 'Run pnpm install --frozen-lockfile in frontend-web first.' }
$addresses = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }
$origins = @("http://127.0.0.1:$UiPort", "http://localhost:$UiPort") + @($addresses | ForEach-Object { "http://$($_.IPAddress):$UiPort" })
& "$PSScriptRoot/fault-lab-local.ps1" -ApiPort $ApiPort -McpPort $McpPort -UiPort $UiPort -UiOrigins ($origins -join ',')
$previousApi = $env:EVENTQUEST_API_TARGET
$previousFault = $env:EVENTQUEST_FAULT_API_TARGET
try {
    $env:EVENTQUEST_API_TARGET = "http://127.0.0.1:$ApiPort"
    $env:EVENTQUEST_FAULT_API_TARGET = "http://127.0.0.1:$ApiPort"
    $logs = Split-Path $statePath
    $ui = Start-Process $node -ArgumentList @('node_modules/vite/bin/vite.js','--host','0.0.0.0','--port',"$UiPort",'--strictPort') -WorkingDirectory $frontend -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs/ui.out.log" -RedirectStandardError "$logs/ui.err.log"
    @{id=$ui.Id;marker='node_modules/vite/bin/vite.js';port=$UiPort} | ConvertTo-Json | Set-Content -LiteralPath $statePath
    Write-Output "Demo UI listening on port $UiPort; API and MCP remain on loopback. Both API proxies use the isolated test database."
} finally {
    $env:EVENTQUEST_API_TARGET = $previousApi
    $env:EVENTQUEST_FAULT_API_TARGET = $previousFault
}
