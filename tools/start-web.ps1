# Start the service invisibly, then open the default browser once it is ready.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'python.ps1')
$port = if ($env:VIDEONOTES_PORT) { [int]$env:VIDEONOTES_PORT } else { 8765 }
$url = "http://127.0.0.1:$port"

function Test-VideoNotesReady {
    try {
        $instance = Invoke-RestMethod -Uri "$url/api/instance" -TimeoutSec 2 -UseBasicParsing
        return ($instance.ok -eq $true -and $instance.root -eq $projectRoot)
    } catch {
        return $false
    }
}

try {
    if ($port -lt 1 -or $port -gt 65535) { throw 'Invalid VIDEONOTES_PORT.' }
    if (-not (Test-VideoNotesReady)) {
        $pythonPath = Get-VideoNotesPython
        $logDirectory = Join-Path $projectRoot 'work'
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $env:PYTHONIOENCODING = 'utf-8'
        $env:PYTHONUNBUFFERED = '1'
        $serverScript = Join-Path $projectRoot 'server.py'
        $serverArguments = @(('"' + $serverScript + '"'), '--port', "$port")
        $serviceProcess = Start-Process -FilePath $pythonPath -ArgumentList $serverArguments `
            -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logDirectory 'web-service.log') `
            -RedirectStandardError (Join-Path $logDirectory 'web-service-error.log')
        $deadline = (Get-Date).AddSeconds(20)
        while (-not (Test-VideoNotesReady)) {
            if ($serviceProcess.HasExited -or (Get-Date) -gt $deadline) {
                throw 'The service did not start. See work\web-service-error.log for details.'
            }
            Start-Sleep -Milliseconds 250
        }
    }
    Start-Process $url
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Video Notes', 'OK', 'Error') | Out-Null
    exit 1
}
