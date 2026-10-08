# =======================================================
# CONFIGURAÇÃO DE CAMINHOS DINÂMICOS (100% Portátil)
# =======================================================
# -Stack n8n|llm executa so essa stack e sai, sem HUD e sem Stop-Tudo.
param(
    [ValidateSet("", "n8n", "llm")]
    [string]$Stack = "",
    [ValidateSet("iniciar", "parar", "reiniciar")]
    [string]$Acao = "iniciar"
)

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
$COMPOSE_LLM          = "$PSScriptRoot\llm\docker-compose.yml"
$PORTA_PORTEIRO       = 5677
$SCOUT_HEALTH_URL     = "http://127.0.0.1:8765/health"
$SCOUT_REDIRECT_URL   = "http://127.0.0.1:8765/redirections"
$PATH_SUPERVISOR_LOCK  = "$PSScriptRoot\.n8groker.supervisor.lock"
$PATH_SHUTDOWN_REQUEST = "$PSScriptRoot\.n8groker.shutdown.request"
$PATH_PID_PAINEL       = "$PSScriptRoot\.n8groker.control-plane.pid"
$PATH_PID_BORDA        = "$PSScriptRoot\.n8groker.control-plane-borda.pid"
$PATH_LOG_PAINEL       = "$PSScriptRoot\control_plane\streamlit.log"
$PATH_LOG_BORDA        = "$PSScriptRoot\control_plane\streamlit-borda.log"

$oldUrl = $null
$script:ReinicioPorUrl = $false
$script:PortaPainel = 8501
$script:PortaBorda = 8502
. (Join-Path $PSScriptRoot "scripts\teclado_janela.ps1")
. (Join-Path $PSScriptRoot "scripts\portas_compose.ps1")
. (Join-Path $PSScriptRoot "scripts\boot_scout.ps1")
. (Join-Path $PSScriptRoot "scripts\keeper_boot.ps1")
$script:UrlPainel = "http://localhost:8501"
$script:PidPainel = 0
$script:PainelAdiado = $false
$script:PainelPortaOcupada = $false
$script:OllamaIniciadoPeloScript = $false
$script:DockerIniciadoPeloScript = $false
$script:OllamaPid = 0
$script:UrlOllama = "http://localhost:11434"
$script:UseScout = $false
$script:UseLlm = $false
$script:ScoutPublicPort = 4050
$script:ScoutNoAr = $false
$script:ScoutMotivo = ""
$script:ComposeCommand = @()
$script:encerramentoPorFalha = $false
$script:acaoAvulsa = $false
if ($Stack) { $script:acaoAvulsa = $true }
$script:Pendencias = New-Object System.Collections.Generic.List[object]
$script:DockerInfoText = ""
$script:MarcadorPainel = "$PSScriptRoot\.n8groker.skip-control-plane-venv"

if (-not $Stack) {
    Clear-Host
    Write-Host "=================================================" -ForegroundColor Cyan
    Write-Host " INICIANDO A ARQUITETURA E FIREWALL " -ForegroundColor Cyan
    Write-Host "=================================================" -ForegroundColor Cyan
}

# =======================================================
# FUNÇÕES UTILITÁRIAS
# =======================================================
function Test-ConsoleInterativo {
    if (-not [Environment]::UserInteractive) {
        return $false
    }
    try {
        if ([Console]::IsInputRedirected) {
            return $false
        }
    } catch {
        return $false
    }
    return $true
}

