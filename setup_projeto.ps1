# N8Groker — setup interactivo de dependencias (nao inicia servicos).
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$PathEnv = Join-Path $Root ".env"
$PathEnvExample = Join-Path $Root ".env.example"
$PathEnvTemplate = Join-Path $Root ".env_template"
$script:Results = @{}

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [string]$Default = ""
    )
    if (-not (Test-Path $PathEnv)) { return $Default }
    foreach ($line in Get-Content $PathEnv -Encoding UTF8) {
        if ($line -match "^\s*#") { continue }
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*)$") {
            return $Matches[1].Trim()
        }
    }
    return $Default
}

function Test-SecretReady {
    param([string]$Value)
    if (-not $Value) { return $false }
    if ($Value -like "__GENERATE_*") { return $false }
    $bad = @(
        "sua_chave_secreta_aqui",
        "your_secret_key",
        "seu_token_porteiro_aqui",
        "your_porteiro_token"
    )
    return -not ($bad -contains $Value)
}

function Test-EnvKeysFilled {
    if (-not (Test-Path $PathEnv)) { return @{ Ok = $false; Detail = ".env ausente" } }
    $pending = @()
    $ngrok = Get-EnvValue "NGROK_AUTHTOKEN" ""
    if (@("", "seu_token_do_ngrok_aqui", "your_ngrok_token") -contains $ngrok) { $pending += "NGROK_AUTHTOKEN" }
    foreach ($key in @("N8N_ENCRYPTION_KEY", "LITELLM_MASTER_KEY", "LANGFUSE_PUBLIC_KEY", "ENCRYPTION_KEY")) {
        if (-not (Test-SecretReady (Get-EnvValue $key ""))) { $pending += $key }
    }
    if ($pending.Count -eq 0) { return @{ Ok = $true; Detail = "OK" } }
    return @{ Ok = $false; Detail = ("Pendente: " + ($pending -join ", ")) }
}

function Test-DockerRunning {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    docker info 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

function Test-DockerNetwork {
    if (-not (Test-DockerRunning)) { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    docker network inspect rede_comunicacao 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

function Test-NodeOk {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { return $false }
    $v = node -v 2>$null
    if (-not $v) { return $false }
    if ($v -match "v(\d+)") {
        return ([int]$Matches[1] -ge 16)
    }
    return $true
}

function Test-DataDirsOk {
    $a = Join-Path $Root "n8n\n8n\data"
    $b = Join-Path $Root "n8n\storage\Porteiro"
    return ((Test-Path $a) -and (Test-Path $b))
}

function Test-PortsFree {
    $ports = @(5677, 5678, 4040, 4050, 8765, 3000, 3030, 4000, 9090)
    $busy = @()
    foreach ($p in $ports) {
        try {
            $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
            if ($c) { $busy += $p }
        } catch { }
    }
    if ($busy.Count -eq 0) { return @{ Ok = $true; Detail = "Todas livres" } }
    return @{ Ok = $false; Detail = "Em uso: $($busy -join ', ')" }
}

function Test-WslOk {
    if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $out = wsl --status 2>&1 | Out-String
    $ErrorActionPreference = $prev
    if ($out -match "not installed|nao instalado|WSL is not") { return $false }
    return $true
}

function Test-ExecutionPolicyOk {
    $pol = Get-ExecutionPolicy -Scope CurrentUser
    return ($pol -in @("RemoteSigned", "Unrestricted", "Bypass"))
}

function Find-HostPython {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += @{ Exe = "py"; Prefix = @("-3") }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $candidates += @{ Exe = "python"; Prefix = @() }
    }
    foreach ($c in $candidates) {
        $argList = @()
        $argList += $c.Prefix
        $argList += @("-c", "import sys; print('%d.%d' % (sys.version_info[0], sys.version_info[1]))")
        $raw = & $c.Exe @argList 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) { continue }
        $parts = "$raw".Trim().Split(".")
        if ($parts.Count -lt 2) { continue }
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
            return @{ Exe = $c.Exe; Prefix = $c.Prefix; Version = "$raw".Trim() }
        }
    }
    return $null
}

function Refresh-SessionPath {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Confirm-Install {
    param([string]$Prompt)
    $answer = Read-Host "    $Prompt (S/N)"
    return ($answer -match "^(S|s|Y|y)$")
}

function Invoke-InitEnv {
    $py = Find-HostPython
    if (-not $py) {
        Write-Host "    Python 3.10+ necessario para gerar o .env." -ForegroundColor Yellow
        return
    }
    $argList = @()
    $argList += $py.Prefix
    $argList += (Join-Path $Root "scripts\init_env.py")
    & $py.Exe @argList
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    init_env.py falhou." -ForegroundColor Red
    }
}

