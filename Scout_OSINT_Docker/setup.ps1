# Scout Network - Supervisor Docker
# Duplo-clique ou: setup.bat | powershell -File setup.ps1
$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeFile = Join-Path $Root "docker-compose.yml"
$GuiScript = Join-Path $Root "Scout_network.py"
$ReqFile = Join-Path $Root "requirements.txt"
$HealthUrl = "http://127.0.0.1:8765/health"
$WsUrl = "ws://127.0.0.1:8765/ws"

$script:GuiProcess = $null
$script:LastExitCode = 0

function Write-Banner {
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  SCOUT GATE - Supervisor Docker"
    Write-Host "============================================"
    Write-Host ""
}

function Find-Python {
    $venv = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return @{ Exe = $venv; UsePyLauncher = $false } }
    if (Get-Command py -ErrorAction SilentlyContinue) { return @{ Exe = "py"; UsePyLauncher = $true } }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @{ Exe = "python"; UsePyLauncher = $false } }
    return $null
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)][string[]]$PyArgs,
        [switch]$ShowOutput
    )

    $info = Find-Python
    if (-not $info) {
        $script:LastExitCode = 9009
        return
    }

    if ($ShowOutput) {
        if ($info.UsePyLauncher) { & $info.Exe -3 @PyArgs }
        else { & $info.Exe @PyArgs }
    } else {
        if ($info.UsePyLauncher) { & $info.Exe -3 @PyArgs | Out-Null }
        else { & $info.Exe @PyArgs | Out-Null }
    }

    $script:LastExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
}

function Get-LastExitCode {
    if ($null -eq $script:LastExitCode) { return 0 }
    return [int]$script:LastExitCode
}

function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker nao encontrado. Instale Docker Desktop."
    }
}

function Ensure-Venv {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return }

    Write-Host "[*] A criar ambiente virtual .venv ..."
    $bootstrap = Find-Python
    if (-not $bootstrap) { throw "Python nao encontrado para criar venv." }

    if ($bootstrap.UsePyLauncher) {
        & py -3 -m venv (Join-Path $Root ".venv")
    } else {
        & $bootstrap.Exe -m venv (Join-Path $Root ".venv")
    }
    $script:LastExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
    if ((Get-LastExitCode) -ne 0) {
        throw "Falha ao criar .venv (codigo $(Get-LastExitCode))."
    }
    Write-Host "    .venv criado."
}

function Ensure-PipDeps {
    Ensure-Venv
    Write-Host "[*] Verificando dependencias Python..."
    Invoke-Python -PyArgs @("-c", "import requests, websocket")
    if ((Get-LastExitCode) -ne 0) {
        Write-Host "    A instalar requirements.txt (sem cache) ..."
        Invoke-Python -PyArgs @("-m", "pip", "install", "--no-cache-dir", "-r", $ReqFile) -ShowOutput
        if ((Get-LastExitCode) -ne 0) { throw "pip install falhou (codigo $(Get-LastExitCode))." }
    }
    Write-Host "    OK"
}

