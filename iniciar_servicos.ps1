# =======================================================
# CONFIGURAÇÃO DE CAMINHOS DINÂMICOS (100% Portátil)
# =======================================================
$PATH_N8N             = "$PSScriptRoot\n8n"
$PATH_NGROK           = "$PSScriptRoot\ngrok"
$PATH_PORTEIRO        = "$PSScriptRoot\porteiro"
$PATH_SCOUT           = "$PSScriptRoot\Scout_OSINT_Docker"
$PATH_ENV             = "$PSScriptRoot\.env"
$PATH_PID_PORTEIRO    = "$PSScriptRoot\.porteiro.pid"
$PATH_LOG_PORTEIRO    = "$PATH_N8N\storage\Porteiro\registro_portaria.log"
$PATH_BANCO_PORTEIRO  = "$PATH_N8N\storage\Porteiro\controle_acesso.json"
$COMPOSE_N8N          = "$PATH_N8N\docker-compose.yml"
$COMPOSE_NGROK        = "$PATH_NGROK\docker-compose.yml"
$COMPOSE_SCOUT        = "$PATH_SCOUT\docker-compose.yml"
$GUI_SCOUT            = "$PATH_SCOUT\Scout_network.py"
$REQ_SCOUT            = "$PATH_SCOUT\requirements.txt"
$PORTA_PORTEIRO       = 5677
$SCOUT_HEALTH_URL     = "http://127.0.0.1:8765/health"
$SCOUT_REDIRECT_URL   = "http://127.0.0.1:8765/redirections"
$PATH_SUPERVISOR_LOCK  = "$PSScriptRoot\.n8groker.supervisor.lock"
$PATH_SHUTDOWN_REQUEST = "$PSScriptRoot\.n8groker.shutdown.request"

$oldUrl = $null
$script:UseScout = $false
$script:ScoutPublicPort = 4050

Clear-Host
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host " INICIANDO A ARQUITETURA E FIREWALL " -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan

# =======================================================
# FUNÇÕES UTILITÁRIAS
# =======================================================
function Confirm-Interactive {
    if ([Environment]::UserInteractive) {
        Write-Host "`nPressione ENTER para fechar..." -ForegroundColor Cyan
        Read-Host | Out-Null
    }
}

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [string]$Default = ""
    )
    if (-not (Test-Path $PATH_ENV)) { return $Default }
    foreach ($line in Get-Content $PATH_ENV -Encoding UTF8) {
        if ($line -match "^\s*#") { continue }
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*)$") {
            $val = $Matches[1].Trim()
            if ($val -match "^#") { return $Default }
            return $val
        }
    }
    return $Default
}

function Test-ScoutEnabled {
    $flag = Get-EnvValue "USE_SCOUT" "1"
    return ($flag -match "^(1|true|yes|sim|on)$")
}

function Register-Supervisor {
    if (Test-Path $PATH_SHUTDOWN_REQUEST) {
        Remove-Item $PATH_SHUTDOWN_REQUEST -Force -ErrorAction SilentlyContinue
    }
    $lock = @{
        pid        = $PID
        started_at = (Get-Date).ToUniversalTime().ToString("o")
    } | ConvertTo-Json -Compress
    Set-Content -Path $PATH_SUPERVISOR_LOCK -Value $lock -Encoding UTF8
}