function Confirm-Interactive {
    if (-not (Test-ConsoleInterativo)) {
        return
    }
    Write-Host "`nPressione ENTER para fechar..." -ForegroundColor Cyan
    try {
        Read-Host | Out-Null
    } catch {
        return
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

function Test-LlmConfigured {
    $needed = @(
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LITELLM_MASTER_KEY",
        "LANGFUSE_DB_PASSWORD",
        "ENCRYPTION_KEY",
        "N8N_CREDENTIALS_OVERWRITE_DATA"
    )
    foreach ($key in $needed) {
        $val = Get-EnvValue $key ""
        if (-not $val) { return $false }
        if ($val -like "__GENERATE_*") { return $false }
    }
    return $true
}

function Get-StacksBoot {
    # Vazio = so o nucleo. n8n e llm, separados por virgula, entram no boot.
    # Token desconhecido e avisado e ignorado. Duplicata sai.
    $bruto = Get-EnvValue "STACKS_BOOT" ""
    $conhecidos = @("n8n", "llm")
    $saida = @()
    foreach ($parte in ($bruto -split ",")) {
        $token = "$parte".Trim().ToLowerInvariant()
        if (-not $token) { continue }
        if ($conhecidos -notcontains $token) {
            Write-Host "[AVISO] STACKS_BOOT ignorou '$token'. Vale n8n ou llm." -ForegroundColor Yellow
            continue
        }
        if ($saida -contains $token) { continue }
        $saida += $token
    }
    Write-Output -NoEnumerate $saida
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
    Write-Host "[SCOUT] Pedido de shutdown em .n8groker.shutdown.request. Encerrando a stack." -ForegroundColor Yellow
    return $true
}

# Teclado e portas publicadas estao em scripts/teclado_janela.ps1 e scripts/portas_compose.ps1.

function Invoke-Compose {
    param(
        [Parameter(Mandatory = $true)][string]$ComposeFile,
        [Parameter(Mandatory = $true)][string[]]$ComposeArgs
    )
    # PowerShell 5.1 transforma stderr nativo em erro. Capturamos os dois fluxos
    # e, se o codigo nao for zero, mostramos a saida. Sucesso nao despeja o
    # progresso no HUD; falha de pull ou up nao pode sumir.
    if (-not $script:ComposeCommand -or @($script:ComposeCommand).Count -eq 0) {
        if (-not (Resolve-ComposeCommand)) {
            Write-Host "[ERRO] Nem docker-compose nem 'docker compose' estao disponiveis." -ForegroundColor Red
            return 1
        }
    }
    Set-PortasDoCompose
    $argv = @()
    foreach ($parte in @($script:ComposeCommand)) { $argv += $parte }
    $argv += @("-f", $ComposeFile, "--env-file", $PATH_ENV)
    foreach ($arg in $ComposeArgs) { $argv += $arg }
    $exe = $argv[0]
    $resto = @()
    for ($i = 1; $i -lt $argv.Count; $i++) { $resto += $argv[$i] }

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $saida = & $exe @resto 2>&1
        $codigo = $LASTEXITCODE
        if ($null -eq $codigo) { $codigo = 1 }
        if ($codigo -ne 0) {
            Write-Host "[ERRO] Compose falhou (codigo $codigo): $exe $($resto -join ' ')" -ForegroundColor Red
            foreach ($linha in @($saida)) {
                if ($linha -is [System.Management.Automation.ErrorRecord]) {
                    Write-Host $linha.ToString() -ForegroundColor Red
                } else {
                    Write-Host "$linha" -ForegroundColor Red
                }
            }
        }
        return $codigo
    } catch {
        Write-Host "[ERRO] Nao foi possivel executar o compose: $($_.Exception.Message)" -ForegroundColor Red
        return 1
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

function Update-N8nContainerIp {
    $dir = Join-Path $PSScriptRoot ".n8groker"
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $arquivo = Join-Path $dir "n8n-container-ip"
    $json = ""
    try {
        $json = & docker inspect -f "{{json .NetworkSettings.Networks}}" n8n_app 2>$null
    } catch {
        $json = ""
    }
    if (-not $json) { return }
    $textoJson = [string]$json
    $achados = [regex]::Matches($textoJson, '"IPAddress"\s*:\s*"([0-9a-fA-F:.]+)"')
    $ips = @()
    foreach ($m in $achados) {
        $ip = $m.Groups[1].Value
        if ($ip -and $ip -ne "0.0.0.0" -and ($ips -notcontains $ip)) {
            $ips += $ip
        }
    }
    if ($ips.Count -eq 0) { return }
    $texto = ($ips -join "`n") + "`n"
    $atual = ""
    if (Test-Path -LiteralPath $arquivo) {
        $atual = [System.IO.File]::ReadAllText($arquivo)
    }
    if ($atual -eq $texto) { return }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($arquivo, $texto, $utf8)
}

function New-SegredoArquivo {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return ([Convert]::ToBase64String($bytes)).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Protect-ArquivoUsuario {
    param([Parameter(Mandatory = $true)][string]$Caminho)
    if (-not (Get-Command icacls -ErrorAction SilentlyContinue)) { return }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & icacls $Caminho /inheritance:r /grant:r ("{0}:(R,W)" -f $env:USERNAME) | Out-Null
    $ErrorActionPreference = $prev
}

function Read-TokenArquivo {
    param([Parameter(Mandatory = $true)][string]$Caminho)
    if (-not (Test-Path -LiteralPath $Caminho)) { return "" }
    $texto = [System.IO.File]::ReadAllText($Caminho)
    if ($texto.Length -gt 0 -and $texto[0] -eq [char]0xFEFF) { $texto = $texto.Substring(1) }
    return $texto.Trim()
}

function Repair-MontagemDeArquivo {
    param(
        [Parameter(Mandatory = $true)][string]$Caminho,
        [Parameter(Mandatory = $true)][string]$Rotulo
    )
    if (-not (Test-Path -LiteralPath $Caminho)) { return }
    if (Test-Path -LiteralPath $Caminho -PathType Leaf) { return }
    if (-not (Test-Path -LiteralPath $Caminho -PathType Container)) {
        throw "$Rotulo em $Caminho nao e arquivo nem pasta. Remova esse caminho antes de subir."
    }
    $itens = @(Get-ChildItem -LiteralPath $Caminho -Force -ErrorAction Stop)
    if ($itens.Count -gt 0) {
        throw "$Rotulo e uma pasta com conteudo em $Caminho. Nada foi apagado. Mova ou esvazie essa pasta e rode o script de novo."
    }
    Write-Host "[REPARO] $Rotulo era uma pasta vazia. O Docker cria essa pasta quando o arquivo nao existe no compose. A pasta foi removida." -ForegroundColor Yellow
    Remove-Item -LiteralPath $Caminho -Force
}

function Write-BytesAleatorios {
    param(
        [Parameter(Mandatory = $true)][string]$Caminho,
        [int]$Tamanho = 32
    )
    $bytes = New-Object byte[] $Tamanho
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    [System.IO.File]::WriteAllBytes($Caminho, $bytes)
    Protect-ArquivoUsuario $Caminho
}

function Ensure-ArquivoBinario {
    param(
        [Parameter(Mandatory = $true)][string]$Caminho,
        [Parameter(Mandatory = $true)][string]$Rotulo
    )
    $pasta = Split-Path -Parent $Caminho
    if ($pasta -and -not (Test-Path -LiteralPath $pasta)) {
        New-Item -ItemType Directory -Path $pasta | Out-Null
    }
    Repair-MontagemDeArquivo -Caminho $Caminho -Rotulo $Rotulo
    $gravar = $true
    if (Test-Path -LiteralPath $Caminho -PathType Leaf) {
        $info = Get-Item -LiteralPath $Caminho
        if ($info.Length -gt 0) { $gravar = $false }
    }
    if (-not $gravar) { return }
    Write-BytesAleatorios -Caminho $Caminho
    Write-Host "[OK] $Rotulo gravado em arquivo." -ForegroundColor Green
}

function Ensure-PorteiroTokens {
    $dir = Join-Path $PSScriptRoot ".n8groker"
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $painel = Join-Path $dir "porteiro-painel.token"
    $n8nTok = Join-Path $dir "porteiro-n8n.token"
    $envTok = Join-Path $dir "porteiro-n8n.env"
    foreach ($arquivo in @($painel, $n8nTok, $envTok)) {
        Repair-MontagemDeArquivo -Caminho $arquivo -Rotulo (Split-Path -Leaf $arquivo)
    }
    foreach ($arquivo in @($painel, $n8nTok)) {
        $atual = Read-TokenArquivo $arquivo
        if ($atual.Length -lt 16) {
            $atual = New-SegredoArquivo
            [System.IO.File]::WriteAllText($arquivo, ($atual + "`n"), $utf8)
            Protect-ArquivoUsuario $arquivo
        }
    }
    $valorN8n = Read-TokenArquivo $n8nTok
    $linha = "PORTEIRO_N8N_TOKEN=" + $valorN8n + "`n"
    $gravado = ""
    if (Test-Path -LiteralPath $envTok) { $gravado = [System.IO.File]::ReadAllText($envTok) }
    if ($gravado -ne $linha) {
        [System.IO.File]::WriteAllText($envTok, $linha, $utf8)
        Protect-ArquivoUsuario $envTok
    }
}

function Start-Porteiro {
    Stop-Porteiro
    Ensure-PorteiroTokens
    Write-Host "[+] Iniciando Porteiro (Node.js)..." -ForegroundColor Yellow
    $proc = Start-Process "node" -ArgumentList "porteiro.js" `
        -WorkingDirectory $PATH_PORTEIRO -WindowStyle Hidden -PassThru
    if ($proc) {
        $proc.Id | Set-Content $PATH_PID_PORTEIRO
    }
}

function Test-LinhaEhPainel {
    param([string]$Linha)
    if ([string]::IsNullOrWhiteSpace($Linha)) { return $false }
    $l = $Linha.ToLowerInvariant()
    return ($l.Contains("streamlit") -and $l.Contains("control_plane") -and $l.Contains("app.py"))
}

function Get-LinhaDeComando {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    if ($ProcessId -le 0) { return "" }
    $cmdPath = "/proc/$ProcessId/cmdline"
    if (Test-Path -LiteralPath $cmdPath) {
        $bytes = [System.IO.File]::ReadAllBytes($cmdPath)
        $texto = [System.Text.Encoding]::UTF8.GetString($bytes)
        return (($texto -replace [char]0, " ").Trim())
    }
    if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
        try {
            $proc = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ProcessId) -ErrorAction Stop
            if ($proc -and $proc.CommandLine) { return [string]$proc.CommandLine }
        } catch {
            return ""
        }
    }
    return ""
}

function Get-PidOuvinte {
    param([Parameter(Mandatory = $true)][int]$Port)
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        foreach ($c in $conns) {
            if ($c.OwningProcess -gt 0) { return [int]$c.OwningProcess }
        }
    }
    if (Get-Command ss -ErrorAction SilentlyContinue) {
        $saida = & ss -ltnp ("sport = :{0}" -f $Port) 2>&1 | Out-String
        if ($saida -match "pid=(\d+)") { return [int]$Matches[1] }
    }
    if (Get-Command lsof -ErrorAction SilentlyContinue) {
        $saida = & lsof -nP ("-iTCP:{0}" -f $Port) -sTCP:LISTEN -t 2>$null
        foreach ($linha in @($saida)) {
            $id = 0
            if ([int]::TryParse(([string]$linha).Trim(), [ref]$id) -and $id -gt 0) { return $id }
        }
    }
    $porProc = Get-PidOuvinteProc -Port $Port
    if ($porProc -gt 0) { return $porProc }
    return 0
}

function Get-PidOuvinteProc {
    param([Parameter(Mandatory = $true)][int]$Port)
    if (-not (Test-Path -LiteralPath "/proc/net/tcp")) { return 0 }
    $portaHex = "{0:X4}" -f $Port
    $inode = ""
    foreach ($arquivo in @("/proc/net/tcp", "/proc/net/tcp6")) {
        if (-not (Test-Path -LiteralPath $arquivo)) { continue }
        foreach ($linha in @(Get-Content -LiteralPath $arquivo -ErrorAction SilentlyContinue)) {
            $partes = @($linha.Trim() -split '\s+')
            if ($partes.Count -lt 10) { continue }
            if ($partes[3] -ne "0A") { continue }
            $local = [string]$partes[1]
            $pedaco = $local.Split(":")
            if ($pedaco[-1] -ne $portaHex) { continue }
            $inode = [string]$partes[9]
            break
        }
        if ($inode) { break }
    }
    if (-not $inode) { return 0 }
    $marca = "socket:[$inode]"
    foreach ($dir in @([System.IO.Directory]::GetDirectories("/proc"))) {
        $nome = [System.IO.Path]::GetFileName($dir)
        $id = 0
        if (-not [int]::TryParse($nome, [ref]$id)) { continue }
        $fdDir = Join-Path $dir "fd"
        if (-not [System.IO.Directory]::Exists($fdDir)) { continue }
        foreach ($fd in @([System.IO.Directory]::GetFiles($fdDir))) {
            try {
                $item = Get-Item -LiteralPath $fd -Force -ErrorAction Stop
                $alvo = @($item.Target) -join " "
                if ($alvo -eq $marca) { return $id }
            } catch {
                continue
            }
        }
    }
    return 0
}

function Get-PidsFilhos {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $saida = @()
    $cimOk = $false
    if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
        try {
            $procs = @(Get-CimInstance Win32_Process -Filter ("ParentProcessId={0}" -f $ProcessId) -ErrorAction Stop)
            $cimOk = $true
            foreach ($p in $procs) {
                $id = 0
                if ([int]::TryParse(([string]$p.ProcessId), [ref]$id) -and $id -gt 0 -and $id -ne $ProcessId) {
                    $saida += $id
                }
            }
        } catch {
            $cimOk = $false
        }
    }
    if ($cimOk) { return $saida }
    # No PowerShell, ps e alias de Get-Process. O binario nativo so entra fora do Windows.
    $psExe = $null
    foreach ($candidato in @("/bin/ps", "/usr/bin/ps")) {
        if (Test-Path -LiteralPath $candidato) { $psExe = $candidato; break }
    }
    if (-not $psExe) { return $saida }
    $raw = & $psExe -o pid= --ppid $ProcessId 2>$null
    foreach ($linha in @($raw)) {
        $id = 0
        if ([int]::TryParse(([string]$linha).Trim(), [ref]$id) -and $id -gt 0 -and $id -ne $ProcessId) {
            $saida += $id
        }
    }
    return $saida
}

function Test-ArvoreEhPainel {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [int]$Profundidade = 0
    )
    if ($ProcessId -le 0 -or $Profundidade -gt 6) { return $false }
    if (Test-LinhaEhPainel (Get-LinhaDeComando -ProcessId $ProcessId)) { return $true }
    foreach ($filho in @(Get-PidsFilhos -ProcessId $ProcessId)) {
        if (Test-ArvoreEhPainel -ProcessId $filho -Profundidade ($Profundidade + 1)) { return $true }
    }
    return $false
}

function Stop-ArvoreProcesso {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    if ($ProcessId -le 0) { return }
    $taskkill = $null
    if (Get-Command taskkill.exe -ErrorAction SilentlyContinue) {
        $taskkill = (Get-Command taskkill.exe).Source
    } elseif (Get-Command taskkill -ErrorAction SilentlyContinue) {
        $taskkill = (Get-Command taskkill).Source
    }
    if ($taskkill) {
        & $taskkill /PID $ProcessId /T /F | Out-Null
        return
    }
    foreach ($filho in @(Get-PidsFilhos -ProcessId $ProcessId)) {
        Stop-ArvoreProcesso -ProcessId $filho
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Test-PainelHttp {
    try {
        $req = [System.Net.WebRequest]::Create(("http://127.0.0.1:{0}/_stcore/health" -f $script:PortaPainel))
        $req.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy()
        $req.Timeout = 2000
        $resp = $req.GetResponse()
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
        $body = $reader.ReadToEnd()
        $reader.Close()
        $resp.Close()
        return ("$body" -match "ok")
    } catch {
        return $false
    }
}

function Write-StatusPainel {
    $url = $script:UrlPainel
    if (Test-PainelHttp) {
        Write-Host "[PAINEL] Control Plane online em $url" -ForegroundColor Green
        return
    }
    if ($script:PainelAdiado) {
        Write-Host "[PAINEL] Control Plane nao iniciado: o venv foi recusado. Endereco previsto: $url" -ForegroundColor Yellow
        return
    }
    if ($script:PainelPortaOcupada) {
        Write-Host "[PAINEL] Porta $($script:PortaPainel) esta com outro programa. O painel nao subiu e esse processo nao foi encerrado. Endereco: $url" -ForegroundColor Yellow
        return
    }
    if ($script:PidPainel -gt 0) {
        Write-Host "[PAINEL] Control Plane subindo em $url (log: control_plane\streamlit.log)" -ForegroundColor Yellow
        return
    }
    Write-Host "[PAINEL] Control Plane offline. Endereco: $url" -ForegroundColor Yellow
}

function Start-ControlPlane {
    $py = Join-Path $PSScriptRoot "control_plane\.venv\Scripts\python.exe"
    $app = Join-Path $PSScriptRoot "control_plane\app.py"
    if (-not (Test-Path -LiteralPath $py)) {
        if (Test-Path -LiteralPath $script:MarcadorPainel) {
            $script:PainelAdiado = $true
            Write-Host "[AVISO] Control Plane nao sera iniciado: a criacao do venv foi recusada. Apague .n8groker.skip-control-plane-venv para perguntar de novo." -ForegroundColor Yellow
            return
        }
        Write-Host "[AVISO] Control Plane nao sera iniciado: falta control_plane\.venv\Scripts\python.exe." -ForegroundColor Yellow
        return
    }
    $ouv = Get-PidOuvinte -Port $script:PortaPainel
    if ($ouv -gt 0) {
        $linha = Get-LinhaDeComando -ProcessId $ouv
        if (Test-LinhaEhPainel $linha) {
            $script:PidPainel = $ouv
            $script:PainelPortaOcupada = $false
            Set-Content -LiteralPath $PATH_PID_PAINEL -Value $ouv -Encoding ASCII
            Write-Host "[OK] Control Plane ja escuta em $($script:UrlPainel) (PID $ouv). Nao abri outro." -ForegroundColor Green
            Start-PainelBorda
            return
        }
        $script:PainelPortaOcupada = $true
        $script:PidPainel = 0
        Write-Host "[AVISO] A porta $($script:PortaPainel) esta em uso pelo PID $ouv, que nao e este Control Plane. Nao encerrei esse processo e nao subi outro painel." -ForegroundColor Yellow
        return
    }
    if (-not (Test-Path -LiteralPath $app)) {
        Write-Host "[AVISO] Control Plane nao sera iniciado: control_plane\app.py nao existe." -ForegroundColor Yellow
        return
    }
    $dirLog = Split-Path -Parent $PATH_LOG_PAINEL
    if (-not (Test-Path -LiteralPath $dirLog)) {
        New-Item -ItemType Directory -Path $dirLog -Force | Out-Null
    }
    $cmdExe = $env:ComSpec
    if (-not $cmdExe -and $env:SystemRoot) { $cmdExe = Join-Path $env:SystemRoot "System32\cmd.exe" }
    if (-not $cmdExe -or -not (Test-Path -LiteralPath $cmdExe)) {
        Write-Host "[AVISO] Nao achei o cmd.exe para subir o Control Plane em segundo plano." -ForegroundColor Yellow
        return
    }
    # cmd /s /c tira so as aspas externas. O python, o app.py e o log ficam citados.
    # A janela fica oculta. O PID guardado e o do cmd; taskkill /T encerra o python filho.
    $inner = 'set PANEL_MODE=console&& "' + $py + '" -m streamlit run "' + $app + '" --server.headless true --server.address 127.0.0.1 --server.port ' + $script:PortaPainel + ' --browser.gatherUsageStats false > "' + $PATH_LOG_PAINEL + '" 2>&1'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $cmdExe
    $psi.Arguments = '/d /s /c "' + $inner + '"'
    $psi.WorkingDirectory = $PSScriptRoot
    $psi.UseShellExecute = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    try {
        $iniciou = $proc.Start()
    } catch {
        Write-Host "[AVISO] Nao consegui iniciar o Control Plane: $($_.Exception.Message)" -ForegroundColor Yellow
        return
    }
    if (-not $iniciou) {
        Write-Host "[AVISO] O Control Plane nao devolveu processo. Veja control_plane\streamlit.log." -ForegroundColor Yellow
        return
    }
    $script:PidPainel = [int]$proc.Id
    $script:PainelPortaOcupada = $false
    Set-Content -LiteralPath $PATH_PID_PAINEL -Value $script:PidPainel -Encoding ASCII
    Write-Host "[OK] Control Plane em segundo plano: $($script:UrlPainel) (log: control_plane\streamlit.log)" -ForegroundColor Green
    Start-PainelBorda
}

function Stop-ControlPlane {
    $alvo = 0
    if ($script:PidPainel -gt 0) {
        $alvo = [int]$script:PidPainel
    } elseif (Test-Path -LiteralPath $PATH_PID_PAINEL) {
        $texto = Get-Content -LiteralPath $PATH_PID_PAINEL -ErrorAction SilentlyContinue | Select-Object -First 1
        $parsed = 0
        if ([int]::TryParse(([string]$texto).Trim(), [ref]$parsed)) { $alvo = $parsed }
    }
    if ($alvo -le 0) {
        Write-Host "    Control Plane nao estava em execucao por este script." -ForegroundColor Gray
        return
    }
    $vivo = Get-Process -Id $alvo -ErrorAction SilentlyContinue
    if (-not $vivo) {
        Write-Host "    Control Plane PID $alvo ja nao esta em execucao." -ForegroundColor Gray
        $script:PidPainel = 0
        if (Test-Path -LiteralPath $PATH_PID_PAINEL) {
            Remove-Item -LiteralPath $PATH_PID_PAINEL -Force -ErrorAction SilentlyContinue
        }
        return
    }
    # O PID pode ser o cmd que lancou o painel ou o python que escuta a porta.
    # So encerra se a linha de comando dessa arvore for deste Streamlit.
    if (-not (Test-ArvoreEhPainel -ProcessId $alvo)) {
        Write-Host "[AVISO] PID $alvo nao e o Control Plane deste repositorio. Nao encerrei esse processo." -ForegroundColor Yellow
        return
    }
    Write-Host "    Encerrando Control Plane PID $alvo e os processos filhos." -ForegroundColor Yellow
    Stop-ArvoreProcesso -ProcessId $alvo
    $script:PidPainel = 0
    if (Test-Path -LiteralPath $PATH_PID_PAINEL) {
        Remove-Item -LiteralPath $PATH_PID_PAINEL -Force -ErrorAction SilentlyContinue
    }
    Stop-PainelBorda
}

function Start-PainelBorda {
    $py = Join-Path $PSScriptRoot "control_plane\.venv\Scripts\python.exe"
    $app = Join-Path $PSScriptRoot "control_plane\app.py"
    if (-not (Test-Path -LiteralPath $py) -or -not (Test-Path -LiteralPath $app)) { return }
    if (Test-Path -LiteralPath $script:MarcadorPainel) { return }
    $ouv = Get-PidOuvinte -Port $script:PortaBorda
    if ($ouv -gt 0) {
        $linha = Get-LinhaDeComando -ProcessId $ouv
        if (Test-LinhaEhPainel $linha) {
            $script:PidBorda = $ouv
            Set-Content -LiteralPath $PATH_PID_BORDA -Value $ouv -Encoding ASCII
            Write-Host "[OK] Painel de borda ja escuta em 127.0.0.1:$($script:PortaBorda) (PID $ouv)." -ForegroundColor Green
            return
        }
        Write-Host "[AVISO] A porta $($script:PortaBorda) esta em uso pelo PID $ouv, que nao e este painel. Nao encerrei esse processo." -ForegroundColor Yellow
        return
    }
    $dirLog = Split-Path -Parent $PATH_LOG_BORDA
    if (-not (Test-Path -LiteralPath $dirLog)) {
        New-Item -ItemType Directory -Path $dirLog -Force | Out-Null
    }
    $cmdExe = $env:ComSpec
    if (-not $cmdExe -and $env:SystemRoot) { $cmdExe = Join-Path $env:SystemRoot "System32\cmd.exe" }
    if (-not $cmdExe -or -not (Test-Path -LiteralPath $cmdExe)) { return }
    # A borda fica atras do Porteiro, no caminho /painel. CORS e XSRF do Streamlit
    # quebram com o HTTPS do ngrok na frente de um HTTP em loopback; o portao e o HMAC.
    $inner = 'set PANEL_MODE=edge&& "' + $py + '" -m streamlit run "' + $app + '" --server.headless true --server.address 127.0.0.1 --server.port ' + $script:PortaBorda + ' --server.baseUrlPath painel --server.enableCORS false --server.enableXsrfProtection false --browser.gatherUsageStats false > "' + $PATH_LOG_BORDA + '" 2>&1'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $cmdExe
    $psi.Arguments = '/d /s /c "' + $inner + '"'
    $psi.WorkingDirectory = $PSScriptRoot
    $psi.UseShellExecute = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    try {
        $iniciou = $proc.Start()
    } catch {
        Write-Host "[AVISO] Nao consegui iniciar o painel de borda: $($_.Exception.Message)" -ForegroundColor Yellow
        return
    }
    if (-not $iniciou) { return }
    $script:PidBorda = [int]$proc.Id
    Set-Content -LiteralPath $PATH_PID_BORDA -Value $script:PidBorda -Encoding ASCII
    Write-Host "[OK] Painel de borda em 127.0.0.1:$($script:PortaBorda)/painel (log: control_plane\streamlit-borda.log)" -ForegroundColor Green
}

function Stop-PainelBorda {
    $alvo = 0
    if ($script:PidBorda -gt 0) {
        $alvo = [int]$script:PidBorda
    } elseif (Test-Path -LiteralPath $PATH_PID_BORDA) {
        $texto = Get-Content -LiteralPath $PATH_PID_BORDA -ErrorAction SilentlyContinue | Select-Object -First 1
        $parsed = 0
        if ([int]::TryParse(([string]$texto).Trim(), [ref]$parsed)) { $alvo = $parsed }
    }
    if ($alvo -le 0) { return }
    $vivo = Get-Process -Id $alvo -ErrorAction SilentlyContinue
    if (-not $vivo) {
        $script:PidBorda = 0
        if (Test-Path -LiteralPath $PATH_PID_BORDA) {
            Remove-Item -LiteralPath $PATH_PID_BORDA -Force -ErrorAction SilentlyContinue
        }
        return
    }
    if (-not (Test-ArvoreEhPainel -ProcessId $alvo)) {
        Write-Host "[AVISO] PID $alvo nao e o painel de borda deste repositorio. Nao encerrei esse processo." -ForegroundColor Yellow
        return
    }
    Write-Host "    Encerrando painel de borda PID $alvo (porta $($script:PortaBorda))." -ForegroundColor Yellow
    Stop-ArvoreProcesso -ProcessId $alvo
    $script:PidBorda = 0
    if (Test-Path -LiteralPath $PATH_PID_BORDA) {
        Remove-Item -LiteralPath $PATH_PID_BORDA -Force -ErrorAction SilentlyContinue
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

function Ensure-SessaoChave {
    $pasta = Join-Path $PSScriptRoot ".n8groker"
    if (-not (Test-Path -LiteralPath $pasta)) {
        New-Item -ItemType Directory -Path $pasta | Out-Null
    }
    $chave = Join-Path $pasta "sessao.key"
    $geracao = Join-Path $pasta "sessoes-geracao.json"
    Repair-MontagemDeArquivo -Caminho $chave -Rotulo "sessao.key"
    Repair-MontagemDeArquivo -Caminho $geracao -Rotulo "sessoes-geracao.json"
    foreach ($arquivo in @($chave, $geracao)) {
        if ((Test-Path -LiteralPath $arquivo -PathType Leaf) -and ((Get-Item -LiteralPath $arquivo).Length -eq 0)) {
            Remove-Item -LiteralPath $arquivo -Force
        }
    }
    $py = Join-Path $PSScriptRoot "control_plane\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) {
        throw "Falta control_plane\.venv para gravar a chave de sessao."
    }
    $scout = Join-Path $PSScriptRoot "Scout_OSINT_Docker"
    $env:PYTHONPATH = $scout
    & $py -c "import sys; from scout.core.sessao_cookie import garantir; garantir(sys.argv[1])" $PSScriptRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel preparar .n8groker\sessao.key."
    }
    Protect-ArquivoUsuario $chave
    if (Test-Path -LiteralPath $geracao) { Protect-ArquivoUsuario $geracao }
}

function Ensure-ChaveUsuario {
    $pasta = Join-Path $PSScriptRoot ".n8groker"
    if (-not (Test-Path -LiteralPath $pasta)) {
        New-Item -ItemType Directory -Path $pasta | Out-Null
    }
    $chave = Join-Path $pasta "usuario.key"
    $pub = Join-Path $pasta "usuario.pub"
    Repair-MontagemDeArquivo -Caminho $pub -Rotulo "usuario.pub"
    if ((Test-Path -LiteralPath $pub -PathType Leaf) -and ((Get-Item -LiteralPath $pub).Length -eq 0)) {
        Remove-Item -LiteralPath $pub -Force
    }
    # 32 bytes aleatorios servem de semente Ed25519. Arquivo com conteudo nao e reescrito.
    Ensure-ArquivoBinario -Caminho $chave -Rotulo "usuario.key"
    $py = Join-Path $PSScriptRoot "control_plane\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) { return }
    $scout = Join-Path $PSScriptRoot "Scout_OSINT_Docker"
    $anterior = $env:PYTHONPATH
    $env:PYTHONPATH = $PSScriptRoot + [IO.Path]::PathSeparator + $scout
    & $py -c "import sys; from control_plane.user_token import garantir; garantir(sys.argv[1])" $PSScriptRoot
    $codigo = $LASTEXITCODE
    if ($null -eq $anterior) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $anterior
    }
    if ($codigo -ne 0) {
        throw "Nao foi possivel preparar .n8groker\usuario.pub."
    }
    if (Test-Path -LiteralPath $pub) { Protect-ArquivoUsuario $pub }
}

function Ensure-AuditArquivo {
    $pasta = Join-Path $PSScriptRoot ".n8groker"
    if (-not (Test-Path -LiteralPath $pasta)) {
        New-Item -ItemType Directory -Path $pasta | Out-Null
    }
    $arquivo = Join-Path $pasta "audit.jsonl"
    Repair-MontagemDeArquivo -Caminho $arquivo -Rotulo "audit.jsonl"
    if (-not (Test-Path -LiteralPath $arquivo)) {
        [System.IO.File]::WriteAllText($arquivo, "")
        Protect-ArquivoUsuario $arquivo
    }
}

function Ensure-ChaveHmac {
    $arquivo = Join-Path $PSScriptRoot ".n8groker\porteiro-hmac.key"
    Ensure-ArquivoBinario -Caminho $arquivo -Rotulo "porteiro-hmac.key"
}

function Ensure-ArquivosMontados {
    Ensure-PorteiroTokens
    Ensure-ChaveHmac
    Ensure-AuditArquivo
    Ensure-TrilhaPasta
}

function Assert-ArquivoNoContainer {
    param(
        [Parameter(Mandatory = $true)][string]$Container,
        [Parameter(Mandatory = $true)][string]$Caminho,
        [switch]$Conteudo
    )
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "docker nao esta no PATH. Nao conferi $Caminho em $Container."
    }
    $expr = "test -f '$Caminho'"
    if ($Conteudo) { $expr = $expr + " -a -s '$Caminho'" }
    & docker exec $Container sh -c $expr
    if ($LASTEXITCODE -ne 0) {
        throw "A montagem $Caminho em $Container nao e um arquivo legivel. O boot parou para nao abrir o painel sem o cabecalho do Porteiro."
    }
}

function Assert-MontagensDoScout {
    Assert-ArquivoNoContainer -Container "scout-backend" -Caminho "/run/porteiro-hmac.key" -Conteudo
    Assert-ArquivoNoContainer -Container "scout-backend" -Caminho "/run/porteiro-painel.token" -Conteudo
    Assert-ArquivoNoContainer -Container "scout-backend" -Caminho "/run/sessao.key" -Conteudo
    Assert-ArquivoNoContainer -Container "scout-backend" -Caminho "/run/sessoes-geracao.json" -Conteudo
    Assert-ArquivoNoContainer -Container "scout-backend" -Caminho "/run/audit.jsonl"
}

function Ensure-TrilhaPasta {
    $base = Join-Path $PSScriptRoot ".n8groker"
    if (-not (Test-Path -LiteralPath $base)) {
        New-Item -ItemType Directory -Path $base | Out-Null
    }
    $pasta = Join-Path $base "trilha"
    if (Test-Path -LiteralPath $pasta -PathType Leaf) {
        throw "A pasta trilha virou arquivo. Remova antes de subir o Scout."
    }
    if (-not (Test-Path -LiteralPath $pasta)) {
        New-Item -ItemType Directory -Path $pasta | Out-Null
    }
}

function Start-Scout {
    $script:ScoutNoAr = $false
    try {
        if (-not (Test-Path $COMPOSE_SCOUT)) {
            throw "Scout_OSINT_Docker/docker-compose.yml nao encontrado."
        }
        Ensure-ArquivosMontados
        Ensure-SessaoChave
        Write-Host "[+] Iniciando Scout Gate (Docker)..." -ForegroundColor Yellow
        $code = Invoke-Compose -ComposeFile $COMPOSE_SCOUT -ComposeArgs @("up", "-d", "--build")
        $saude = $false
        if ($code -eq 0) {
            $saude = [bool](Wait-ScoutHealth)
            if ($saude) {
                Assert-MontagensDoScout
            }
        }
        $resultado = Resultado-SubidaScout -CodigoUp $code -SaudeOk $saude
    } catch {
        $resultado = Resultado-SubidaScout -Erro $_.Exception.Message
    }
    if ($resultado.NoAr) {
        $script:ScoutNoAr = $true
        $script:ScoutMotivo = ""
        return
    }
    $script:ScoutNoAr = $false
    $script:ScoutMotivo = [string]$resultado.Motivo
    Write-Host "[AVISO] $($script:ScoutMotivo). O Control Plane continua no ar." -ForegroundColor Yellow
}

function Write-EnvLinesNoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyCollection()][string[]]$Lines
    )
    # UTF8Encoding($false) nao grava BOM no Windows PowerShell 5.1 nem no PowerShell 7.
    # Set-Content -Encoding UTF8 no 5.1 grava BOM e o compose pode ler a primeira chave errada.
    $newline = "`n"
    if (Test-Path -LiteralPath $Path) {
        $existente = [System.IO.File]::ReadAllText($Path)
        if ($existente.Contains("`r`n")) {
            $newline = "`r`n"
        }
    }
    if ($null -eq $Lines) { $Lines = @() }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $corpo = [string]::Join($newline, $Lines)
    if ($corpo.Length -gt 0 -and -not $corpo.EndsWith($newline)) {
        $corpo = $corpo + $newline
    }
    [System.IO.File]::WriteAllText($Path, $corpo, $utf8)
}

function Repair-EnvBom {
    if (-not (Test-Path -LiteralPath $PATH_ENV)) { return }
    $bytes = [System.IO.File]::ReadAllBytes($PATH_ENV)
    if ($bytes.Length -lt 3) { return }
    if ($bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) { return }
    $resto = New-Object byte[] ($bytes.Length - 3)
    [System.Buffer]::BlockCopy($bytes, 3, $resto, 0, $resto.Length)
    [System.IO.File]::WriteAllBytes($PATH_ENV, $resto)
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Value
    )
    if (-not (Test-Path -LiteralPath $PATH_ENV)) { return }
    Repair-EnvBom
    $lines = @(Get-Content -LiteralPath $PATH_ENV -Encoding UTF8)
    $found = $false
    $limpas = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if ($null -eq $line) { continue }
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=") {
            $found = $true
            # Concatena: valor com $ nao pode ser expandido como variavel do PowerShell.
            [void]$limpas.Add($Key + '=' + $Value)
        } else {
            [void]$limpas.Add([string]$line)
        }
    }
    if (-not $found) {
        [void]$limpas.Add($Key + '=' + $Value)
    }
    Write-EnvLinesNoBom -Path $PATH_ENV -Lines $limpas.ToArray()
}

function Sync-ScoutPorteiroRoute {
    $publicPedida = [int](Get-EnvValue "SCOUT_PUBLIC_PORT" "4050")
    $publicPort = Resolve-PortaSemHud -Pedida $publicPedida -Segura 4050
    if ($publicPort -ne $publicPedida) {
        Write-Host "[AVISO] Porta de escuta 8501 e o HUD-admin. O boot nao grava essa escuta. A rota segue $publicPort." -ForegroundColor Yellow
    }
    $upstreamHost = Get-EnvValue "SCOUT_UPSTREAM_HOST" "host.docker.internal"
    $upstreamPedida = [int](Get-EnvValue "SCOUT_UPSTREAM_PORT" "5677")
    $upstreamAtual = 0
    try {
        $rotas = Invoke-RestMethod -Uri "http://127.0.0.1:8765/redirections" -TimeoutSec 5
        foreach ($entry in @($rotas.entries)) {
            if ($entry.id -eq "porteiro-manual") {
                $upstreamAtual = [int]$entry.upstream_port
            }
        }
    } catch {
        $upstreamAtual = 0
    }
    $upstreamPort = Resolve-PortaSemHud -Pedida $upstreamPedida -Segura 5677 -Atual $upstreamAtual
    if ($upstreamPort -ne $upstreamPedida) {
        Write-Host "[AVISO] SCOUT_UPSTREAM_PORT 8501 e o HUD-admin. O boot nao grava esse destino. A rota segue $upstreamPort." -ForegroundColor Yellow
    }
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

function Open-PainelScout {
    if (-not (Test-PainelHttp)) {
        Write-Host "[PAINEL] O Control Plane nao esta no ar. Vou tentar inicia-lo." -ForegroundColor Yellow
        Start-ControlPlane
        $limite = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $limite) {
            if (Test-PainelHttp) { break }
            Start-Sleep -Milliseconds 400
        }
    }
    if (-not (Test-PainelHttp)) {
        Write-Host "[PAINEL] Nao consegui abrir o painel em $($script:UrlPainel). Veja control_plane\streamlit.log." -ForegroundColor Yellow
        return
    }
    $url = $script:UrlPainel + "/?aba=scout"
    Write-Host "[PAINEL] Abrindo $url no navegador." -ForegroundColor Green
    Start-Process $url
}

function Get-NgrokTunnelTarget {
    if ($script:UseScout) {
        $pedida = [int](Get-EnvValue "SCOUT_PUBLIC_PORT" "4050")
        $porta = Resolve-PortaSemHud -Pedida $pedida -Segura 4050
        if ($porta -ne $pedida) {
            Write-Host "[AVISO] SCOUT_PUBLIC_PORT 8501 e o HUD-admin. O ngrok nao aponta para la. O alvo segue $porta." -ForegroundColor Yellow
        }
        return "$porta"
    }
    return "$PORTA_PORTEIRO"
}

function Wait-NgrokPublicUrl {
    param([int]$TimeoutSec = 45)
    $limite = (Get-Date).AddSeconds($TimeoutSec)
    $ultimoErro = ""
    while ((Get-Date) -lt $limite) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2 -ErrorAction Stop
            $lista = @($response.tunnels)
            if ($lista.Count -gt 0 -and $lista[0].public_url) {
                return [string]$lista[0].public_url
            }
            $ultimoErro = "A API respondeu sem public_url."
        } catch {
            $ultimoErro = $_.Exception.Message
        }
        Write-Host "    Aguardando a URL publica do ngrok..." -ForegroundColor Gray
        Start-Sleep -Seconds 2
    }
    if ($ultimoErro) {
        Write-Host "    Ultima resposta do ngrok: $ultimoErro" -ForegroundColor Yellow
    }
    return $null
}