function Install-WithWinget {
    param(
        [string]$Id,
        [string]$Label
    )
    if (-not (Confirm-Install "Instalar $Label com winget?")) {
        Write-Host "    Instalacao cancelada." -ForegroundColor Yellow
        return
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "    winget nao encontrado. Use o link de documentacao deste item." -ForegroundColor Yellow
        return
    }
    winget install -e --id $Id --accept-package-agreements --accept-source-agreements
    Refresh-SessionPath
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
    $dirs = @(
        (Join-Path $Root "n8n\n8n\data"),
        (Join-Path $Root "n8n\storage\Porteiro")
    )
    foreach ($d in $dirs) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
        $keep = Join-Path $d ".gitkeep"
        if (-not (Test-Path -LiteralPath $keep)) {
            [System.IO.File]::WriteAllBytes($keep, [byte[]]@())
        }
    }
    Write-Host "    Pastas de dados criadas." -ForegroundColor Green
    Ensure-ArquivosN8n
}

function Show-ItemMenu {
    param(
        [string]$Name,
        [string]$Detail,
        [bool]$CanAuto,
        [string]$Url
    )
    Write-Host ""
    Write-Host "  --- $Name ---" -ForegroundColor Cyan
    Write-Host "  Estado: $Detail" -ForegroundColor Yellow
    if ($Url) { Write-Host "  Ajuda:  $Url" -ForegroundColor DarkGray }
    Write-Host ""
    if ($CanAuto) {
        Write-Host "  [1] Configurar automaticamente"
    } else {
        Write-Host "  [1] (auto indisponivel para este item)"
    }
    Write-Host "  [2] Abrir site / documentacao no browser"
    Write-Host "  [3] Ignorar por agora (continuar checklist)"
    Write-Host "  [4] Reverificar este item"
    Write-Host ""
}

function Invoke-DependencyLoop {
    param(
        [hashtable]$Item
    )

    while ($true) {
        $testResult = & $Item.Test
        $ok = $false
        $detail = ""

        if ($testResult -is [hashtable]) {
            $ok = [bool]$testResult.Ok
            $detail = [string]$testResult.Detail
        } else {
            $ok = [bool]$testResult
            $detail = if ($ok) { "OK" } else { "Pendente" }
        }

        if ($ok) {
            $script:Results[$Item.Id] = @{ Status = "OK"; Detail = $detail }
            Write-Host "  [OK] $($Item.Name) - $detail" -ForegroundColor Green
            return
        }

        Show-ItemMenu -Name $Item.Name -Detail $detail -CanAuto $Item.CanAuto -Url $Item.Url
        $choice = Read-Host "  Opcao"

        switch ($choice) {
            "1" {
                if ($Item.CanAuto -and $Item.Auto) {
                    & $Item.Auto
                } else {
                    Write-Host "    Auto-config nao disponivel. Use [2] para abrir o site." -ForegroundColor Yellow
                }
            }
            "2" {
                if ($Item.Url) {
                    Start-Process $Item.Url
                    Write-Host "    Browser aberto." -ForegroundColor Gray
                } else {
                    Write-Host "    Sem URL para este item." -ForegroundColor Yellow
                }
            }
            "3" {
                $script:Results[$Item.Id] = @{ Status = "IGNORADO"; Detail = $detail }
                Write-Host "  [--] $($Item.Name) ignorado por agora." -ForegroundColor DarkYellow
                return
            }
            "4" { continue }
            default {
                Write-Host "    Opcao invalida." -ForegroundColor Yellow
            }
        }
    }
}

