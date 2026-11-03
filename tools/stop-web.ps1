# Stop only this project instance, never unrelated services on the same port.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$port = if ($env:VIDEONOTES_PORT) { [int]$env:VIDEONOTES_PORT } else { 8765 }
try {
    $instance = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/instance" -TimeoutSec 3 -UseBasicParsing
    if (-not $instance.ok -or $instance.root -ne $root) { throw 'This port belongs to another application or project copy.' }
    $servicePid = [int]$instance.pid
    $service = Get-CimInstance Win32_Process -Filter "ProcessId = $servicePid"
    if (-not $service -or $service.Name -notmatch '^python(w)?\.exe$' -or $service.CommandLine -notmatch 'server\.py') {
        throw 'Cannot verify the service process; no process was stopped.'
    }
    & taskkill.exe /PID $servicePid /T /F
    if ($LASTEXITCODE -ne 0) { throw 'The service could not be stopped.' }
    Write-Host 'Video Notes stopped.' -ForegroundColor Green
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    Write-Host 'If the service is not running, no action is needed.'
    exit 1
}