function Write-NgrokUrlNoAmbiente {
    param([Parameter(Mandatory = $true)][string]$Url)
    $env:NGROK_REMOTE_URL = $Url
    $gravada = Get-EnvValue "SCOUT_NGROK_TUNNEL_URL" ""
    if ($gravada -ne $Url) {
        Set-EnvValue -Key "SCOUT_NGROK_TUNNEL_URL" -Value $Url
        Write-Host "[OK] URL publica gravada no .env antes de subir o n8n e o Scout." -ForegroundColor Green
    } else {
        Write-Host "[OK] URL publica do ngrok ja estava no .env." -ForegroundColor Green
    }
}

function Get-CorStatusContainer {
    param([string]$Status)
    if ([string]::IsNullOrWhiteSpace($Status)) { return "Red" }
    if ($Status -match "health: starting") { return "Yellow" }
    if ($Status -match "Restarting") { return "Yellow" }
    if ($Status -match "Up") { return "Green" }
    return "Red"
}

function Get-TextoStatusContainer {
    param([string]$Status)
    if ([string]::IsNullOrWhiteSpace($Status)) { return "Nao Encontrado/Criado" }
    if ($script:ReinicioPorUrl -and $Status -match "health: starting") {
        return ($Status + " (reinicio esperado, nao e falha)")
    }
    return $Status
}

