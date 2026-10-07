@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo =================================================
echo  N8GROKER - FACTORY RESET
echo =================================================
echo.
echo  ATENCAO: Execute apenas numa COPIA do projeto.
echo  Isto apaga .env, base n8n, dados Scout/Porteiro,
echo  logs e estado de runtime. NAO ha desfazer.
echo.
echo  Pasta alvo:
echo    %~dp0
echo.
echo  Para confirmar, digite exactamente: Excluir
echo.
set /p CONFIRM="Confirmacao: "

if not "%CONFIRM%"=="Excluir" (
    echo.
    echo Cancelado. Nenhuma alteracao feita.
    pause
    exit /b 1
)

echo.
set /p CREATE_ENV="Criar .env a partir de .env_template? (S/N): "

set "PS_ARGS="
if /I "%CREATE_ENV%"=="S" set "PS_ARGS=-CreateEnvFromTemplate"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0factory_reset.ps1" %PS_ARGS%
set "EC=%ERRORLEVEL%"

echo.
if not "%EC%"=="0" (
    echo Factory reset terminou com codigo %EC%.
) else (
    echo Factory reset concluido.
)
pause
exit /b %EC%
