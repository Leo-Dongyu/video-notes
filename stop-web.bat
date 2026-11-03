@echo off
chcp 65001 >nul
title Stop Video Notes Server
echo ==========================================
echo   Stopping Video Notes web service...
echo   port: 8765 (edit stop-web.bat if changed)
echo ==========================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\stop-web.ps1"
echo.
ping -n 4 127.0.0.1 >nul