function Unregister-Supervisor {
    foreach ($p in @($PATH_SUPERVISOR_LOCK, $PATH_SHUTDOWN_REQUEST)) {
        if (Test-Path $p) {
            Remove-Item $p -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-ShutdownRequest {
    if (-not (Test-Path $PATH_SHUTDOWN_REQUEST)) { return $false }
    try {
        $raw = Get-Content $PATH_SHUTDOWN_REQUEST -Raw -Encoding UTF8
        Remove-Item $PATH_SHUTDOWN_REQUEST -Force -ErrorAction Stop
        $obj = $raw | ConvertFrom-Json
        if ($obj.source -ne "scout_gui") { return $false }
    } catch {
        Remove-Item $PATH_SHUTDOWN_REQUEST -Force -ErrorAction SilentlyContinue
        return $false
    }
    Write-Host "[SCOUT] Pedido de shutdown recebido da GUI." -ForegroundColor Yellow
    return $true
}

function Invoke-Compose {
    param(
        [Parameter(Mandatory = $true)][string]$ComposeFile,
        [Parameter(Mandatory = $true)][string[]]$ComposeArgs
    )
    # Evita NativeCommandError do PowerShell 5.1 quando docker escreve progresso em stderr.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & docker-compose -f $ComposeFile --env-file $PATH_ENV @ComposeArgs 2>$null | Out-Null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Stop-Porteiro {
    if (Test-Path $PATH_PID_PORTEIRO) {
        $pidVal = Get-Content $PATH_PID_PORTEIRO -ErrorAction SilentlyContinue
        if ($pidVal) {
            $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -eq 'node') {
                Stop-Process -Id $pidVal -Force -ErrorAction SilentlyContinue
            }
        }
        Remove-Item $PATH_PID_PORTEIRO -ErrorAction SilentlyContinue
    }
    try {
        $conn = Get-NetTCPConnection -LocalPort $PORTA_PORTEIRO -State Listen -ErrorAction SilentlyContinue
        if ($conn) {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    } catch { }
}

function Start-Porteiro {
    Stop-Porteiro
    Write-Host "[+] Iniciando Porteiro (Node.js)..." -ForegroundColor Yellow
    $proc = Start-Process "node" -ArgumentList "porteiro.js" `
        -WorkingDirectory $PATH_PORTEIRO -WindowStyle Hidden -PassThru
    if ($proc) {
        $proc.Id | Set-Content $PATH_PID_PORTEIRO
    }
}

function Test-ScoutHealth {
    try {
        $r = Invoke-WebRequest -Uri $SCOUT_HEALTH_URL -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Wait-ScoutHealth {
    param([int]$MaxTries = 45)
    Write-Host "[SCOUT] Aguardando backend em $SCOUT_HEALTH_URL ..." -ForegroundColor Gray
    for ($i = 1; $i -le $MaxTries; $i++) {
        if (Test-ScoutHealth) {
            Write-Host "[OK] Scout backend respondeu ($i tentativas)." -ForegroundColor Green
            return $true
        }
        Start-Sleep -Seconds 1
    }
    Write-Host "[AVISO] Scout backend nao respondeu a tempo." -ForegroundColor Yellow
    return $false
}

function Stop-Scout {
    if (-not (Test-Path $COMPOSE_SCOUT)) { return }
    Write-Host "[*] Derrubando container scout-backend..." -ForegroundColor Yellow
    Invoke-Compose -ComposeFile $COMPOSE_SCOUT -ComposeArgs @("down") | Out-Null
}

function Start-Scout {
    if (-not (Test-Path $COMPOSE_SCOUT)) {
        throw "Scout_OSINT_Docker/docker-compose.yml nao encontrado."
    }
    Write-Host "[+] Iniciando Scout Gate (Docker)..." -ForegroundColor Yellow
    $code = Invoke-Compose -ComposeFile $COMPOSE_SCOUT -ComposeArgs @("up", "-d", "--build")
    if ($code -ne 0) {
        throw "docker compose up scout-backend falhou (codigo $code)."
    }
    if (-not (Wait-ScoutHealth)) {
        throw "Scout backend nao respondeu em :8765/health"
    }
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Value
    )
    if (-not (Test-Path $PATH_ENV)) { return }
    $lines = Get-Content $PATH_ENV -Encoding UTF8
    $found = $false
    $newLines = foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=") {
            $found = $true
            "$Key=$Value"
        } else {
            $line
        }
    }
    if (-not $found) {
        $newLines = @($newLines) + "$Key=$Value"
    }
    Set-Content -Path $PATH_ENV -Value $newLines -Encoding UTF8
}

function Sync-ScoutPorteiroRoute {
    $publicPort = [int](Get-EnvValue "SCOUT_PUBLIC_PORT" "4050")
    $upstreamHost = Get-EnvValue "SCOUT_UPSTREAM_HOST" "host.docker.internal"
    $upstreamPort = [int](Get-EnvValue "SCOUT_UPSTREAM_PORT" "5677")
    $base = "http://127.0.0.1:8765"
    try {
        $patchBody = @{
            listen_port    = $publicPort
            upstream_host  = $upstreamHost
            upstream_port  = $upstreamPort
            enabled        = $true
        } | ConvertTo-Json
        Invoke-RestMethod -Uri "$base/redirections/porteiro-manual" -Method PATCH `
            -Body $patchBody -ContentType "application/json" -TimeoutSec 5 | Out-Null

        $data = Invoke-RestMethod -Uri "$base/redirections" -TimeoutSec 5
        foreach ($entry in @($data.entries)) {
            if ($entry.id -eq "porteiro-manual") { continue }
            $shouldDisable = $false
            if ($entry.enabled -and $entry.name -in @("n8n_app", "ngrok_service")) {
                $shouldDisable = $true
            }
            if ($entry.enabled -and [int]$entry.listen_port -eq $publicPort) {
                $shouldDisable = $true
            }
            if ($shouldDisable) {
                $toggleBody = @{ id = $entry.id; enabled = $false } | ConvertTo-Json
                Invoke-RestMethod -Uri "$base/redirections/toggle" -Method POST `
                    -Body $toggleBody -ContentType "application/json" -TimeoutSec 5 | Out-Null
            }
        }
        Write-Host "[OK] Scout rota porteiro sincronizada ($upstreamHost`:$upstreamPort listen :$publicPort)" -ForegroundColor Green
    } catch {
        Write-Host "[AVISO] Falha ao sincronizar rota Scout: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

function Find-ScoutPython {
    $venv = Join-Path $PATH_SCOUT ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return @{ Exe = $venv; UsePyLauncher = $false } }
    if (Get-Command py -ErrorAction SilentlyContinue) { return @{ Exe = "py"; UsePyLauncher = $true } }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @{ Exe = "python"; UsePyLauncher = $false } }
    return $null
}

function Ensure-ScoutPythonDeps {
    $info = Find-ScoutPython
    if (-not $info) { throw "Python 3.10+ necessario para a GUI do Scout." }

    $venvPy = Join-Path $PATH_SCOUT ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Host "[SCOUT] Criando ambiente virtual Python..." -ForegroundColor Gray
        if ($info.UsePyLauncher) { & py -3 -m venv (Join-Path $PATH_SCOUT ".venv") }
        else { & $info.Exe -m venv (Join-Path $PATH_SCOUT ".venv") }
    }

    $py = Join-Path $PATH_SCOUT ".venv\Scripts\python.exe"
    & $py -c "import requests, websocket" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[SCOUT] Instalando dependencias Python..." -ForegroundColor Gray
        & $py -m pip install --no-cache-dir -r $REQ_SCOUT
        if ($LASTEXITCODE -ne 0) { throw "pip install Scout falhou." }
    }
}

function Get-ScoutGuiProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*Scout_network.py*" }
}

