# Shared Python discovery. Never assumes Anaconda or a particular drive.
function Get-VideoNotesPython {
    param([switch]$IgnoreVenv)
    $root = Split-Path -Parent $PSScriptRoot
    $candidates = @()
    if ($env:VIDEONOTES_PYTHON) { $candidates += $env:VIDEONOTES_PYTHON }
    if (-not $IgnoreVenv) { $candidates += (Join-Path $root '.venv\Scripts\python.exe') }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('-3.13','-3.12','-3.11')) {
            try {
                $found = & $launcher.Source $version -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0 -and $found) { $candidates += [string]$found }
            } catch { continue }
        }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch '\\WindowsApps\\') { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            & $candidate -c 'import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,13) and sys.maxsize>2**32 else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { continue }
    }
    throw 'Python 3.11-3.13 (64-bit) not found. Install it from python.org, then run install.bat.'
}
