param([switch]$WithOcr)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'python.ps1')
try {
    Set-Location -LiteralPath $projectRoot
    $basePython = Get-VideoNotesPython -IgnoreVenv
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $basePython -m venv (Join-Path $projectRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create .venv. Check Python venv/ensurepip installation.' }
    }
    $env:PYTHONIOENCODING = 'utf-8'
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip update failed. Check network/proxy settings.' }
    & $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the pip error above.' }
    if ($WithOcr) {
        & $venvPython -m pip install -r (Join-Path $projectRoot 'requirements-optional.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Optional OCR installation failed; basic environment is preserved.' }
    }
    & $venvPython -m video_notes.doctor
    if ($LASTEXITCODE -ne 0) { throw 'Python packages installed. Install the missing external tools listed above, then run doctor.bat.' }
    Write-Host 'Installation complete. Double-click start-web.vbs.' -ForegroundColor Green
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
