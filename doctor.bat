@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\doctor.ps1"
set "doctor_result=%errorlevel%"
echo.
pause
exit /b %doctor_result%