function Start-ScoutGui {
    Ensure-ScoutPythonDeps
    foreach ($p in (Get-ScoutGuiProcesses)) {
        try {
            $proc = Get-Process -Id $p.ProcessId -ErrorAction Stop
            if (-not $proc.HasExited) {
                Write-Host "[SCOUT] GUI ja aberta (PID $($proc.Id))." -ForegroundColor Gray
                return
            }
        } catch { }
    }

    $py = Join-Path $PATH_SCOUT ".venv\Scripts\python.exe"
    $env:N8GROKER_ROOT = $PSScriptRoot
    $env:SCOUT_BACKEND_URL = Get-EnvValue "SCOUT_BACKEND_URL" "ws://127.0.0.1:8765/ws"
    $env:SCOUT_DOCKER_MANAGED = "1"
    Write-Host "[SCOUT] Abrindo interface grafica..." -ForegroundColor Yellow

    # ProcessStartInfo garante cwd + env no Windows (Start-Process nem sempre herda tudo).
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $py
    $psi.Arguments = "`"$GUI_SCOUT`""
    $psi.WorkingDirectory = $PATH_SCOUT
    $psi.UseShellExecute = $false
    $null = $psi.EnvironmentVariables["N8GROKER_ROOT"] = $PSScriptRoot
    $null = $psi.EnvironmentVariables["SCOUT_BACKEND_URL"] = $env:SCOUT_BACKEND_URL
    $null = $psi.EnvironmentVariables["SCOUT_DOCKER_MANAGED"] = "1"
    [System.Diagnostics.Process]::Start($psi) | Out-Null
}

function Ensure-ArquivosN8n {
    # Pasta dos fluxos na raiz. O legado n8n/n8n/storage era o volume dobrado
    # ./n8n/storage (compose dentro de n8n/) em /home/node/.n8n-files.
    $dest = Join-Path $PSScriptRoot "Arquivos-n8n"
    if (-not (Test-Path -LiteralPath $dest)) {
        New-Item -ItemType Directory -Path $dest | Out-Null
    }
    $legacy = Join-Path $PSScriptRoot "n8n\n8n\storage"
    if (-not (Test-Path -LiteralPath $legacy)) { return }
    $markerName = ".migrado-para-Arquivos-n8n"
    $marker = Join-Path $legacy $markerName
    if (Test-Path -LiteralPath $marker) { return }
    $pending = @()
    foreach ($item in @(Get-ChildItem -LiteralPath $legacy -Force)) {
        if ($item.Name -eq $markerName) { continue }
        $target = Join-Path $dest $item.Name
        if (Test-Path -LiteralPath $target) {
            $pending += $item.Name
            Write-Host "[AVISO] Ja existe em Arquivos-n8n; nao movido: $($item.Name)" -ForegroundColor Yellow
        } else {
            Move-Item -LiteralPath $item.FullName -Destination $target
            Write-Host "[OK] Movido de n8n/n8n/storage para Arquivos-n8n: $($item.Name)" -ForegroundColor Green
        }
    }
    if ($pending.Count -eq 0) {
        Set-Content -LiteralPath $marker -Value "conteudo de n8n/n8n/storage movido para Arquivos-n8n" -Encoding UTF8
    }
}

function Get-NgrokTunnelTarget {
    if ($script:UseScout) {
        $port = Get-EnvValue "SCOUT_PUBLIC_PORT" "4050"
        return $port
    }
    return "$PORTA_PORTEIRO"
}

$script:desligamentoExecutado = $false
function Stop-Tudo {
    if ($script:desligamentoExecutado) { return }
    $script:desligamentoExecutado = $true

    Clear-Host
    Write-Host "=================================================" -ForegroundColor Red
    Write-Host " INICIANDO DESLIGAMENTO SEGURO DA INFRAESTRUTURA " -ForegroundColor Red
    Write-Host "=================================================" -ForegroundColor Red

    $step = 1
    $total = if ($script:UseScout) { 4 } else { 3 }

    Write-Host "[$step/$total] Derrubando container do Ngrok..." -ForegroundColor Yellow
    Invoke-Compose -ComposeFile $COMPOSE_NGROK -ComposeArgs @("down") | Out-Null
    $step++

    Write-Host "[$step/$total] Derrubando container do n8n..." -ForegroundColor Yellow
    Invoke-Compose -ComposeFile $COMPOSE_N8N -ComposeArgs @("down") | Out-Null
    $step++

    if ($script:UseScout) {
        Write-Host "[$step/$total] Derrubando Scout Gate..." -ForegroundColor Yellow
        Stop-Scout
        $step++
    }

    Write-Host "[$step/$total] Encerrando o Porteiro (Node.js)..." -ForegroundColor Yellow
    Stop-Porteiro

    Unregister-Supervisor

    Write-Host "`nDesligamento concluido com sucesso! Todos os recursos foram liberados." -ForegroundColor Green
    Start-Sleep -Seconds 1
    Confirm-Interactive
}

