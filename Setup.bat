@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo =================================================
echo  N8GROKER - SETUP DO PROJETO
echo =================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_projeto.ps1"
set "EC=%ERRORLEVEL%"

if not "%EC%"=="0" (
    echo.
    echo Setup terminou com codigo %EC%.
)
pause
exit /b %EC%
