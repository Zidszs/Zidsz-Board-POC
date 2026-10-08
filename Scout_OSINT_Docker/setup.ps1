# Scout Network - Supervisor Docker
# Neste ramo a janela Tk foi removida. A gestao fica no Control Plane.
# Duplo-clique ou: setup.bat | powershell -File setup.ps1
$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeFile = Join-Path $Root "docker-compose.yml"
$HealthUrl = "http://127.0.0.1:8765/health"
$PainelScout = "http://localhost:8501/?aba=scout"

$script:LastExitCode = 0

function Write-Banner {
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  SCOUT GATE - Supervisor Docker"
    Write-Host "============================================"
    Write-Host ""
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

function Open-PainelScout {
    Write-Host "[*] A janela Tk do Scout foi removida neste ramo." -ForegroundColor Yellow
    Write-Host "[*] Abrindo a aba Scout do painel: $PainelScout" -ForegroundColor Green
    Start-Process $PainelScout
}

function Show-Status {
    $bk = "OFFLINE"
    if (Test-BackendHealth) { $bk = "ONLINE" }
    elseif (-not (Test-ContainerRunning)) { $bk = "PARADO" }

    Write-Host "  Backend Docker : $bk  ($HealthUrl)"
    Write-Host "  Gestao         : $PainelScout"
    Write-Host "  Janela Tk      : removida neste ramo (continua no master)"
    Write-Host "  Pasta projecto : $Root"
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
    Write-Host "[*] A derrubar Docker..."
    Invoke-DockerCompose @("down")
    if ((Get-LastExitCode) -ne 0) { Write-Host "[AVISO] docker compose down retornou codigo $(Get-LastExitCode)" }
    Write-Host "[OK] Container Scout encerrado."
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
        Write-Host "  [G]  Abrir o painel (aba Scout)"
        Write-Host "  [R]  Reiniciar backend Docker"
        Write-Host "  [Q]  ENCERRAR o container Scout"
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
            'G' { Open-PainelScout; Start-Sleep -Milliseconds 600 }
            'R' { Restart-Backend; Start-Sleep -Milliseconds 600 }
            'Q' { Stop-All; return }
        }
    }
}

# --- Main ---
try {
    $Host.UI.RawUI.WindowTitle = "Scout Network - Standby"
    Write-Banner

    Test-Docker
    Ensure-Backend
    Write-Host "[*] Gestao do Scout: $PainelScout" -ForegroundColor Green
    Write-Host "[*] A janela Tk nao faz parte deste ramo. Ela continua no branch master." -ForegroundColor Gray
    Enter-Standby
} catch {
    Write-Host ""
    Write-Host "[ERRO] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Read-Host "Pressione Enter para sair"
    exit 1
}
