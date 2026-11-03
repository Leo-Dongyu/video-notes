# 关闭 Chrome -> 用临时 profile + CDP 读取 B 站 cookie -> 重新打开 Chrome
# 用法（在项目目录中打开 PowerShell）：
#   powershell -ExecutionPolicy Bypass -File tools\chrome-debug.ps1
# 可选：-Force 跳过确认；-Port 指定 CDP 端口；-Python 指定解释器
param(
  [switch]$Force,
  [int]$Port = 9222,
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot 'python.ps1')
if (-not $Python) { $Python = Get-VideoNotesPython }

function Find-Chrome {
  $candidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
  )
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  throw "未找到 chrome.exe"
}

$chrome = Find-Chrome
Write-Host "Chrome: $chrome"

$running = @(Get-Process -Name chrome -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
  if (-not $Force) {
    $ans = Read-Host "需要先关闭 Chrome 的 $($running.Count) 个进程才能读取 cookie 数据库，是否继续？(y/N)"
    if ($ans -notmatch '^(y|Y)') { Write-Host "已取消"; exit 1 }
  }
  Write-Host "正在关闭 Chrome ..."
  foreach ($p in $running) { try { [void]$p.CloseMainWindow() } catch {} }
  $deadline = (Get-Date).AddSeconds(12)
  while ((Get-Date) -lt $deadline -and @(Get-Process -Name chrome -ErrorAction SilentlyContinue).Count -gt 0) {
    Start-Sleep -Milliseconds 250
  }
  Get-Process -Name chrome -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
  if (@(Get-Process -Name chrome -ErrorAction SilentlyContinue).Count -gt 0) {
    Write-Warning "仍有 Chrome 进程未退出，读取大概率失败"
  } else {
    Write-Host "Chrome 已关闭"
  }
}

Write-Host "开始读取 cookie（临时 profile + CDP）..."
& $Python tools\chrome_cookies.py --force --cdp $Port -o cookies\bilibili.txt
$code = $LASTEXITCODE
Write-Host "读取脚本退出码：$code"

$cookieFile = Join-Path $root "cookies\bilibili.txt"
if (Test-Path $cookieFile) {
  $n = (Get-Content $cookieFile | Where-Object { $_ -and $_ -notmatch '^#' }).Count
  Write-Host "cookie 文件已写入：$cookieFile（约 $n 行）"
} else {
  Write-Warning "未生成 cookies\bilibili.txt，请改用 Web 界面的「B站扫码登录」"
}

Write-Host "重新打开 Chrome ..."
Start-Process $chrome "https://www.bilibili.com" | Out-Null
Write-Host "完成。回到网页点击「从 Chrome 导入」或直接刷新状态。"