# --- Checklist ---
$checklist = @(
    @{
        Id     = "exec_policy"
        Name   = "PowerShell (ExecutionPolicy)"
        Url    = "https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_execution_policies"
        CanAuto = $true
        Test   = { Test-ExecutionPolicyOk }
        Auto   = {
            Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
            Write-Host "    ExecutionPolicy = RemoteSigned (CurrentUser)" -ForegroundColor Green
        }
    },
    @{
        Id     = "python_host"
        Name   = "Python 3.10+ (Control Plane e geracao do .env)"
        Url    = "https://www.python.org/downloads/"
        CanAuto = $true
        Test   = {
            $py = Find-HostPython
            if ($py) { return @{ Ok = $true; Detail = $py.Version } }
            return @{ Ok = $false; Detail = "Python 3.10+ ausente" }
        }
        Auto   = { Install-WithWinget -Id "Python.Python.3.12" -Label "Python 3.12" }
    },
    @{
        Id     = "env_file"
        Name   = "Arquivo .env (segredos gerados, sem valor real no Git)"
        Url    = $null
        CanAuto = $true
        Test   = { Test-Path $PathEnv }
        Auto   = { Invoke-InitEnv }
    },
    @{
        Id     = "env_keys"
        Name   = "Chaves .env (ngrok manual + segredos gerados)"
        Url    = "https://dashboard.ngrok.com/get-started/your-authtoken"
        CanAuto = $true
        Test   = { Test-EnvKeysFilled }
        Auto   = {
            Invoke-InitEnv
            if (Test-Path $PathEnv) {
                # PS 5.1 reparte ArgumentList nos espaços; aspas mantêm o caminho inteiro.
                Start-Process notepad.exe -ArgumentList "`"$PathEnv`""
                Write-Host "    .env aberto. Preencha so o NGROK_AUTHTOKEN se ele ainda for placeholder." -ForegroundColor Gray
            } else {
                Write-Host "    Crie .env primeiro (item anterior)." -ForegroundColor Yellow
            }
        }
    },
    @{
        Id     = "docker_install"
        Name   = "Docker instalado"
        Url    = "https://www.docker.com/products/docker-desktop/"
        CanAuto = $true
        Test   = { [bool](Get-Command docker -ErrorAction SilentlyContinue) }
        Auto   = { Install-WithWinget -Id "Docker.DockerDesktop" -Label "Docker Desktop" }
    },
    @{
        Id     = "docker_running"
        Name   = "Docker a correr"
        Url    = "https://www.docker.com/products/docker-desktop/"
        CanAuto = $false
        Test   = { Test-DockerRunning }
        Auto   = {
            Write-Host "    Abra o Docker Desktop e aguarde o icone verde." -ForegroundColor Gray
            Write-Host "    Depois escolha [4] Reverificar." -ForegroundColor Gray
        }
    },
    @{
        Id     = "wsl"
        Name   = "WSL 2 (recomendado para Docker)"
        Url    = "https://learn.microsoft.com/windows/wsl/install"
        CanAuto = $false
        Test   = { Test-WslOk }
        Auto   = { }
    },
    @{
        Id     = "docker_network"
        Name   = "Rede Docker rede_comunicacao"
        Url    = $null
        CanAuto = $true
        Test   = { Test-DockerNetwork }
        Auto   = {
            if (-not (Test-DockerRunning)) {
                Write-Host "    Docker offline. Inicie o Docker Desktop primeiro." -ForegroundColor Yellow
                return
            }
            docker network create rede_comunicacao 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "    Rede rede_comunicacao criada." -ForegroundColor Green
            } else {
                Write-Host "    Rede ja existe ou falhou (codigo $LASTEXITCODE)." -ForegroundColor Gray
            }
        }
    },
    @{
        Id     = "nodejs"
        Name   = "Node.js (LTS, v16+)"
        Url    = "https://nodejs.org/"
        CanAuto = $false
        Test   = { Test-NodeOk }
        Auto   = { }
    },
    @{
        Id     = "data_dirs"
        Name   = "Pastas de dados (n8n + Porteiro)"
        Url    = $null
        CanAuto = $true
        Test   = { Test-DataDirsOk }
        Auto   = { Ensure-DataDirs }
    },
    @{
        Id     = "ports"
        Name   = "Portas livres (5677,5678,4040,4050,8765,3000,3030,4000,9090)"
        Url    = $null
        CanAuto = $false
        Test   = {
            $r = Test-PortsFree
            if (-not $r.Ok) { return @{ Ok = $false; Detail = $r.Detail } }
            return @{ Ok = $true; Detail = $r.Detail }
        }
        Auto   = {
            Write-Host "    Feche o processo que usa a porta ou altere portas no .env." -ForegroundColor Gray
        }
    },
    @{
        Id     = "control_plane"
        Name   = "Dependencias Python do Control Plane"
        Url    = $null
        CanAuto = $true
        Test   = {
            $py = Join-Path $Root "control_plane\.venv\Scripts\python.exe"
            if (-not (Test-Path $py)) { return @{ Ok = $false; Detail = "venv ausente" } }
            & $py -c "import streamlit" 2>$null
            if ($LASTEXITCODE -eq 0) { return @{ Ok = $true; Detail = "streamlit OK" } }
            return @{ Ok = $false; Detail = "streamlit ausente no venv" }
        }
        Auto   = {
            $hostPy = Find-HostPython
            if (-not $hostPy) {
                Write-Host "    Instale Python 3.10+ antes." -ForegroundColor Yellow
                return
            }
            $venv = Join-Path $Root "control_plane\.venv"
            $argList = @()
            $argList += $hostPy.Prefix
            $argList += @("-m", "venv", $venv)
            & $hostPy.Exe @argList
            $venvPy = Join-Path $venv "Scripts\python.exe"
            if (-not (Test-Path $venvPy)) {
                Write-Host "    Falha ao criar o venv do Control Plane." -ForegroundColor Red
                return
            }
            & $venvPy -m pip install --no-cache-dir -r (Join-Path $Root "control_plane\requirements.txt")
        }
    },
    @{
        Id     = "llm_images"
        Name   = "Imagens Docker do Langfuse e do LiteLLM"
        Url    = $null
        CanAuto = $true
        Test   = {
            if (-not (Test-DockerRunning)) { return @{ Ok = $false; Detail = "Docker offline" } }
            $images = @(
                "langfuse/langfuse:4.30.0",
                "langfuse/langfuse-worker:4.30.0",
                "ghcr.io/berriai/litellm:v1.103.1"
            )
            foreach ($img in $images) {
                docker image inspect $img 2>$null | Out-Null
                if ($LASTEXITCODE -ne 0) { return @{ Ok = $false; Detail = "falta $img" } }
            }
            return @{ Ok = $true; Detail = "imagens presentes" }
        }
        Auto   = {
            if (-not (Test-Path $PathEnv)) {
                Write-Host "    Crie o .env antes do pull (os compose exigem as variaveis)." -ForegroundColor Yellow
                return
            }
            if (-not (Confirm-Install "Baixar as imagens do Langfuse e do LiteLLM?")) {
                Write-Host "    Pull cancelado." -ForegroundColor Yellow
                return
            }
            $compose = Join-Path $Root "llm\docker-compose.yml"
            if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
                & docker-compose -f $compose --env-file $PathEnv pull
            } else {
                & docker compose -f $compose --env-file $PathEnv pull
            }
        }
    }
)

# --- Main ---
Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host " N8GROKER - SETUP DO PROJETO" -ForegroundColor Cyan
Write-Host " Pasta: $Root" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Verificacao completa em uma sessao. Por item em falta:"
Write-Host "  [1] Auto-config  [2] Abrir link  [3] Ignorar  [4] Reverificar"
Write-Host ""

foreach ($item in $checklist) {
    Invoke-DependencyLoop -Item $item
}

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host " RESUMO" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan

$criticalIds = @("python_host", "env_file", "env_keys", "docker_install", "docker_running", "docker_network", "nodejs")
$hasCriticalFail = $false

foreach ($item in $checklist) {
    if (-not $script:Results.ContainsKey($item.Id)) {
        if (& $item.Test) {
            $script:Results[$item.Id] = @{ Status = "OK"; Detail = "OK" }
        } else {
            $script:Results[$item.Id] = @{ Status = "FALTA"; Detail = "Nao verificado" }
        }
    }
    $r = $script:Results[$item.Id]
    $color = switch ($r.Status) {
        "OK" { "Green"; break }
        "IGNORADO" { "DarkYellow"; break }
        default { "Red" }
    }
    Write-Host ("  [{0}] {1} - {2}" -f $r.Status, $item.Name, $r.Detail) -ForegroundColor $color
    if ($criticalIds -contains $item.Id -and $r.Status -ne "OK") {
        $hasCriticalFail = $true
    }
}

Write-Host ""
if ($hasCriticalFail) {
    Write-Host "[AVISO] Itens criticos em falta. iniciar_servicos.ps1 pode falhar." -ForegroundColor Yellow
}

$launch = Read-Host "Iniciar servicos agora com iniciar_servicos.ps1? (S/N)"
if ($launch -match "^(S|s|Y|y)$") {
    $starter = Join-Path $Root "iniciar_servicos.ps1"
    if (Test-Path $starter) {
        Write-Host ""
        Write-Host "A iniciar servicos..." -ForegroundColor Green
        & powershell -NoProfile -ExecutionPolicy Bypass -File $starter
    } else {
        Write-Host "iniciar_servicos.ps1 nao encontrado." -ForegroundColor Red
    }
} else {
    Write-Host ""
    Write-Host 'Proximo passo manual: .\iniciar_servicos.ps1' -ForegroundColor Gray
    Write-Host "Importar Aprovacao de Acesso (Novo).json no n8n apos primeiro boot." -ForegroundColor Gray
}

Write-Host ""