trap { Stop-Tudo; break }

# =======================================================
# 0. CHECAGEM DE PRÉ-REQUISITOS (INSTALAÇÃO)
# =======================================================
Write-Host '[SISTEMA] Verificando pre-requisitos...' -ForegroundColor Gray

try {
    $nodeVersion = node -v 2>$null
    if (-not $nodeVersion) { throw "Node ausente" }
    Write-Host "[OK] Node.js detectado ($nodeVersion)" -ForegroundColor Green
} catch {
    Write-Host "`n[ERRO CRITICO] Node.js nao encontrado!" -ForegroundColor Red -BackgroundColor Black
    Write-Host "Causa: O Porteiro precisa do Node.js para rodar nativamente no Windows." -ForegroundColor Yellow
    Write-Host "Solucao: Instale em https://nodejs.org e reinicie o terminal." -ForegroundColor White
    Confirm-Interactive
    Exit 1
}

try {
    docker info > $null 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Docker offline" }
    Write-Host "[OK] Docker detectado e em execucao" -ForegroundColor Green
} catch {
    Write-Host "`n[ERRO CRITICO] Docker Desktop nao esta rodando!" -ForegroundColor Red -BackgroundColor Black
    Write-Host "Causa: O n8n e o Ngrok dependem do motor do Docker ativo." -ForegroundColor Yellow
    Write-Host "Solucao: Abra o Docker Desktop, espere ele ficar 'Green' e rode o script." -ForegroundColor White
    Confirm-Interactive
    Exit 1
}

