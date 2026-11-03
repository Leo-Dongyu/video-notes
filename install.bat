@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install.ps1" %*
set "install_result=%errorlevel%"
echo.
pause
exit /b %install_result%