function Test-BackendHealth {
    try {
        $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Test-ContainerRunning {
    $out = docker ps --filter "name=scout-backend" --filter "status=running" --format "{{.Names}}" 2>$null
    return ($out -match "scout-backend")
}

function Wait-BackendHealth {
    param([int]$MaxTries = 45)
    Write-Host "[*] A aguardar $HealthUrl ..."
    for ($i = 1; $i -le $MaxTries; $i++) {
        if (Test-BackendHealth) {
            Write-Host "    Backend respondeu ($i tentativas)."
            return $true
        }
        Start-Sleep -Seconds 1
    }
    Write-Host "[AVISO] Timeout no health check."
    return $false
}

function Invoke-DockerCompose {
    param([Parameter(Mandatory = $true)][string[]]$ComposeArgs)
    Push-Location $Root
    try {
        & docker compose -f $ComposeFile @ComposeArgs
        $script:LastExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
    } finally {
        Pop-Location
    }
}

function Ensure-Backend {
    if (Test-BackendHealth) {
        Write-Host "[*] Backend ja online - reutilizando container."
        return
    }

    if (Test-ContainerRunning) {
        Write-Host "[*] Container a correr mas health falhou - a reiniciar..."
        Invoke-DockerCompose @("restart", "scout-backend")
        if ((Get-LastExitCode) -ne 0) { throw "docker compose restart falhou." }
        if (Wait-BackendHealth -MaxTries 30) { return }
        Write-Host "[AVISO] Reinicio nao recuperou health. A reconstruir..."
    }

    Write-Host "[*] Backend offline - a construir e iniciar..."
    Invoke-DockerCompose @("up", "-d", "--build")
    if ((Get-LastExitCode) -ne 0) { throw "docker compose up falhou (codigo $(Get-LastExitCode))." }

    if (-not (Wait-BackendHealth -MaxTries 45)) {
        throw "Backend nao respondeu em :8765/health"
    }
    Write-Host "[OK] Backend activo."
}

function Get-GuiProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*Scout_network.py*" }
}

function Test-GuiRunning {
    foreach ($p in (Get-GuiProcesses)) {
        try {
            $proc = Get-Process -Id $p.ProcessId -ErrorAction Stop
            if (-not $proc.HasExited) {
                $script:GuiProcess = $proc
                return $true
            }
        } catch {}
    }
    $script:GuiProcess = $null
    return $false
}

function Start-Gui {
    if (Test-GuiRunning) {
        Write-Host "[*] GUI ja esta aberta (PID $($script:GuiProcess.Id))."
        return
    }

    Write-Host "[*] A abrir interface grafica..."
    $env:SCOUT_BACKEND_URL = $WsUrl
    $env:SCOUT_DOCKER_MANAGED = "1"

    $info = Find-Python
    if (-not $info) { throw "Python nao encontrado para GUI." }

    # PS 5.1 reparte ArgumentList nos espaços; aspas mantêm o caminho inteiro.
    if ($info.UsePyLauncher) {
        $script:GuiProcess = Start-Process -FilePath $info.Exe -ArgumentList @("-3", "`"$GuiScript`"") -PassThru -WindowStyle Normal
    } else {
        $script:GuiProcess = Start-Process -FilePath $info.Exe -ArgumentList @("`"$GuiScript`"") -PassThru -WindowStyle Normal
    }
    Write-Host "[OK] GUI iniciada (PID $($script:GuiProcess.Id))."
}

function Stop-Gui {
    foreach ($p in (Get-GuiProcesses)) {
        Write-Host "    A encerrar GUI PID $($p.ProcessId) ..."
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $script:GuiProcess = $null
}

function Show-Status {
    $bk = "OFFLINE"
    if (Test-BackendHealth) { $bk = "ONLINE" }
    elseif (-not (Test-ContainerRunning)) { $bk = "PARADO" }

    $gui = if (Test-GuiRunning) { "ABERTA" } else { "FECHADA" }

    Write-Host "  Backend Docker : $bk  ($HealthUrl)"
    Write-Host "  Interface GUI  : $gui"
    if ($script:GuiProcess -and -not $script:GuiProcess.HasExited) {
        Write-Host "  GUI PID          : $($script:GuiProcess.Id)"
    }
    Write-Host "  Pasta projecto   : $Root"
}

function Restart-Backend {
    Write-Host ""
    Write-Host "[*] A reiniciar scout-backend..."
    Invoke-DockerCompose @("restart", "scout-backend")
    if ((Get-LastExitCode) -ne 0) { Write-Host "[AVISO] restart retornou codigo $(Get-LastExitCode)" }
    Wait-BackendHealth -MaxTries 30 | Out-Null
}

function Stop-All {
    Write-Host ""
    Write-Host "[*] A encerrar Scout Network..."
    Stop-Gui
    Write-Host "[*] A derrubar Docker..."
    Invoke-DockerCompose @("down")
    if ((Get-LastExitCode) -ne 0) { Write-Host "[AVISO] docker compose down retornou codigo $(Get-LastExitCode)" }
    Write-Host "[OK] Tudo encerrado."
}

function Enter-Standby {
    while ($true) {
        Clear-Host
        Write-Host ""
        Write-Host "  SCOUT NETWORK - STANDBY (supervisor activo)"
        Write-Host "  -------------------------------------------"
        Write-Host ""
        Show-Status
        Write-Host ""
        Write-Host "  -------------------------------------------"
        Write-Host "  [G]  Abrir / reabrir interface grafica"
        Write-Host "  [R]  Reiniciar backend Docker"
        Write-Host "  [Q]  ENCERRAR TUDO (GUI + Docker)"
        Write-Host "  -------------------------------------------"
        Write-Host ""
        Write-Host -NoNewline "  Tecla (G/R/Q): "

        try {
            $key = [Console]::ReadKey($true).KeyChar
        } catch {
            $input = Read-Host "Opcao (G/R/Q)"
            if (-not $input) { continue }
            $key = $input[0]
        }
        Write-Host $key

        switch ([char]::ToUpper($key)) {
            'G' { Start-Gui; Start-Sleep -Milliseconds 600 }
            'R' { Restart-Backend; Start-Sleep -Milliseconds 600 }
            'Q' { Stop-All; return }
        }
    }
}

# --- Main ---
try {
    $Host.UI.RawUI.WindowTitle = "Scout Network - Standby"
    Write-Banner

    if (-not (Find-Python)) { throw "Python 3.10+ nao encontrado." }
    Test-Docker
    Ensure-PipDeps
    Ensure-Backend
    Start-Gui
    Enter-Standby
} catch {
    Write-Host ""
    Write-Host "[ERRO] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Read-Host "Pressione Enter para sair"
    exit 1
}
