$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'python.ps1')
try {
    $pythonPath = Get-VideoNotesPython
    Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
    $env:PYTHONIOENCODING = 'utf-8'
    & $pythonPath -m video_notes.doctor
    exit $LASTEXITCODE
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