$script:desligamentoExecutado = $false
function Stop-Tudo {
    # -Stack n8n|llm nao pode cair no desligamento do nucleo.
    if ($script:acaoAvulsa) { return }
    if ($script:desligamentoExecutado) { return }
    $script:desligamentoExecutado = $true
    Stop-KeeperSeVivo -Raiz $PSScriptRoot

    # Falha de subida nao pode ser apagada antes da pessoa ler a causa.
    if (-not $script:encerramentoPorFalha) {
        Clear-Host
    }
    Write-Host "=================================================" -ForegroundColor Red
    Write-Host " INICIANDO DESLIGAMENTO SEGURO DA INFRAESTRUTURA " -ForegroundColor Red
    Write-Host "=================================================" -ForegroundColor Red

    $step = 1
    $total = 6
    if ($script:UseScout) { $total += 1 }
    if ($script:UseLlm) { $total += 1 }

    $podeCompose = Resolve-ComposeCommand
    $falhaDown = $false
    if (-not $podeCompose) {
        Write-Host "[AVISO] docker compose indisponivel. Containers nao foram derrubados por este script." -ForegroundColor Yellow
    }

    Write-Host "[$step/$total] Derrubando container do Ngrok..." -ForegroundColor Yellow
    if ($podeCompose) {
        $codeDown = Invoke-Compose -ComposeFile $COMPOSE_NGROK -ComposeArgs @("down")
        if ($codeDown -ne 0) { $falhaDown = $true }
    }
    $step++

    Write-Host "[$step/$total] Derrubando container do n8n..." -ForegroundColor Yellow
    if ($podeCompose) {
        $codeDown = Invoke-Compose -ComposeFile $COMPOSE_N8N -ComposeArgs @("down")
        if ($codeDown -ne 0) { $falhaDown = $true }
    }
    $step++

    if ($script:UseLlm) {
        Write-Host "[$step/$total] Derrubando Langfuse e LiteLLM..." -ForegroundColor Yellow
        if ($podeCompose) {
            $codeDown = Invoke-Compose -ComposeFile $COMPOSE_LLM -ComposeArgs @("down")
            if ($codeDown -ne 0) { $falhaDown = $true }
        }
        $step++
    }

    if ($script:UseScout) {
        Write-Host "[$step/$total] Derrubando Scout Gate..." -ForegroundColor Yellow
        if ($podeCompose) { Stop-Scout }
        $step++
    }

    Write-Host "[$step/$total] Encerrando o Porteiro (Node.js)..." -ForegroundColor Yellow
    Stop-Porteiro
    $step++

    Write-Host "[$step/$total] Encerrando o Control Plane (porta 8501)..." -ForegroundColor Yellow
    Stop-ControlPlane
    $step++

    Write-Host "[$step/$total] Ollama local (so se este script abriu)..." -ForegroundColor Yellow
    Stop-OllamaLocal
    $step++

    Write-Host "[$step/$total] Docker Desktop (so se este script abriu, depois do compose down)..." -ForegroundColor Yellow
    Stop-DockerDesktopIniciado

    Unregister-Supervisor

    if ($script:encerramentoPorFalha) {
        Write-Host ""
        Write-Host "A infraestrutura foi encerrada depois de uma falha." -ForegroundColor Red
        Write-Host "A causa esta acima. Isto nao foi um desligamento limpo." -ForegroundColor Red
    } elseif ($falhaDown) {
        Write-Host ""
        Write-Host "Desligamento pedido, mas algum compose down falhou. A saida esta acima." -ForegroundColor Yellow
    } else {
        Write-Host "`nDesligamento concluido. O que este script abriu foi encerrado. Ollama ou Docker Desktop que ja estavam no ar foram mantidos." -ForegroundColor Green
    }
    Start-Sleep -Seconds 1
    Confirm-Interactive
}

trap {
    $script:encerramentoPorFalha = $true
    Write-Host ""
    Write-Host "[ERRO FATAL] $($_.Exception.Message)" -ForegroundColor Red -BackgroundColor Black
    Stop-Tudo
    break
}

# =======================================================
# 0. PREPARO DA MAQUINA (IDEMPOTENTE)
# =======================================================
# Varredura do que a subida chama de verdade:
#   node porteiro.js, docker, docker-compose ou "docker compose",
#   Python real para scripts\init_env.py e para os venv.
# Git nao e invocado. npm nao entra: o Porteiro so usa http, fs e path,
# e nao ha package.json. O ngrok do projeto e o container ngrok/ngrok,
# nao um binario no host. O alias python da Microsoft Store (WindowsApps)
# passa em Get-Command e falha na execucao; por isso o Python e executado.

function Add-Pendencia {
    param(
        [Parameter(Mandatory = $true)][string]$Nome,
        [Parameter(Mandatory = $true)][string]$Motivo
    )
    foreach ($p in $script:Pendencias) {
        if ($p.Nome -eq $Nome) { return }
    }
    $script:Pendencias.Add([pscustomobject]@{ Nome = $Nome; Motivo = $Motivo }) | Out-Null
}

function Stop-SePendencias {
    if ($script:Pendencias.Count -eq 0) { return }
    Write-Host ""
    Write-Host "[PARADO] Nada foi iniciado. Ainda falta:" -ForegroundColor Red
    foreach ($p in $script:Pendencias) {
        Write-Host ("  - {0}: {1}" -f $p.Nome, $p.Motivo) -ForegroundColor Yellow
    }
    Write-Host "Rode o script de novo depois de resolver. O que ja estiver pronto nao e refeito." -ForegroundColor Gray
    # O motor ou o Ollama podem ter sido abertos antes desta saida. So fecha o que este script abriu.
    Stop-OllamaLocal
    Stop-DockerDesktopIniciado
    Confirm-Interactive
    Exit 1
}

function Confirm-Sim {
    param([Parameter(Mandatory = $true)][string]$Pergunta)
    Write-Host ""
    Write-Host $Pergunta -ForegroundColor Cyan
    if (-not [Environment]::UserInteractive) {
        Write-Host "    Sessao nao interativa: tratado como nao." -ForegroundColor Yellow
        return $false
    }
    $resposta = Read-Host "    S/N"
    if (-not $resposta) { return $false }
    return [bool]($resposta.Trim() -match '^(s|sim|y|yes)$')
}

