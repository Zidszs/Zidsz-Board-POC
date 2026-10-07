@echo off
setlocal
cd /d "%~dp0"

:: Launcher simples - logica em setup.ps1 (evita bugs de encoding do CMD)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" pause
exit /b %EC%
