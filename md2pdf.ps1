param(
  [Parameter(Mandatory=$true)][string]$Md,
  [string]$Pdf = "",
  [ValidateSet("edge","chrome")][string]$Browser = "edge",
  [string]$Title = "",
  [switch]$KeepHtml
)
# Markdown -> PDF（Edge/Chrome 无头打印）。用法：
#   powershell -ExecutionPolicy Bypass -File md2pdf.ps1 -Md "笔记.md" [-Pdf "笔记.pdf"] [-Browser chrome] [-Title "..."]
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$mdPath = (Resolve-Path $Md).Path
if ([string]::IsNullOrWhiteSpace($Pdf)) {
  $Pdf = [System.IO.Path]::ChangeExtension($mdPath, ".pdf")
} elseif (-not [System.IO.Path]::IsPathRooted($Pdf)) {
  $Pdf = Join-Path (Split-Path -Parent $mdPath) $Pdf
}
$pdffull = [System.IO.Path]::GetFullPath($Pdf)

# 1) Markdown -> HTML
$htmlPath = [System.IO.Path]::ChangeExtension($mdPath, ".html")
$pyArgs = @("$here\md2html.py", "-i", $mdPath, "-o", $htmlPath)
if (-not [string]::IsNullOrWhiteSpace($Title)) { $pyArgs += @("-t", $Title) }
$env:PYTHONIOENCODING = 'utf-8'
. (Join-Path $here 'tools\python.ps1')
$py = Get-VideoNotesPython
& $py @pyArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $htmlPath)) { throw "HTML 生成失败" }

# 2) 找浏览器
$candidates = @()
if ($env:VIDEONOTES_BROWSER) { $candidates += $env:VIDEONOTES_BROWSER }
foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
  if (-not $base) { continue }
  if ($Browser -eq 'edge') {
    $candidates += (Join-Path $base 'Microsoft\Edge\Application\msedge.exe')
    $candidates += (Join-Path $base 'Google\Chrome\Application\chrome.exe')
  } else {
    $candidates += (Join-Path $base 'Google\Chrome\Application\chrome.exe')
    $candidates += (Join-Path $base 'Microsoft\Edge\Application\msedge.exe')
  }
}
$exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) { throw "找不到 $Browser 浏览器可执行文件" }

# 3) 无头打印 PDF
$tmpProfile = Join-Path $env:TEMP ("md2pdf-profile-" + [guid]::NewGuid().ToString("N").Substring(0,8))
$uri = ([System.Uri]::new($htmlPath)).AbsoluteUri
if (Test-Path $pdffull) { Remove-Item $pdffull -Force }
$arguments = @(
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--disable-extensions", "--user-data-dir=`"$tmpProfile`"",
  "--print-to-pdf=`"$pdffull`"", "--print-to-pdf-no-header", "--no-pdf-header-footer",
  "`"$uri`""
)
$p = Start-Process -FilePath $exe -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
if (-not (Test-Path $pdffull)) { throw "PDF 打印失败（浏览器退出码 $($p.ExitCode)）" }
$size = (Get-Item $pdffull).Length
if ($size -lt 2048) { throw "PDF 体积异常（$size 字节）" }

if (-not $KeepHtml) { Remove-Item $htmlPath -Force -ErrorAction SilentlyContinue }
Remove-Item $tmpProfile -Recurse -Force -ErrorAction SilentlyContinue
Write-Output ("[md2pdf] PDF 已生成: {0} ({1:N0} 字节)" -f $pdffull, $size)
Write-Output $pdffull