function Read-ValorOculto {
    param([Parameter(Mandatory = $true)][string]$Prompt)
    try {
        $secure = Read-Host -Prompt $Prompt -AsSecureString
    } catch {
        Write-Host "    Nao foi possivel ler o valor sem eco." -ForegroundColor Yellow
        return ""
    }
    if (-not $secure -or $secure.Length -eq 0) { return "" }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Update-SessionPath {
    param([string[]]$PreferFirst)
    $chunks = @()
    foreach ($d in @($PreferFirst)) { if ($d) { $chunks += $d } }
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($machine) { $chunks += ($machine -split ';') }
    if ($user) { $chunks += ($user -split ';') }
    $vistos = @{}
    $ordenado = @()
    foreach ($c in $chunks) {
        if (-not $c) { continue }
        $t = "$c".Trim()
        if (-not $t) { continue }
        $chave = $t.TrimEnd('\').ToLowerInvariant()
        if ($vistos.ContainsKey($chave)) { continue }
        $vistos[$chave] = $true
        $ordenado += $t
    }
    $env:Path = ($ordenado -join ';')
}

function Get-PreferPathDirs {
    $dirs = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\Scripts"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher"),
        (Join-Path $env:ProgramFiles "nodejs"),
        (Join-Path $env:LOCALAPPDATA "Programs\nodejs"),
        (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin")
    )
    $existentes = @()
    foreach ($d in $dirs) {
        if ($d -and (Test-Path $d)) { $existentes += $d }
    }
    return $existentes
}

function Ensure-UserPathHas {
    param([string[]]$Dirs)
    $presentes = @()
    foreach ($d in @($Dirs)) {
        if ($d -and (Test-Path $d)) { $presentes += $d.TrimEnd('\') }
    }
    if ($presentes.Count -eq 0) {
        Update-SessionPath -PreferFirst @(Get-PreferPathDirs)
        return
    }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $partes = @()
    if ($userPath) {
        foreach ($p in ($userPath -split ';')) {
            if ($p -and $p.Trim()) { $partes += $p.Trim() }
        }
    }
    $norm = @{}
    foreach ($p in $partes) { $norm[$p.TrimEnd('\').ToLowerInvariant()] = $true }
    $faltam = @()
    foreach ($d in $presentes) {
        $chave = $d.TrimEnd('\').ToLowerInvariant()
        if (-not $norm.ContainsKey($chave)) { $faltam += $d }
    }
    if ($faltam.Count -gt 0) {
        $novo = @($faltam) + @($partes)
        [Environment]::SetEnvironmentVariable("Path", ($novo -join ';'), "User")
        Write-Host "    PATH do usuario atualizado. Esta sessao tambem passa a enxergar a ferramenta." -ForegroundColor Gray
    }
    Update-SessionPath -PreferFirst $presentes
}

function Add-PythonInstallToPath {
    $real = Find-RealPython
    $dirs = @()
    if ($real -and $real.Exe -match '\\python\.exe$') {
        $pasta = Split-Path $real.Exe -Parent
        $dirs += (Join-Path $pasta "Scripts")
        $dirs += $pasta
    }
    $dirs += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\Scripts")
    $dirs += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312")
    $dirs += (Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher")
    Ensure-UserPathHas -Dirs $dirs
}

function Get-PythonCandidates {
    $lista = New-Object System.Collections.Generic.List[object]
    $raiz = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $raiz) {
        $dirsPy = Get-ChildItem -Path $raiz -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "Python3*" } |
            Sort-Object -Property Name -Descending
        foreach ($dir in @($dirsPy)) {
            if (-not $dir) { continue }
            $exe = Join-Path $dir.FullName "python.exe"
            if (Test-Path $exe) {
                $lista.Add(@{ Exe = $exe; Prefix = @(); UsaLauncher = $false }) | Out-Null
            }
        }
    }
    foreach ($ver in @("Python312", "Python311", "Python310", "Python39")) {
        $exePf = Join-Path $env:ProgramFiles "$ver\python.exe"
        if (Test-Path $exePf) {
            $lista.Add(@{ Exe = $exePf; Prefix = @(); UsaLauncher = $false }) | Out-Null
        }
    }
    $launcher = Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher\py.exe"
    if (Test-Path $launcher) {
        $lista.Add(@{ Exe = $launcher; Prefix = @("-3"); UsaLauncher = $true }) | Out-Null
    }
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd -and $pyCmd.Source -notlike "*WindowsApps*") {
        $lista.Add(@{ Exe = $pyCmd.Source; Prefix = @("-3"); UsaLauncher = $true }) | Out-Null
    }
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd -and $pythonCmd.Source -notlike "*WindowsApps*") {
        $lista.Add(@{ Exe = $pythonCmd.Source; Prefix = @(); UsaLauncher = $false }) | Out-Null
    }
    return ,$lista.ToArray()
}

function Test-PythonCandidate {
    param($Candidate)
    $argList = @()
    foreach ($p in @($Candidate.Prefix)) { if ($p) { $argList += $p } }
    $argList += @("-c", "import sys; print(sys.executable); print('%d.%d' % sys.version_info[:2])")
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $raw = & $Candidate.Exe @argList 2>&1 | Out-String
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($codigo -ne 0) { return $null }
    if ($raw -match 'Microsoft Store|WindowsApps') { return $null }
    $ver = $null
    foreach ($line in ($raw -split "`r?`n")) {
        $t = "$line".Trim()
        if ($t -match '^\d+\.\d+$') { $ver = $t }
    }
    if (-not $ver) { return $null }
    $partes = $ver.Split(".")
    $major = [int]$partes[0]
    $minor = [int]$partes[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) { return $null }
    $prefix = @()
    foreach ($p in @($Candidate.Prefix)) { if ($p) { $prefix += $p } }
    return @{
        Exe         = $Candidate.Exe
        Prefix      = $prefix
        Version     = $ver
        UsaLauncher = [bool]$Candidate.UsaLauncher
    }
}

function Find-RealPython {
    $candidatos = Get-PythonCandidates
    foreach ($candidato in @($candidatos)) {
        if (-not $candidato) { continue }
        $ok = Test-PythonCandidate -Candidate $candidato
        if ($ok) { return $ok }
    }
    return $null
}

function Test-NodeOk {
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    if ($cmd.Source -like "*WindowsApps*") { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $saida = & node -v 2>&1 | Out-String
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($codigo -ne 0) { return $false }
    if ($saida -notmatch 'v(\d+)') { return $false }
    return ([int]$Matches[1] -ge 16)
}

function Test-DockerCli {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    if ($cmd.Source -like "*WindowsApps*") { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $saida = & docker version 2>&1 | Out-String
    $ErrorActionPreference = $prev
    return ($saida -match 'Version')
}

function Test-DockerEngine {
    if (-not (Test-DockerCli)) { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $saida = & docker info 2>&1 | Out-String
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = $prev
    $script:DockerInfoText = $saida
    return ($codigo -eq 0)
}

function Resolve-ComposeCommand {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $dc = Get-Command docker-compose -ErrorAction SilentlyContinue
    if ($dc -and $dc.Source -notlike "*WindowsApps*") {
        & docker-compose version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $script:ComposeCommand = @("docker-compose")
            $ErrorActionPreference = $prev
            return $true
        }
    }
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($dockerCmd -and $dockerCmd.Source -notlike "*WindowsApps*") {
        & docker compose version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $script:ComposeCommand = @("docker", "compose")
            $ErrorActionPreference = $prev
            return $true
        }
    }
    $script:ComposeCommand = @()
    $ErrorActionPreference = $prev
    return $false
}

function Find-DockerDesktopExe {
    $candidatos = @()
    if ($env:ProgramFiles) {
        $candidatos += (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe")
    }
    $pf86 = ${env:ProgramFiles(x86)}
    if ($pf86) {
        $candidatos += (Join-Path $pf86 "Docker\Docker\Docker Desktop.exe")
    }
    if ($env:LOCALAPPDATA) {
        $candidatos += (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
        $candidatos += (Join-Path $env:LOCALAPPDATA "Programs\Docker\Docker\Docker Desktop.exe")
    }
    foreach ($exe in $candidatos) {
        if ($exe -and (Test-Path $exe)) { return $exe }
    }
    return $null
}

function Wait-DockerEngine {
    param([int]$TimeoutSec = 180)
    $limite = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $limite) {
        if (Test-DockerEngine) { return $true }
        Write-Host "    Aguardando o motor Docker (docker info)..." -ForegroundColor Gray
        Start-Sleep -Seconds 5
    }
    if ($script:DockerInfoText) {
        Write-Host "    Ultima saida de docker info:" -ForegroundColor Yellow
        Write-Host $script:DockerInfoText -ForegroundColor Red
    }
    return $false
}

function Test-OllamaLocalLigado {
    $flag = Get-EnvValue "USE_OLLAMA_LOCAL" "1"
    return ($flag -match "^(1|true|yes|sim|on)$")
}

function Test-LinhaEhOllama {
    param([string]$Linha)
    if ([string]::IsNullOrWhiteSpace($Linha)) { return $false }
    $l = $Linha.ToLowerInvariant()
    # O executavel se chama ollama. Um caminho que so contem a palavra no meio nao conta.
    return [bool]($l -match '(^|[\\/ ])ollama(\.exe)?(\s|$)')
}

function Test-OllamaHttp {
    foreach ($caminho in @("/api/version", "/api/tags")) {
        try {
            $req = [System.Net.WebRequest]::Create(("http://127.0.0.1:11434{0}" -f $caminho))
            $req.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy()
            $req.Timeout = 2000
            $req.Method = "GET"
            $resp = $req.GetResponse()
            $code = [int]$resp.StatusCode
            $resp.Close()
            if ($code -ge 200 -and $code -lt 300) { return $true }
        } catch {
            continue
        }
    }
    return $false
}

function Wait-OllamaHttp {
    param([int]$TimeoutSec = 30)
    $limite = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $limite) {
        if (Test-OllamaHttp) { return $true }
        Write-Host "    Aguardando o Ollama em $($script:UrlOllama) ..." -ForegroundColor Gray
        Start-Sleep -Seconds 1
    }
    return $false
}

function Find-OllamaCli {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and $cmd.Source -notlike "*WindowsApps*") { return [string]$cmd.Source }
    $candidatos = @()
    if ($env:LOCALAPPDATA) {
        $candidatos += (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe")
    }
    if ($env:ProgramFiles) {
        $candidatos += (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    }
    foreach ($exe in $candidatos) {
        if ($exe -and (Test-Path -LiteralPath $exe)) { return $exe }
    }
    return $null
}

function Find-OllamaApp {
    $candidatos = @()
    if ($env:LOCALAPPDATA) {
        $candidatos += (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe")
        $candidatos += (Join-Path $env:LOCALAPPDATA "Programs\Ollama\Ollama.exe")
    }
    if ($env:ProgramFiles) {
        $candidatos += (Join-Path $env:ProgramFiles "Ollama\ollama app.exe")
        $candidatos += (Join-Path $env:ProgramFiles "Ollama\Ollama.exe")
    }
    foreach ($exe in $candidatos) {
        if ($exe -and (Test-Path -LiteralPath $exe)) { return $exe }
    }
    return $null
}

function Install-OllamaSeConfirmado {
    $url = "https://ollama.com/download"
    $pergunta = "Instalar o Ollama?`n    Motivo: o programa nao esta neste host. O script nao cadastra modelo nem endereco no LiteLLM; isso continua na UI. Winget: Ollama.Ollama. Sem instalar, a stack segue e a porta local nao sobe.`n    Caminho oficial: $url"
    if (-not (Confirm-Sim $pergunta)) {
        Write-Host "[AVISO] Instalacao do Ollama recusada. A subida continua sem o Ollama local." -ForegroundColor Yellow
        return $false
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "[AVISO] winget nao encontrado. Instale o Ollama em $url e rode de novo. A stack segue agora." -ForegroundColor Yellow
        return $false
    }
    $argsWinget = @(
        "install", "-e", "--id", "Ollama.Ollama",
        "--accept-package-agreements", "--accept-source-agreements",
        "--scope", "user"
    )
    & winget @argsWinget
    $dirs = @()
    if ($env:LOCALAPPDATA) { $dirs += (Join-Path $env:LOCALAPPDATA "Programs\Ollama") }
    Update-SessionPath -PreferFirst $dirs
    if ((Find-OllamaCli) -or (Find-OllamaApp)) { return $true }
    Write-Host "[AVISO] O winget rodou, mas o Ollama ainda nao foi encontrado. A stack segue. Se o instalador pediu reinicio, rode este script de novo." -ForegroundColor Yellow
    return $false
}

function Start-OllamaLocal {
    $script:OllamaIniciadoPeloScript = $false
    $script:OllamaPid = 0
    if (-not (Test-OllamaLocalLigado)) {
        Write-Host "[INFO] Ollama local desligado (USE_OLLAMA_LOCAL=0). O script nao abre nem fecha o programa. O endereco fica na UI do LiteLLM." -ForegroundColor Gray
        return
    }
    if (Test-OllamaHttp) {
        Write-Host "[OK] Ollama ja responde em $($script:UrlOllama). Nao abri outro e nao vou encerra-lo no Q." -ForegroundColor Green
        return
    }
    $cli = Find-OllamaCli
    $app = Find-OllamaApp
    if (-not $cli -and -not $app) {
        Write-Host "[AVISO] Ollama nao esta instalado neste host. A stack segue. Modelo e endereco nao sao gravados no LiteLLM." -ForegroundColor Yellow
        if (-not (Install-OllamaSeConfirmado)) { return }
        $cli = Find-OllamaCli
        $app = Find-OllamaApp
    }
    if (Test-OllamaHttp) {
        Write-Host "[OK] Ollama ja responde em $($script:UrlOllama). Nao abri outro." -ForegroundColor Green
        return
    }
    $proc = $null
    try {
        if ($cli) {
            Write-Host "[+] Iniciando ollama serve em segundo plano..." -ForegroundColor Yellow
            $proc = Start-Process -FilePath $cli -ArgumentList @("serve") -WindowStyle Hidden -PassThru
        } elseif ($app) {
            Write-Host "[+] Iniciando o aplicativo Ollama em segundo plano..." -ForegroundColor Yellow
            $proc = Start-Process -FilePath $app -WindowStyle Hidden -PassThru
        }
    } catch {
        Write-Host "[AVISO] Nao consegui iniciar o Ollama: $($_.Exception.Message). A stack segue." -ForegroundColor Yellow
        return
    }
    if (-not $proc) {
        Write-Host "[AVISO] O Ollama nao devolveu processo. A stack segue." -ForegroundColor Yellow
        return
    }
    $script:OllamaPid = [int]$proc.Id
    $script:OllamaIniciadoPeloScript = $true
    if (Wait-OllamaHttp -TimeoutSec 30) {
        Write-Host "[OK] Ollama local no ar em $($script:UrlOllama). Este script encerra esse processo no Q." -ForegroundColor Green
    } else {
        Write-Host "[AVISO] O Ollama foi iniciado por este script, mas $($script:UrlOllama) nao respondeu a tempo. A stack segue." -ForegroundColor Yellow
    }
}

function Stop-OllamaLocal {
    if (-not $script:OllamaIniciadoPeloScript) {
        Write-Host "    Ollama nao foi aberto por este script. Se ja havia um na maquina, ele continua." -ForegroundColor Gray
        return
    }
    $alvo = 0
    if ($script:OllamaPid -gt 0) { $alvo = [int]$script:OllamaPid }
    if ($alvo -le 0) {
        Write-Host "[AVISO] O script marcou o Ollama como iniciado, mas nao ha PID. Nenhum outro processo foi encerrado." -ForegroundColor Yellow
        $script:OllamaIniciadoPeloScript = $false
        return
    }
    $vivo = Get-Process -Id $alvo -ErrorAction SilentlyContinue
    if (-not $vivo) {
        Write-Host "    O PID $alvo do Ollama que este script abriu ja nao existe." -ForegroundColor Gray
        $script:OllamaIniciadoPeloScript = $false
        $script:OllamaPid = 0
        return
    }
    if (-not (Test-LinhaEhOllama (Get-LinhaDeComando -ProcessId $alvo))) {
        Write-Host "[AVISO] PID $alvo nao parece o Ollama que este script abriu. Nao encerrei esse processo." -ForegroundColor Yellow
        return
    }
    Write-Host "    Encerrando o Ollama aberto por este script (PID $alvo) e os filhos." -ForegroundColor Yellow
    Stop-ArvoreProcesso -ProcessId $alvo
    $script:OllamaIniciadoPeloScript = $false
    $script:OllamaPid = 0
    if (Test-OllamaHttp) {
        Write-Host "[AVISO] $($script:UrlOllama) ainda responde. Outro Ollama, que este script nao abriu, foi mantido." -ForegroundColor Yellow
    } else {
        Write-Host "[OK] Ollama local encerrado." -ForegroundColor Green
    }
}

function Stop-DockerDesktopIniciado {
    if (-not $script:DockerIniciadoPeloScript) {
        Write-Host "    Docker Desktop nao foi aberto por este script. O motor que ja estava em execucao continua." -ForegroundColor Gray
        return
    }
    Write-Host "    Encerrando o Docker Desktop que este script abriu, depois do compose down..." -ForegroundColor Yellow
    $usouCli = $false
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        $prev = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $ajuda = & docker desktop --help 2>&1 | Out-String
        if ($ajuda -match '(?i)\bstop\b') {
            & docker desktop stop 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
            $usouCli = $true
        }
        $ErrorActionPreference = $prev
    }
    if (-not $usouCli) {
        Write-Host "    Comando docker desktop stop indisponivel. Fechando o processo do Docker Desktop e o backend." -ForegroundColor Yellow
        foreach ($nome in @("Docker Desktop", "com.docker.backend")) {
            foreach ($p in @(Get-Process -Name $nome -ErrorAction SilentlyContinue)) {
                Write-Host "    Pedindo saida de $($p.ProcessName) PID $($p.Id)." -ForegroundColor Gray
                try {
                    $null = $p.CloseMainWindow()
                } catch {
                    Write-Host "    CloseMainWindow falhou para PID $($p.Id)." -ForegroundColor Gray
                }
            }
        }
        Start-Sleep -Seconds 3
        if (Test-DockerEngine) {
            foreach ($nome in @("Docker Desktop", "com.docker.backend")) {
                foreach ($p in @(Get-Process -Name $nome -ErrorAction SilentlyContinue)) {
                    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
    $limite = (Get-Date).AddSeconds(45)
    do {
        if (-not (Test-DockerEngine)) {
            Write-Host "[OK] Docker Desktop encerrado. docker info nao responde mais." -ForegroundColor Green
            $script:DockerIniciadoPeloScript = $false
            return
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $limite)
    Write-Host "[AVISO] O Docker Desktop que este script abriu ainda responde a docker info." -ForegroundColor Yellow
}

function Write-StatusOllama {
    if (-not (Test-OllamaLocalLigado)) {
        Write-Host "[OLLAMA] Local desligado (USE_OLLAMA_LOCAL=0). Endereco na UI do LiteLLM." -ForegroundColor Gray
        return
    }
    if (Test-OllamaHttp) {
        Write-Host "[OLLAMA] Local online em $($script:UrlOllama)" -ForegroundColor Green
        return
    }
    if ($script:OllamaIniciadoPeloScript) {
        Write-Host "[OLLAMA] Este script abriu o Ollama, mas $($script:UrlOllama) ainda nao responde." -ForegroundColor Yellow
        return
    }
    Write-Host "[OLLAMA] Local offline em $($script:UrlOllama)" -ForegroundColor Yellow
}

function Write-StatusDockerDono {
    if ($script:DockerIniciadoPeloScript) {
        Write-Host "[DOCKER] Motor aberto por este script. A tecla Q encerra o Docker Desktop depois do compose." -ForegroundColor Yellow
        return
    }
    Write-Host "[DOCKER] Motor nao foi aberto por este script. A tecla Q nao fecha o Docker Desktop." -ForegroundColor Gray
}

function Test-RedeComunicacao {
    if (-not (Test-DockerEngine)) { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker network inspect rede_comunicacao 2>&1 | Out-Null
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return ($codigo -eq 0)
}

function Ensure-RedeComunicacao {
    if (Test-RedeComunicacao) {
        Write-Host "[OK] Rede rede_comunicacao" -ForegroundColor Green
        return $true
    }
    $prevRede = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $saidaRede = & docker network create rede_comunicacao 2>&1
    $codigoRede = $LASTEXITCODE
    $ErrorActionPreference = $prevRede
    if ($codigoRede -ne 0) {
        foreach ($linha in @($saidaRede)) {
            if ($linha -is [System.Management.Automation.ErrorRecord]) {
                Write-Host $linha.ToString() -ForegroundColor Yellow
            } else {
                Write-Host "$linha" -ForegroundColor Yellow
            }
        }
    }
    if (Test-RedeComunicacao) {
        Write-Host "[OK] Rede rede_comunicacao" -ForegroundColor Green
        return $true
    }
    Write-Host "[ERRO] docker network create rede_comunicacao falhou." -ForegroundColor Red
    return $false
}

function Invoke-StackApp {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$AcaoStack
    )
    $arquivo = $null
    if ($Id -eq "n8n") {
        $arquivo = $COMPOSE_N8N
    } elseif ($Id -eq "llm") {
        $arquivo = $COMPOSE_LLM
    } else {
        Write-Host "[ERRO] Stack desconhecida: $Id. Vale n8n ou llm." -ForegroundColor Red
        return 1
    }
    if ($Id -eq "llm" -and -not (Test-LlmConfigured)) {
        if ($AcaoStack -eq "parar") {
            Write-Host "[INFO] Stack LLM sem chaves no .env. Nada foi derrubado." -ForegroundColor Cyan
            return 0
        }
        Write-Host "[AVISO] Chaves ausentes no .env. Rode o Setup para gera-las. A stack LLM nao sobe." -ForegroundColor Yellow
        return 1
    }
    if ($AcaoStack -eq "iniciar" -and $Id -eq "n8n") {
        $prevPs = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $langfuseNoAr = & docker ps --filter "name=^langfuse-web$" --filter "status=running" --format "{{.Names}}" 2>&1
        $litellmNoAr = & docker ps --filter "name=^litellm$" --filter "status=running" --format "{{.Names}}" 2>&1
        $ErrorActionPreference = $prevPs
        if (-not "$langfuseNoAr".Trim() -or -not "$litellmNoAr".Trim()) {
            Write-Host "[INFO] A stack LLM esta fora do ar. O n8n sobe mesmo assim. A credencial OpenAI aponta para http://litellm:4000/v1 e so responde quando Langfuse e LiteLLM estiverem no ar. Isso nao e erro." -ForegroundColor Cyan
        }
        $env:NODES_EXCLUDE = ConvertTo-NodesExcludeJson (Get-EnvValue "N8N_NODES_EXCLUDE" "")
    }
    if ($Id -eq "n8n" -and $AcaoStack -ne "parar") {
        Ensure-ArquivosN8n
    }
    $composeArgs = @()
    if ($AcaoStack -eq "iniciar") {
        $composeArgs = @("up", "-d")
    } elseif ($AcaoStack -eq "parar") {
        $composeArgs = @("down")
    } elseif ($AcaoStack -eq "reiniciar") {
        $composeArgs = @("restart")
    } else {
        Write-Host "[ERRO] Acao desconhecida: $AcaoStack. Vale iniciar, parar ou reiniciar." -ForegroundColor Red
        return 1
    }
    $codigoStack = Invoke-Compose -ComposeFile $arquivo -ComposeArgs $composeArgs
    if ($null -eq $codigoStack) { $codigoStack = 1 }
    if ($AcaoStack -eq "iniciar" -and $Id -eq "n8n" -and $codigoStack -eq 0) {
        Show-NodesExclude
    }
    return $codigoStack
}

function Invoke-WingetInstall {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Nome,
        [Parameter(Mandatory = $true)][string]$Motivo,
        [Parameter(Mandatory = $true)][bool]$ScopeUser,
        [Parameter(Mandatory = $true)][string]$UrlOficial
    )
    $texto = "Instalar $Nome ?`n    Motivo: $Motivo"
    if (-not (Confirm-Sim $texto)) {
        Add-Pendencia -Nome $Nome -Motivo "Instalacao recusada. $Motivo Caminho oficial: $UrlOficial"
        return $false
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "    winget nao encontrado. Caminho oficial: $UrlOficial" -ForegroundColor Yellow
        Add-Pendencia -Nome $Nome -Motivo "winget ausente. Instale por este caminho e rode de novo: $UrlOficial. $Motivo"
        return $false
    }
    $argsComum = @(
        "install", "-e", "--id", $Id,
        "--accept-package-agreements", "--accept-source-agreements"
    )
    if ($ScopeUser) {
        Write-Host "    winget install $Id --scope user" -ForegroundColor Gray
        & winget @($argsComum + @("--scope", "user"))
        $codigoUser = $LASTEXITCODE
        if ($codigoUser -ne 0 -and $codigoUser -ne -1978335189) {
            Write-Host "    Escopo de usuario nao concluiu (codigo $codigoUser). Tentando a instalacao padrao; pode pedir administrador." -ForegroundColor Yellow
            & winget @argsComum
        }
    } else {
        Write-Host "    $Nome nao instala em escopo de usuario. O winget pode pedir administrador." -ForegroundColor Gray
        Write-Host "    winget install $Id" -ForegroundColor Gray
        & winget @argsComum
    }
    $codigoWinget = $LASTEXITCODE
    if ($codigoWinget -ne 0 -and $codigoWinget -ne -1978335189) {
        Write-Host "    winget terminou com codigo $codigoWinget. A saida acima e a causa." -ForegroundColor Yellow
    }
    return $true
}

function Install-FerramentaSeFaltar {
    param(
        [Parameter(Mandatory = $true)][string]$Nome,
        [Parameter(Mandatory = $true)][scriptblock]$Teste,
        [Parameter(Mandatory = $true)][string]$WingetId,
        [Parameter(Mandatory = $true)][bool]$ScopeUser,
        [Parameter(Mandatory = $true)][string]$Motivo,
        [Parameter(Mandatory = $true)][string]$UrlOficial,
        [string[]]$PathDirs,
        [Parameter(Mandatory = $true)][string]$MotivoFalha
    )
    if (& $Teste) { return $true }
    $aceitou = Invoke-WingetInstall -Id $WingetId -Nome $Nome -Motivo $Motivo -ScopeUser $ScopeUser -UrlOficial $UrlOficial
    if ($PathDirs -and @($PathDirs).Count -gt 0) {
        Ensure-UserPathHas -Dirs $PathDirs
    } else {
        Update-SessionPath -PreferFirst @(Get-PreferPathDirs)
    }
    if (& $Teste) { return $true }
    if ($aceitou) { Add-Pendencia -Nome $Nome -Motivo $MotivoFalha }
    return $false
}

function Get-EnvFileMap {
    param([Parameter(Mandatory = $true)][string]$Path)
    $mapa = @{}
    if (-not (Test-Path $Path)) { return $mapa }
    foreach ($line in Get-Content $Path -Encoding UTF8) {
        if ($line -match '^\s*#') { continue }
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $mapa[$Matches[1]] = $Matches[2].Trim()
        }
    }
    return $mapa
}

function Test-ValorPlaceholder {
    param([string]$Value)
    if ($null -eq $Value) { return $true }
    $v = "$Value".Trim()
    if ($v -like "__GENERATE_*") { return $false }
    $ruins = @(
        "",
        "seu_usuario",
        "sua_senha",
        "seu_token_do_ngrok_aqui",
        "your_ngrok_token",
        "sua_chave_secreta_aqui",
        "your_secret_key",
        "seu_token_porteiro_aqui",
        "your_porteiro_token",
        "changeme",
        "change_me",
        "placeholder"
    )
    if ($ruins -contains $v) { return $true }
    if ($v -match '^(seu_|sua_|your_)') { return $true }
    return $false
}

function Test-DevePedirValor {
    param(
        [string]$Key,
        [string]$Current,
        [string]$Example,
        [bool]$HasCurrent
    )
    if ($Key -eq "SCOUT_NGROK_TUNNEL_URL") { return $false }
    if ($Key -eq "LANGFUSE_INIT_USER_EMAIL") { return $false }
    # Vazio (ou ausente) e o padrao: so o nucleo. Nao e segredo, nao pede valor
    # e nao entra na rotacao. n8n, llm e token desconhecido tambem nao.
    if ($Key -eq "STACKS_BOOT") { return $false }
    if ($Example -like "__GENERATE_*") { return $false }
    if ($HasCurrent -and $Current -like "__GENERATE_*") { return $false }
    if ($HasCurrent -and (Test-ValorPlaceholder $Current)) { return $true }
    if ((-not $HasCurrent) -and (Test-ValorPlaceholder $Example)) { return $true }
    return $false
}

function Get-MotivoChave {
    param([string]$Key)
    switch ($Key) {
        "NGROK_AUTHTOKEN" {
            return "O container ngrok/ngrok autentica com o token do painel (https://dashboard.ngrok.com/get-started/your-authtoken). O script nao inventa token."
        }
        "PORTEIRO_USER" { return "O acesso do Porteiro usa este usuario. O placeholder do exemplo nao serve." }
        "PORTEIRO_PASS" { return "O acesso do Porteiro usa esta senha. O placeholder do exemplo nao serve." }
        default { return "O valor ainda e placeholder ou esta vazio. Chave que ja saiu do placeholder nao e rotacionada." }
    }
}

function Repair-LangfuseEmail {
    $atual = Get-EnvValue "LANGFUSE_INIT_USER_EMAIL" ""
    if ($atual -eq "admin@localhost" -or [string]::IsNullOrWhiteSpace($atual)) {
        Set-EnvValue -Key "LANGFUSE_INIT_USER_EMAIL" -Value "admin@example.com"
        Write-Host "[OK] LANGFUSE_INIT_USER_EMAIL definido como admin@example.com (o Langfuse 4.30 rejeita admin@localhost)." -ForegroundColor Green
    }
}

function Confirm-ValoresManuais {
    if (-not (Test-Path $PATH_ENV)) { return }
    $exemplo = Get-EnvFileMap -Path (Join-Path $PSScriptRoot ".env.example")
    $atual = Get-EnvFileMap -Path $PATH_ENV
    $chaves = @()
    foreach ($k in @($exemplo.Keys)) { $chaves += $k }
    foreach ($k in @($atual.Keys)) {
        if ($chaves -notcontains $k) { $chaves += $k }
    }
    foreach ($key in $chaves) {
        $exemploValor = ""
        if ($exemplo.ContainsKey($key)) { $exemploValor = [string]$exemplo[$key] }
        if ($exemploValor -like "__GENERATE_*") {
            $v = ""
            $tem = $atual.ContainsKey($key)
            if ($tem) { $v = [string]$atual[$key] }
            if ((-not $tem) -or $v -like "__GENERATE_*" -or [string]::IsNullOrWhiteSpace($v)) {
                # Se o init_env ja falhou, uma pendencia de .env basta. Nao listar cada marcador.
                $jaTemEnv = $false
                foreach ($p in $script:Pendencias) {
                    if ($p.Nome -eq ".env") { $jaTemEnv = $true }
                }
                if (-not $jaTemEnv) {
                    Add-Pendencia -Nome $key -Motivo "Segredo gerado ausente ou ainda marcador. O scripts\init_env.py preenche isso; nao digitamos esse valor."
                }
            }
            continue
        }
        $temAtual = $atual.ContainsKey($key)
        $valorAtual = ""
        if ($temAtual) { $valorAtual = [string]$atual[$key] }
        if (-not (Test-DevePedirValor -Key $key -Current $valorAtual -Example $exemploValor -HasCurrent $temAtual)) {
            continue
        }
        $motivo = Get-MotivoChave $key
        if (-not (Confirm-Sim "Gravar um valor novo para $key no .env local?`n    Motivo: $motivo")) {
            Add-Pendencia -Nome $key -Motivo "Valor ainda e placeholder e a gravacao foi recusada. $motivo"
            continue
        }
        $digitado = Read-ValorOculto "    Valor de $key (nao sera ecoado de novo)"
        if ([string]::IsNullOrWhiteSpace($digitado) -or (Test-ValorPlaceholder $digitado.Trim()) -or ($digitado -match '[\r\n]')) {
            Write-Host "    Nada gravado: vazio, placeholder ou valor em mais de uma linha." -ForegroundColor Yellow
            Add-Pendencia -Nome $key -Motivo "Valor nao informado para $key."
            continue
        }
        Set-EnvValue -Key $key -Value $digitado.Trim()
        Write-Host "    $key gravado no .env." -ForegroundColor Green
    }
}

function Test-EnvTemMarcadorPendente {
    # Comentario com __GENERATE_*__ nao conta. So valor de linha KEY=VALUE.
    if (-not (Test-Path -LiteralPath $PATH_ENV)) { return $false }
    $mapa = Get-EnvFileMap -Path $PATH_ENV
    foreach ($chave in @($mapa.Keys)) {
        $valor = [string]$mapa[$chave]
        if ($valor -like "*__GENERATE_*") { return $true }
    }
    return $false
}

function Initialize-EnvDoProjeto {
    $exemplo = Join-Path $PSScriptRoot ".env.example"
    if (-not (Test-Path $PATH_ENV)) {
        $sim = Confirm-Sim "Nao ha .env na raiz. Copiar .env.example e gerar os segredos com scripts\init_env.py?`n    Motivo: n8n, ngrok e Langfuse/LiteLLM leem esse arquivo. O .env nao vai para o Git."
        if (-not $sim) {
            Add-Pendencia -Nome ".env" -Motivo "Arquivo ausente. Sem ele a subida nao tem token nem chave."
            return
        }
        if (-not (Test-Path $exemplo)) {
            Add-Pendencia -Nome ".env" -Motivo ".env.example nao encontrado no repositorio."
            return
        }
        Copy-Item -Path $exemplo -Destination $PATH_ENV
        Write-Host "[OK] .env criado a partir do exemplo." -ForegroundColor Green
    } else {
        Write-Host "[OK] .env presente" -ForegroundColor Green
    }
    if (Test-Path -LiteralPath $PATH_ENV) { Repair-EnvBom }
    if (Test-EnvTemMarcadorPendente) {
        $py = Find-RealPython
        if (-not $py) {
            Add-Pendencia -Nome ".env" -Motivo "Ha marcadores __GENERATE_*__ e nao ha Python real para scripts\init_env.py. O alias da Microsoft Store nao serve."
        } else {
            Write-Host "[SISTEMA] Preenchendo marcadores __GENERATE_*__ sem rotacionar segredo ja definido." -ForegroundColor Gray
            $argList = @()
            foreach ($p in @($py.Prefix)) { if ($p) { $argList += $p } }
            $argList += (Join-Path $PSScriptRoot "scripts\init_env.py")
            & $py.Exe @argList
            if ($LASTEXITCODE -ne 0) {
                Add-Pendencia -Nome ".env" -Motivo "scripts\init_env.py falhou. Veja a saida acima. Segredo que ja existe nao e rotacionado."
            }
        }
    }
    if (Test-Path $PATH_ENV) {
        Repair-LangfuseEmail
        Confirm-ValoresManuais
    }
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

function Ensure-DataDirs {
    foreach ($rel in @("n8n\n8n\data", "n8n\storage\Porteiro")) {
        $destino = Join-Path $PSScriptRoot $rel
        if (-not (Test-Path $destino)) {
            New-Item -ItemType Directory -Path $destino -Force | Out-Null
            Write-Host "[OK] Pasta criada: $rel" -ForegroundColor Green
        }
    }
    Ensure-ArquivosN8n
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $hash = $sha.ComputeHash($stream)
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
    return (([System.BitConverter]::ToString($hash)).Replace("-", "")).ToLowerInvariant()
}

function Ensure-ProjectVenv {
    param(
        [Parameter(Mandatory = $true)][string]$Nome,
        [Parameter(Mandatory = $true)][string]$VenvDir,
        [Parameter(Mandatory = $true)][string]$Requirements,
        [Parameter(Mandatory = $true)][string]$ImportCheck,
        [Parameter(Mandatory = $true)][bool]$Obrigatorio,
        [Parameter(Mandatory = $true)][string]$Motivo,
        [string]$MarcadorAdiar
    )
    $pyExe = Join-Path $VenvDir "Scripts\python.exe"
    $stamp = Join-Path $VenvDir ".n8groker-requirements.sha256"
    $hashReq = ""
    if (Test-Path -LiteralPath $Requirements) { $hashReq = Get-FileSha256 $Requirements }
    if ((Test-Path $pyExe) -and $hashReq) {
        $gravado = ""
        if (Test-Path -LiteralPath $stamp) { $gravado = ([System.IO.File]::ReadAllText($stamp)).Trim() }
        if ($gravado -eq $hashReq) {
            $prev = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            & $pyExe -c $ImportCheck 2>&1 | Out-Null
            $codigo = $LASTEXITCODE
            $ErrorActionPreference = $prev
            if ($codigo -eq 0) {
                if ($MarcadorAdiar -and (Test-Path $MarcadorAdiar)) {
                    Remove-Item $MarcadorAdiar -Force -ErrorAction SilentlyContinue
                }
                Write-Host "[OK] $Nome" -ForegroundColor Green
                return
            }
        }
    }
    if ((Test-Path $pyExe) -and (Test-Path -LiteralPath $Requirements)) {
        Write-Host "[AVISO] $Nome esta incompleto ou o requirements mudou. Rodando pip install de novo." -ForegroundColor Yellow
        & $pyExe -m pip install --no-cache-dir -r $Requirements
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[AVISO] pip de $Nome falhou. O painel, se subir, avisa a dependencia que falta." -ForegroundColor Yellow
            return
        }
        & $pyExe -c $ImportCheck
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[AVISO] $Nome instalou, mas o import falhou. O painel avisa isso na tela em vez de quebrar mudo." -ForegroundColor Yellow
            return
        }
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($stamp, ($hashReq + "`n"), $utf8)
        Write-Host "[OK] $Nome" -ForegroundColor Green
        return
    }
    if ($MarcadorAdiar -and (Test-Path $MarcadorAdiar)) {
        Write-Host "[INFO] $Nome adiado nesta maquina. Apague $MarcadorAdiar para perguntar de novo." -ForegroundColor Gray
        return
    }
    if (-not (Find-RealPython)) {
        if ($Obrigatorio) {
            Add-Pendencia -Nome $Nome -Motivo "Python real ausente, entao o venv nao foi criado. $Motivo"
        } else {
            Write-Host "[AVISO] $Nome ficou sem venv: nao ha Python real. Este boot nao abre o painel Streamlit." -ForegroundColor Yellow
        }
        return
    }
    if (Test-Path $pyExe) {
        $pergunta = "Instalar as dependencias de $Nome ?`n    Motivo: $Motivo"
    } else {
        $pergunta = "Criar o ambiente virtual de $Nome e instalar as dependencias?`n    Motivo: $Motivo"
    }
    if (-not (Confirm-Sim $pergunta)) {
        if ($Obrigatorio) {
            Add-Pendencia -Nome $Nome -Motivo "Preparacao recusada. $Motivo"
        } else {
            if ($MarcadorAdiar) {
                Set-Content -Path $MarcadorAdiar -Value "adiado" -Encoding ASCII
            }
            Write-Host "[AVISO] $Nome nao foi preparado. A subida dos containers segue sem o painel Streamlit." -ForegroundColor Yellow
        }
        return
    }
    $hostPy = Find-RealPython
    if (-not (Test-Path $pyExe)) {
        $argList = @()
        foreach ($p in @($hostPy.Prefix)) { if ($p) { $argList += $p } }
        $argList += @("-m", "venv", $VenvDir)
        & $hostPy.Exe @argList
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pyExe)) {
            if ($Obrigatorio) {
                Add-Pendencia -Nome $Nome -Motivo "Falha ao criar o venv. Veja a saida acima."
            } else {
                Write-Host "[AVISO] Falha ao criar o venv de $Nome. A subida segue sem o painel." -ForegroundColor Yellow
            }
            return
        }
    }
    & $pyExe -m pip install --no-cache-dir -r $Requirements
    if ($LASTEXITCODE -ne 0) {
        if ($Obrigatorio) {
            Add-Pendencia -Nome $Nome -Motivo "pip install falhou. Veja a saida acima."
        } else {
            Write-Host "[AVISO] pip de $Nome falhou. Veja a saida acima. A subida segue sem o painel." -ForegroundColor Yellow
        }
        return
    }
    & $pyExe -c $ImportCheck
    if ($LASTEXITCODE -ne 0) {
        if ($Obrigatorio) {
            Add-Pendencia -Nome $Nome -Motivo "O import de verificacao falhou depois do pip."
        } else {
            Write-Host "[AVISO] $Nome instalou, mas o import falhou. A subida segue sem o painel." -ForegroundColor Yellow
        }
        return
    }
    if ($hashReq) {
        $utf8Novo = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($stamp, ($hashReq + "`n"), $utf8Novo)
    }
    Write-Host "[OK] $Nome" -ForegroundColor Green
    return
}

function Invoke-AutoSetup {
    if ($Stack) { return }
    Write-Host "[SISTEMA] Verificando o que esta subida realmente chama..." -ForegroundColor Gray
    Write-Host "    Node (Porteiro), Docker Desktop (ja inclui o Compose) e Python real (init_env e venv)." -ForegroundColor Gray
    Write-Host "    Git, npm e o binario ngrok do host ficam de fora: ninguem os chama aqui." -ForegroundColor Gray
    Update-SessionPath -PreferFirst @(Get-PreferPathDirs)

    $dirsNode = @(
        (Join-Path $env:ProgramFiles "nodejs"),
        (Join-Path $env:LOCALAPPDATA "Programs\nodejs")
    )
    if (Install-FerramentaSeFaltar -Nome "Node.js" -Teste { Test-NodeOk } -WingetId "OpenJS.NodeJS.LTS" -ScopeUser $true -Motivo "O Porteiro sobe com node porteiro.js (modulos nativos http, fs e path)." -UrlOficial "https://nodejs.org/" -PathDirs $dirsNode -MotivoFalha "O winget rodou, mas node -v (16+) ainda nao funciona nesta sessao. Feche e abra o terminal, ou instale em https://nodejs.org/.") {
        $versaoNode = (& node -v 2>&1 | Out-String).Trim()
        Write-Host "[OK] Node.js ($versaoNode)" -ForegroundColor Green
    }

    $dirsPython = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\Scripts"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher")
    )
    if (Install-FerramentaSeFaltar -Nome "Python 3.12" -Teste { $null -ne (Find-RealPython) } -WingetId "Python.Python.3.12" -ScopeUser $true -Motivo "scripts\init_env.py e os venv precisam de um Python real. O atalho da Microsoft Store nao executa." -UrlOficial "https://www.python.org/downloads/" -PathDirs $dirsPython -MotivoFalha "O winget rodou, mas nenhum Python 3.10+ real respondeu. O alias da Store nao conta. Instale em https://www.python.org/downloads/ e rode de novo.") {
        $pyInfo = Find-RealPython
        Write-Host "[OK] Python $($pyInfo.Version)" -ForegroundColor Green
        Add-PythonInstallToPath
    }

    $dirsDocker = @()
    if ($env:ProgramFiles) {
        $dirsDocker += (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin")
    }
    # Uma pergunta so para o Desktop. Ele ja traz `docker compose`. O pacote
    # winget Docker.DockerCompose (docker-compose portatil) so entra se, depois
    # disso, nem `docker compose` nem `docker-compose` responderem.
    $dockerCliOk = Install-FerramentaSeFaltar -Nome "Docker Desktop" -Teste { Test-DockerCli } -WingetId "Docker.DockerDesktop" -ScopeUser $false -Motivo "n8n, ngrok, Scout e Langfuse/LiteLLM sobem em container. O Docker Desktop ja inclui o Compose (docker compose). Neste Windows o instalador usa WSL2 e pode pedir administrador." -UrlOficial "https://www.docker.com/products/docker-desktop/" -PathDirs $dirsDocker -MotivoFalha "O winget rodou, mas o comando docker ainda nao responde. Se o instalador pediu reinicio, reinicie e rode este script de novo."
    if ($dockerCliOk) {
        Write-Host "[OK] Docker CLI" -ForegroundColor Green
        if (Install-FerramentaSeFaltar -Nome "Docker Compose" -Teste { Resolve-ComposeCommand } -WingetId "Docker.DockerCompose" -ScopeUser $false -Motivo "O comando docker responde, mas nem docker compose nem docker-compose funcionam. O Desktop normalmente ja inclui o Compose; este pacote e so a reserva." -UrlOficial "https://docs.docker.com/compose/install/" -MotivoFalha "Nem docker compose nem docker-compose responderam depois da instalacao. Veja https://docs.docker.com/compose/install/.") {
            $nomeCompose = (@($script:ComposeCommand) -join ' ')
            Write-Host "[OK] Docker Compose ($nomeCompose)" -ForegroundColor Green
        }
    } else {
        Write-Host "[AVISO] Sem o comando docker, o Compose nao e perguntado a parte. O Docker Desktop ja inclui docker compose." -ForegroundColor Yellow
    }

    Initialize-EnvDoProjeto
    Ensure-DataDirs

    if (Test-DockerCli) {
        if (Test-DockerEngine) {
            Write-Host "[OK] Motor Docker em execucao. Este script nao vai fecha-lo no Q." -ForegroundColor Green
            $script:DockerIniciadoPeloScript = $false
        } else {
            $abrir = Confirm-Sim "O Docker esta instalado, mas o motor esta parado. Abrir o Docker Desktop e esperar ate docker info responder?`n    Motivo: sem o motor, nenhum container sobe."
            if (-not $abrir) {
                Add-Pendencia -Nome "Motor Docker" -Motivo "Motor parado e a abertura foi recusada."
            } else {
                $exeDocker = Find-DockerDesktopExe
                if ($exeDocker) {
                    Write-Host "    Abrindo $exeDocker" -ForegroundColor Gray
                    $procDocker = Start-Process -FilePath $exeDocker -PassThru
                    if ($procDocker) {
                        $script:DockerIniciadoPeloScript = $true
                        Write-Host "    Docker Desktop aberto por este script. A tecla Q encerra ele depois do compose." -ForegroundColor Gray
                    }
                } else {
                    Write-Host "    Nao achei Docker Desktop.exe. Abra o aplicativo; o script espera o motor." -ForegroundColor Yellow
                }
                if (Wait-DockerEngine) {
                    Write-Host "[OK] Motor Docker em execucao" -ForegroundColor Green
                    if (-not $script:DockerIniciadoPeloScript) {
                        Write-Host "    O motor subiu sem este script ter aberto o Desktop. O Q nao vai fecha-lo." -ForegroundColor Gray
                    }
                } else {
                    if ($script:DockerInfoText -match 'WSL|wsl') {
                        $wslSim = Confirm-Sim "A saida do docker info cita o WSL. Rodar wsl --install (caminho oficial; pode pedir administrador e reinicio)?`n    Motivo: o Docker Desktop neste Windows depende do WSL2."
                        if ($wslSim) {
                            if (Get-Command wsl -ErrorAction SilentlyContinue) {
                                & wsl --install
                                Write-Host "    Se o Windows pediu reinicio, reinicie e rode este script de novo. Ele nao refaz o que ja estiver pronto." -ForegroundColor Yellow
                            } else {
                                Write-Host "    O comando wsl nao existe. Instale por https://learn.microsoft.com/windows/wsl/install" -ForegroundColor Yellow
                            }
                        } else {
                            Add-Pendencia -Nome "WSL" -Motivo "docker info citou o WSL e a instalacao foi recusada. Caminho oficial: https://learn.microsoft.com/windows/wsl/install"
                        }
                    }
                    Add-Pendencia -Nome "Motor Docker" -Motivo "docker info nao respondeu a tempo. A saida esta acima."
                }
            }
        }
    }

    if (Test-DockerEngine) {
        if (-not (Ensure-RedeComunicacao)) {
            Add-Pendencia -Nome "rede_comunicacao" -Motivo "docker network create rede_comunicacao falhou. Veja a saida acima."
        }
    }

    if (Test-ScoutEnabled) {
        if (-not (Test-Path $COMPOSE_SCOUT)) {
            Add-Pendencia -Nome "Scout" -Motivo "USE_SCOUT=1, mas Scout_OSINT_Docker\docker-compose.yml nao existe."
        } else {
            Write-Host "[INFO] Scout sobe no Docker. A gestao fica na aba Scout do painel, nao numa janela separada." -ForegroundColor Gray
        }
    } else {
        Write-Host "[INFO] Scout desabilitado (USE_SCOUT=0)." -ForegroundColor Gray
    }

    Ensure-ProjectVenv -Nome "Control Plane (Streamlit)" -VenvDir (Join-Path $PSScriptRoot "control_plane\.venv") -Requirements (Join-Path $PSScriptRoot "control_plane\requirements.txt") -ImportCheck "import streamlit; import cryptography" -Obrigatorio $false -Motivo "Com o venv pronto, este script sobe o painel em http://localhost:8501. Recusar deixa o HUD sem o Streamlit. cryptography entra no mesmo requirements." -MarcadorAdiar $script:MarcadorPainel | Out-Null

    Stop-SePendencias
}

Invoke-AutoSetup

$script:UseScout = Test-ScoutEnabled
$script:UseLlm = Test-LlmConfigured
$script:ScoutPublicPort = Get-EnvValue "SCOUT_PUBLIC_PORT" "4050"

Write-Host "[OK] Configuracoes de seguranca (.env) carregadas" -ForegroundColor Green
Write-Host "-------------------------------------------------" -ForegroundColor Gray

function ConvertTo-NodesExcludeJson {
    param([string]$Lista)
    $padrao = "n8n-nodes-base.executeCommand,n8n-nodes-base.ssh,n8n-nodes-base.readWriteFile,n8n-nodes-base.localFileTrigger,n8n-nodes-base.readBinaryFile,n8n-nodes-base.readBinaryFiles,n8n-nodes-base.writeBinaryFile"
    $texto = ""
    if ($Lista) { $texto = $Lista.Trim() }
    if (-not $texto) { $texto = $padrao }
    if ($texto.StartsWith("[")) { return $texto }
    $partes = @()
    foreach ($parte in $texto.Split(",")) {
        $nome = $parte.Trim()
        if ($nome) { $partes += $nome }
    }
    $itens = @()
    foreach ($nome in $partes) {
        $limpo = $nome.Replace('"', "").Replace("\", "")
        $itens += '"' + $limpo + '"'
    }
    return "[" + ($itens -join ",") + "]"
}

function Show-NodesExclude {
    $tentativa = 0
    while ($tentativa -lt 5) {
        $valor = & docker exec n8n_app printenv NODES_EXCLUDE 2>$null
        if ($LASTEXITCODE -eq 0 -and $valor) {
            Write-Host "[N8N] NODES_EXCLUDE=$valor" -ForegroundColor Gray
            return
        }
        $tentativa++
        Start-Sleep -Seconds 2
    }
    Write-Host "[AVISO] n8n_app nao devolveu NODES_EXCLUDE. A exclusao de nodes nao foi confirmada." -ForegroundColor Yellow
}

if ($Stack) {
    $script:acaoAvulsa = $true
    if (-not (Resolve-ComposeCommand)) {
        Write-Host "[ERRO] Docker nao esta no PATH. Abra o Docker Desktop e tente de novo." -ForegroundColor Red
        Exit 1
    }
    if (-not (Test-DockerEngine)) {
        Write-Host "[ERRO] O Docker esta no PATH, mas o motor nao respondeu. Abra o Docker Desktop e espere ele ficar no ar." -ForegroundColor Red
        Exit 1
    }
    if (-not (Ensure-RedeComunicacao)) {
        Write-Host "[ERRO] Nao consegui garantir a rede rede_comunicacao." -ForegroundColor Red
        Exit 1
    }
    $codigoAvulso = Invoke-StackApp -Id $Stack -AcaoStack $Acao
    if ($null -eq $codigoAvulso) { $codigoAvulso = 1 }
    Exit $codigoAvulso
}

try {
    Register-Supervisor
    # Arquivos de montagem antes de qualquer docker compose. Se o arquivo
    # nao existe, o Docker cria uma pasta no lugar e o Scout nao assina.
    Ensure-ArquivosMontados
    Ensure-ChaveUsuario
    Ensure-ChaveMaquina -Raiz $PSScriptRoot

    # =======================================================
    # 1. INICIALIZAÇÃO DOS SERVIÇOS
    # =======================================================
    # O venv do painel ja foi oferecido no auto-setup. Aqui so sobe o Streamlit
    # se esse venv existe; recusa (marcador) ou porta alheia nao mata ninguem.
    Start-ControlPlane
    # Ollama local e opcional. Nao grava modelo nem endereco no LiteLLM.
    Start-OllamaLocal
    Start-Porteiro
    Start-Sleep -Seconds 3

    # O Scout sobe antes do ngrok para o nome scout-backend existir na rede.
    # O ngrok fala com esse nome, nao com host.docker.internal (no Docker Desktop
    # esse salto chega como o gateway 172.18.0.1 e o Scout negava a visita).
    $script:ScoutNoAr = $false
    $script:ScoutMotivo = ""
    if ($script:UseScout) {
        Start-Scout
    }
    $portaScout = Get-PortaScoutEfetiva
    $planoNgrok = Plano-NgrokAposScout -UsarScout ([bool]$script:UseScout) -ScoutNoAr ([bool]$script:ScoutNoAr)
    if ($planoNgrok.Subir) {
        $env:NGROK_TUNNEL_HOST = $planoNgrok.Host
        $env:NGROK_TUNNEL_TARGET = Get-NgrokTunnelTarget
        Write-Host "[REDE] Ngrok upstream: $($env:NGROK_TUNNEL_HOST):$($env:NGROK_TUNNEL_TARGET)" -ForegroundColor Gray
        Write-Host "[+] Iniciando ngrok para obter a URL publica antes do n8n..." -ForegroundColor Yellow
        $codeNgrok = Invoke-Compose -ComposeFile $COMPOSE_NGROK -ComposeArgs @("up", "-d")
        $avisoNgrok = Completar-Ngrok -Codigo $codeNgrok
        if ($avisoNgrok) {
            Write-Host "[AVISO] $avisoNgrok" -ForegroundColor Yellow
        }
        if ($codeNgrok -eq 0) {
            $urlAntes = Get-EnvValue "SCOUT_NGROK_TUNNEL_URL" ""
            $urlPublica = Wait-NgrokPublicUrl
            if ($urlPublica) {
                Write-NgrokUrlNoAmbiente -Url $urlPublica
                $oldUrl = $urlPublica
                if ($script:UseScout -and $urlAntes -ne $urlPublica) {
                    Start-Scout
                }
            } else {
                Write-Host "[AVISO] O ngrok ainda nao devolveu a URL publica. O HUD aplica a URL no Scout quando ela aparecer. Se o n8n estiver no ar, o HUD tambem o recria. health: starting nessa hora e reinicio esperado, nao e falha." -ForegroundColor Yellow
            }
        }
    } else {
        Write-Host "[AVISO] Scout fora do ar. O ngrok nao aponta para scout-backend. Porta efetiva $portaScout. Motivo: $($script:ScoutMotivo)" -ForegroundColor Yellow
    }

    if ($script:UseScout -and $script:ScoutNoAr) {
        Sync-ScoutPorteiroRoute
    }

    $stacksBoot = Get-StacksBoot
    if ($null -eq $stacksBoot) { $stacksBoot = @() }
    $textoBoot = Texto-BootNucleo -UsarScout ([bool]$script:UseScout) -ScoutNoAr ([bool]$script:ScoutNoAr) -Motivo $script:ScoutMotivo -Porta "$portaScout" -Stacks @($stacksBoot)
    if ($textoBoot.StartsWith("Scout fora")) {
        Write-Host "[BOOT] $textoBoot" -ForegroundColor Yellow
    } else {
        Write-Host "[BOOT] $textoBoot" -ForegroundColor Green
    }

    if (@($stacksBoot) -contains "llm") {
        if ($script:UseLlm) {
            Write-Host "[+] Iniciando Langfuse e LiteLLM..." -ForegroundColor Yellow
            $codeLlm = Invoke-Compose -ComposeFile $COMPOSE_LLM -ComposeArgs @("up", "-d")
            if ($codeLlm -ne 0) { throw "docker compose up Langfuse/LiteLLM falhou (codigo $codeLlm)." }
        } else {
            Write-Host "[AVISO] STACKS_BOOT pede llm, mas faltam chaves no .env. Rode o Setup. O nucleo segue." -ForegroundColor Yellow
        }
    }

    if (@($stacksBoot) -contains "n8n") {
        if (-not $script:UseLlm -or -not (@($stacksBoot) -contains "llm")) {
            Write-Host "[INFO] A stack LLM esta fora do ar. O n8n sobe mesmo assim. A credencial OpenAI aponta para http://litellm:4000/v1 e so responde quando Langfuse e LiteLLM estiverem no ar. Isso nao e erro." -ForegroundColor Cyan
        }
        Write-Host "[+] Iniciando n8n..." -ForegroundColor Yellow
        Ensure-ArquivosN8n
        $env:NODES_EXCLUDE = ConvertTo-NodesExcludeJson (Get-EnvValue "N8N_NODES_EXCLUDE" "")
        $codeN8n = Invoke-Compose -ComposeFile $COMPOSE_N8N -ComposeArgs @("up", "-d")
        if ($codeN8n -ne 0) { throw "docker compose up n8n falhou (codigo $codeN8n)." }
        Show-NodesExclude
    }

    Write-Host "[SISTEMA] Aguardando estabilizacao dos servicos..." -ForegroundColor Gray
    Start-Sleep -Seconds 5
    Update-N8nContainerIp

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
                $scoutBruto = docker ps -a --filter "name=^scout-backend$" --format "{{.Status}}"
                $containerScout = $false
                $saudeScout = $false
                if ($scoutBruto -and ([string]$scoutBruto).StartsWith("Up")) {
                    $containerScout = $true
                    if (([string]$scoutBruto) -notmatch "health: starting") {
                        $saudeScout = [bool](Test-ScoutHealth)
                    }
                }
                $estadoScout = Estado-ScoutNoLoop -ContainerNoAr ([bool]$containerScout) -SaudeOk ([bool]$saudeScout)
                $script:ScoutNoAr = [bool]$estadoScout.NoAr
                $script:ScoutMotivo = [string]$estadoScout.Motivo
                $script:ScoutSaudeDesteQuadro = [bool]$saudeScout
                $linhaScout = Texto-HudScout -ScoutNoAr ([bool]$script:ScoutNoAr) -Motivo $script:ScoutMotivo -Porta (Get-PortaScoutEfetiva)
                $corModo = "Magenta"
                if (-not $script:ScoutNoAr) { $corModo = "Yellow" }
                Write-Host (' [MODO]    ' + $linhaScout) -ForegroundColor $corModo
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
                $scoutStatus = $scoutBruto
            }

            if (-not $n8nStatus)   { $n8nStatus   = "Nao Encontrado/Criado" }
            if (-not $ngrokStatus) { $ngrokStatus = "Nao Encontrado/Criado" }
            $n8nBruto = $n8nStatus
            $scoutBruto = $scoutStatus
            if ($n8nBruto -and ([string]$n8nBruto).StartsWith("Up")) {
                Update-N8nContainerIp
            }
            $corN8n   = Get-CorStatusContainer $n8nStatus
            $corNgrok = Get-CorStatusContainer $ngrokStatus
            $n8nStatus = Get-TextoStatusContainer $n8nStatus
            $ngrokStatus = Get-TextoStatusContainer $ngrokStatus
            $corScout = "Gray"

            Write-Host " [SERVICO] " -NoNewline -ForegroundColor White
            Write-Host "n8n_app " -ForegroundColor $corN8n -NoNewline
            Write-Host "-> Status: $n8nStatus" -ForegroundColor Gray

            Write-Host " [SERVICO] " -NoNewline -ForegroundColor White
            Write-Host "ngrok   " -ForegroundColor $corNgrok -NoNewline
            Write-Host "-> Status: $ngrokStatus" -ForegroundColor Gray

            if ($script:UseScout) {
                if (-not $scoutStatus) { $scoutStatus = "Nao Encontrado/Criado" }
                $corScout = Get-CorStatusContainer $scoutStatus
                $scoutStatus = Get-TextoStatusContainer $scoutStatus
                Write-Host " [SERVICO] " -NoNewline -ForegroundColor White
                Write-Host "scout   " -ForegroundColor $corScout -NoNewline
                Write-Host "-> Status: $scoutStatus" -ForegroundColor Gray
            }

            if ($script:UseLlm) {
                $langfuseStatus = docker ps -a --filter "name=^langfuse-web$" --format "{{.Status}}"
                $litellmStatus  = docker ps -a --filter "name=^litellm$" --format "{{.Status}}"
                if (-not $langfuseStatus) { $langfuseStatus = "Nao Encontrado/Criado" }
                if (-not $litellmStatus)  { $litellmStatus  = "Nao Encontrado/Criado" }
                $corLf = Get-CorStatusContainer $langfuseStatus
                $corLl = Get-CorStatusContainer $litellmStatus
                $langfuseStatus = Get-TextoStatusContainer $langfuseStatus
                $litellmStatus = Get-TextoStatusContainer $litellmStatus
                Write-Host " [SERVICO] " -NoNewline -ForegroundColor White
                Write-Host "langfuse" -ForegroundColor $corLf -NoNewline
                Write-Host " -> Status: $langfuseStatus" -ForegroundColor Gray
                Write-Host " [SERVICO] " -NoNewline -ForegroundColor White
                Write-Host "litellm " -ForegroundColor $corLl -NoNewline
                Write-Host " -> Status: $litellmStatus" -ForegroundColor Gray
            }

            if ($script:ReinicioPorUrl) {
                $n8nOk = ($n8nBruto -match "Up") -and ($n8nBruto -notmatch "health: starting")
                $scoutOk = $true
                if ($script:UseScout) {
                    $scoutOk = ($scoutBruto -match "Up") -and ($scoutBruto -notmatch "health: starting")
                }
                if ($n8nOk -and $scoutOk) { $script:ReinicioPorUrl = $false }
            }

            Write-StatusPainel
            Write-StatusOllama
            Write-StatusDockerDono
            Write-Host "-------------------------------------------------" -ForegroundColor Gray

            # ---------------------------------------------------
            # HUD SCOUT (rotas activas)
            # ---------------------------------------------------
            if ($script:UseScout) {
                try {
                    if ($script:ScoutSaudeDesteQuadro) {
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
                    $env:NGROK_REMOTE_URL = $currentUrl
                    Set-EnvValue -Key "SCOUT_NGROK_TUNNEL_URL" -Value $currentUrl
                    $recriaN8n = $false
                    if (@($stacksBoot) -contains "n8n") {
                        $recriaN8n = $true
                    } else {
                        $n8nExiste = docker ps -a --filter "name=^n8n_app$" --format "{{.Names}}"
                        if ($n8nExiste) { $recriaN8n = $true }
                    }
                    $quem = @()
                    if ($recriaN8n) { $quem += "o n8n" }
                    if ($script:UseScout) { $quem += "o Scout" }
                    if ($quem.Count -gt 0) {
                        $listaQuem = $quem -join " e "
                        Write-Host "[ATUALIZANDO] A URL publica do ngrok mudou. Recriando $listaQuem para aplicar. health: starting em seguida e reinicio esperado, nao e falha." -ForegroundColor Yellow
                    } else {
                        Write-Host "[ATUALIZANDO] A URL publica do ngrok mudou. Scout desligado e n8n fora: nenhum app foi recriado. health: starting em seguida e reinicio esperado, nao e falha." -ForegroundColor Yellow
                    }
                    if ($recriaN8n) {
                        Ensure-ArquivosN8n
                        $env:NODES_EXCLUDE = ConvertTo-NodesExcludeJson (Get-EnvValue "N8N_NODES_EXCLUDE" "")
                        Invoke-Compose -ComposeFile $COMPOSE_N8N -ComposeArgs @("up", "-d") | Out-Null
                    }
                    if ($script:UseScout) {
                        Ensure-ArquivosMontados
                        Ensure-SessaoChave
                        Invoke-Compose -ComposeFile $COMPOSE_SCOUT -ComposeArgs @("up", "-d") | Out-Null
                    }
                    $oldUrl = $currentUrl
                    if ($recriaN8n -or $script:UseScout) { $script:ReinicioPorUrl = $true }
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
            Write-Host 'Pressione G para abrir o painel (Scout), Q para encerrar o nucleo e as stacks que estiverem no ar (Ollama e Docker Desktop so fecham se este script os abriu)' -ForegroundColor Yellow

            for ($i = 0; $i -lt 50; $i++) {
                if (Test-ShutdownRequest) {
                    $exitRequested = $true
                    break
                }
                $tecla = Read-TeclaJanela
                if ($tecla -eq 81) {
                    $exitRequested = $true
                    break
                }
                if ($tecla -eq 71) {
                    Open-PainelScout
                    Start-Sleep -Milliseconds 400
                    break
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
    $script:encerramentoPorFalha = $true
    Write-Host ""
    Write-Host "[ERRO FATAL] $($_.Exception.Message)" -ForegroundColor Red -BackgroundColor Black
    Write-Host "A infraestrutura sera encerrada. A mensagem acima permanece na tela." -ForegroundColor Yellow
} finally {
    Stop-Tudo
}