if (-not (Test-Path $PATH_ENV)) {
    Write-Host "`n[ERRO] Arquivo .env nao detectado na raiz do projeto!" -ForegroundColor Red -BackgroundColor Black
    Write-Host "Causa: As chaves do n8n e Ngrok estao faltando." -ForegroundColor Yellow
    Write-Host "Solucao: Copie o arquivo .env_template para .env e preencha seus dados." -ForegroundColor White
    Confirm-Interactive
    Exit 1
}

$script:UseScout = Test-ScoutEnabled
$script:ScoutPublicPort = Get-EnvValue "SCOUT_PUBLIC_PORT" "4050"

if ($script:UseScout) {
    if (-not (Test-Path $COMPOSE_SCOUT)) {
        Write-Host "`n[ERRO] USE_SCOUT=1 mas Scout_OSINT_Docker nao encontrado." -ForegroundColor Red
        Confirm-Interactive
        Exit 1
    }
    try {
        Ensure-ScoutPythonDeps
        Write-Host "[OK] Scout Gate habilitado (Python + deps)" -ForegroundColor Green
    } catch {
        Write-Host "`n[ERRO] Scout requer Python 3.10+ para a GUI." -ForegroundColor Red
        Write-Host "Detalhe: $($_.Exception.Message)" -ForegroundColor Yellow
        Confirm-Interactive
        Exit 1
    }
} else {
    Write-Host '[INFO] Scout desabilitado (USE_SCOUT=0) - modo legado directo ao Porteiro' -ForegroundColor Gray
}

Write-Host "[OK] Configuracoes de seguranca (.env) carregadas" -ForegroundColor Green
Write-Host "-------------------------------------------------" -ForegroundColor Gray

