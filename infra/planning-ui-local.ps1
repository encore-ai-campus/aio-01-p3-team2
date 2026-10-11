param([switch]$Stop, [int]$UiPort=5177, [int]$ApiPort=8004, [int]$FaultApiPort=8013)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$statePath = Join-Path $repositoryRoot 'frontend-web/test-results/planning-ui-process.json'
if ($Stop) {
    if (Test-Path -LiteralPath $statePath) {
        $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.id)"
        if ($process -and $process.CommandLine.Contains($saved.marker)) {
            Stop-Process -Id $saved.id
        }
        Remove-Item -LiteralPath $statePath
    }
    return
}
if (Test-Path -LiteralPath $statePath) { throw 'Stop the owned planning UI before restarting.' }
if (Get-NetTCPConnection -LocalPort $UiPort -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $UiPort is already occupied; existing server preserved."
}
$previousApi = $env:EVENTQUEST_API_TARGET
$previousFaultApi = $env:EVENTQUEST_FAULT_API_TARGET
try {
    $env:EVENTQUEST_API_TARGET = "http://127.0.0.1:$ApiPort"
    $env:EVENTQUEST_FAULT_API_TARGET = "http://127.0.0.1:$FaultApiPort"
    $node = (Get-Command node.exe).Source
    $logs = Split-Path $statePath
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $process = Start-Process $node -ArgumentList @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',"$UiPort",'--strictPort') -WorkingDirectory "$repositoryRoot/frontend-web" -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs/planning-ui.out.log" -RedirectStandardError "$logs/planning-ui.err.log"
    @{id=$process.Id;marker="--port $UiPort";ui="http://127.0.0.1:$UiPort";api=$env:EVENTQUEST_API_TARGET;faultApi=$env:EVENTQUEST_FAULT_API_TARGET} | ConvertTo-Json | Set-Content -LiteralPath $statePath
    Write-Output "Shared planning UI: http://127.0.0.1:$UiPort"
} finally {
    $env:EVENTQUEST_API_TARGET = $previousApi
    $env:EVENTQUEST_FAULT_API_TARGET = $previousFaultApi
}