try {
    Register-Supervisor

    # =======================================================
    # 1. INICIALIZAÇÃO DOS SERVIÇOS
    # =======================================================
    Start-Porteiro
    Start-Sleep -Seconds 3

    if ($script:UseScout) {
        Start-Scout
        Sync-ScoutPorteiroRoute
    }

    $env:NGROK_TUNNEL_TARGET = Get-NgrokTunnelTarget
    Write-Host "[REDE] Ngrok upstream: host.docker.internal:$env:NGROK_TUNNEL_TARGET" -ForegroundColor Gray

    Ensure-ArquivosN8n
    Write-Host "[+] Iniciando Conteineres Docker..." -ForegroundColor Yellow
    $codeN8n = Invoke-Compose -ComposeFile $COMPOSE_N8N -ComposeArgs @("up", "-d")
    if ($codeN8n -ne 0) { throw "docker compose up n8n falhou (codigo $codeN8n)." }
    $codeNgrok = Invoke-Compose -ComposeFile $COMPOSE_NGROK -ComposeArgs @("up", "-d")
    if ($codeNgrok -ne 0) { throw "docker compose up ngrok falhou (codigo $codeNgrok)." }

    Write-Host "[SISTEMA] Aguardando estabilizacao dos servicos..." -ForegroundColor Gray
    Start-Sleep -Seconds 5

    # =======================================================
    # LOOP DO PAINEL DE MONITORAMENTO (HUD)
    # =======================================================
    $exitRequested = $false

    while ($true) {
        try {
            if (Test-ShutdownRequest) {
                $exitRequested = $true
                break
            }

            Clear-Host
            Write-Host "=================================================" -ForegroundColor Cyan
            Write-Host "  [PAINEL] CENTRAL DE INFRAESTRUTURA E FIREWALL  " -ForegroundColor Cyan
            Write-Host "=================================================" -ForegroundColor Cyan

            if ($script:UseScout) {
                Write-Host (' [MODO]    Scout Gate ACTIVO - porta ' + $script:ScoutPublicPort) -ForegroundColor Magenta
            } else {
                Write-Host ' [MODO]    Legado (Ngrok -> Porteiro directo)' -ForegroundColor Gray
            }

            # ---------------------------------------------------
            # HUD DE SAÚDE DOS CONTÊINERES
            # ---------------------------------------------------
            $n8nStatus   = docker ps -a --filter "name=^n8n_app$" --format "{{.Status}}"
            $ngrokStatus = docker ps -a --filter "name=^ngrok_service$" --format "{{.Status}}"
            $scoutStatus = $null
            if ($script:UseScout) {
                $scoutStatus = docker ps -a --filter "name=^scout-backend$" --format "{{.Status}}"
            }

            $corN8n   = if ($n8nStatus   -match "Up") { "Green" } elseif ($n8nStatus   -match "Restarting") { "Yellow" } else { "Red" }
            $corNgrok = if ($ngrokStatus -match "Up") { "Green" } elseif ($ngrokStatus -match "Restarting") { "Yellow" } else { "Red" }
            $corScout = "Gray"

            if (-not $n8nStatus)   { $n8nStatus   = "Nao Encontrado/Criado"; $corN8n   = "Red" }
            if (-not $ngrokStatus) { $ngrokStatus = "Nao Encontrado/Criado"; $corNgrok = "Red" }

            Write-Host " [SERVICO] " -NoNewline -ForegroundColor White
            Write-Host "n8n_app " -ForegroundColor $corN8n -NoNewline
            Write-Host "-> Status: $n8nStatus" -ForegroundColor Gray

            Write-Host " [SERVICO] " -NoNewline -ForegroundColor White
            Write-Host "ngrok   " -ForegroundColor $corNgrok -NoNewline
            Write-Host "-> Status: $ngrokStatus" -ForegroundColor Gray

            if ($script:UseScout) {
                if (-not $scoutStatus) { $scoutStatus = "Nao Encontrado/Criado"; $corScout = "Red" }
                else { $corScout = if ($scoutStatus -match "Up") { "Green" } elseif ($scoutStatus -match "Restarting") { "Yellow" } else { "Red" } }
                Write-Host " [SERVICO] " -NoNewline -ForegroundColor White
                Write-Host "scout   " -ForegroundColor $corScout -NoNewline
                Write-Host "-> Status: $scoutStatus" -ForegroundColor Gray
            }

            Write-Host "-------------------------------------------------" -ForegroundColor Gray

            # ---------------------------------------------------
            # HUD SCOUT (rotas activas)
            # ---------------------------------------------------
            if ($script:UseScout) {
                try {
                    if (Test-ScoutHealth) {
                        $scoutData = Invoke-RestMethod -Uri $SCOUT_REDIRECT_URL -TimeoutSec 2
                        $entries = @($scoutData.entries)
                        if (-not $entries -and $scoutData.redirections) { $entries = @($scoutData.redirections) }
                        $activas = @($entries | Where-Object { $_.enabled -eq $true }).Count
                        Write-Host ('[SCOUT] Rotas activas : ' + $activas + ' / ' + $entries.Count) -ForegroundColor White
                    } else {
                        Write-Host '[SCOUT] Backend offline' -ForegroundColor Red
                    }
                } catch {
                    Write-Host '[SCOUT] API indisponivel' -ForegroundColor Yellow
                }
                Write-Host "-------------------------------------------------" -ForegroundColor Gray
            }

            # ---------------------------------------------------
            # HUD DE REDE E NGROK
            # ---------------------------------------------------
            try {
                $response = Invoke-RestMethod -Uri "http://localhost:4040/api/tunnels" -TimeoutSec 2 -ErrorAction Stop
                $currentUrl = $response.tunnels[0].public_url

                if ($null -ne $currentUrl -and $currentUrl -ne $oldUrl) {
                    Write-Host "[ATUALIZANDO] Injetando nova URL no n8n..." -ForegroundColor Yellow
                    $env:NGROK_REMOTE_URL = $currentUrl
                    Set-EnvValue -Key "SCOUT_NGROK_TUNNEL_URL" -Value $currentUrl
                    Invoke-Compose -ComposeFile $COMPOSE_N8N -ComposeArgs @("up", "-d") | Out-Null
                    if ($script:UseScout) {
                        Invoke-Compose -ComposeFile $COMPOSE_SCOUT -ComposeArgs @("up", "-d") | Out-Null
                    }
                    $oldUrl = $currentUrl
                }
                Write-Host "[REDE] URL Publica : $oldUrl" -ForegroundColor Green
            } catch {
                Write-Host "[AVISO] Ngrok offline ou aguardando conexao..." -ForegroundColor Yellow
            }

            Write-Host "-------------------------------------------------" -ForegroundColor Gray

            # ---------------------------------------------------
            # HUD DO PORTEIRO E FILA DE ESPERA
            # ---------------------------------------------------
            try {
                if (Test-Path $PATH_BANCO_PORTEIRO) {
                    $jsonBanco = Get-Content $PATH_BANCO_PORTEIRO -Raw | ConvertFrom-Json

                    $vips = @($jsonBanco.visitantes | Where-Object { $_.status -eq 'aprovado' }).Count
                    Write-Host "[OK] IPs VIPs      : $vips permitidos" -ForegroundColor White

                    $pendentes = @($jsonBanco.visitantes | Where-Object { $_.status -eq 'pendente' })

                    if ($pendentes.Count -gt 0) {
                        Write-Host "`n[!] AGUARDANDO APROVACAO:" -ForegroundColor Yellow
                        foreach ($v in $pendentes) {
                            $horaInicio = [DateTime]::Parse($v.data_primeiro_acesso).ToLocalTime()
                            $tempoEspera = (Get-Date) - $horaInicio
                            $minutos = [math]::Floor($tempoEspera.TotalMinutes)
                            $segundos = $tempoEspera.Seconds
                            $tempoFormatado = "{0:00}m {1:00}s" -f $minutos, $segundos
                            Write-Host "  > IP: $($v.ip.PadRight(20)) | Espera: $tempoFormatado | Tentativas: $($v.tentativas)" -ForegroundColor DarkYellow
                        }
                    } else {
                        Write-Host "[INFO] Ninguem aguardando no momento." -ForegroundColor Gray
                    }
                }

                if (Test-Path $PATH_LOG_PORTEIRO) {
                    Write-Host ""
                    Write-Host "[LOG] Auditoria (Ultimos Eventos):" -ForegroundColor Gray
                    Get-Content $PATH_LOG_PORTEIRO -Tail 4 -Encoding UTF8 | ForEach-Object {
                        Write-Host "  $_" -ForegroundColor DarkGray
                    }
                }
            } catch {
                Write-Host "[ERRO] Fila: $($_.Exception.Message)" -ForegroundColor Red
            }

            Write-Host "=================================================" -ForegroundColor Cyan
            if ($script:UseScout) {
                Write-Host 'Pressione G para GUI Scout, Q para encerrar tudo (fechar GUI Scout tambem pode encerrar tudo)' -ForegroundColor Yellow
            } else {
                Write-Host 'Pressione a tecla Q para encerrar e desligar tudo.' -ForegroundColor Yellow
            }

            for ($i = 0; $i -lt 50; $i++) {
                if (Test-ShutdownRequest) {
                    $exitRequested = $true
                    break
                }
                if ([console]::KeyAvailable) {
                    $key = [System.Console]::ReadKey($true)
                    if ($key.Key -eq [System.ConsoleKey]::Q) {
                        $exitRequested = $true
                        break
                    }
                    if ($script:UseScout -and $key.Key -eq [System.ConsoleKey]::G) {
                        Start-ScoutGui
                        Start-Sleep -Milliseconds 400
                        break
                    }
                }
                Start-Sleep -Milliseconds 100
            }

            if ($exitRequested) { break }
        } catch {
            Write-Host "[ERRO HUD] $($_.Exception.Message)" -ForegroundColor Red
            Start-Sleep -Seconds 2
        }
    }
} catch {
    Write-Host ""
    Write-Host "[ERRO FATAL] $($_.Exception.Message)" -ForegroundColor Red -BackgroundColor Black
    Write-Host "A infraestrutura sera encerrada." -ForegroundColor Yellow
    Confirm-Interactive
} finally {
    Stop-Tudo
}
