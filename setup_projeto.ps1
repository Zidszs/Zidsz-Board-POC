# N8Groker — setup interactivo de dependencias (nao inicia servicos).
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$PathEnv = Join-Path $Root ".env"
$PathEnvTemplate = Join-Path $Root ".env_template"
$PathScout = Join-Path $Root "Scout_OSINT_Docker"
$ReqScout = Join-Path $PathScout "requirements.txt"

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

function Test-ScoutEnabled {
    $flag = Get-EnvValue "USE_SCOUT" "1"
    if (-not (Test-Path $PathEnv)) {
        if (Test-Path $PathEnvTemplate) {
            foreach ($line in Get-Content $PathEnvTemplate -Encoding UTF8) {
                if ($line -match "^\s*USE_SCOUT\s*=\s*(.*)$") {
                    $flag = $Matches[1].Trim()
                    break
                }
            }
        }
    }
    return ($flag -match "^(1|true|yes|sim|on)$")
}

function Find-ScoutPython {
    $venv = Join-Path $PathScout ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return @{ Exe = $venv; UsePyLauncher = $false } }
    if (Get-Command py -ErrorAction SilentlyContinue) { return @{ Exe = "py"; UsePyLauncher = $true } }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @{ Exe = "python"; UsePyLauncher = $false } }
    return $null
}

function Invoke-AutoScoutPython {
    $info = Find-ScoutPython
    if (-not $info) {
        Write-Host "    Python nao encontrado. Instale Python 3.10+ e escolha Reverificar." -ForegroundColor Yellow
        return
    }
    $venvPy = Join-Path $PathScout ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Host "    A criar .venv em Scout_OSINT_Docker..." -ForegroundColor Gray
        if ($info.UsePyLauncher) { & py -3 -m venv (Join-Path $PathScout ".venv") }
        else { & $info.Exe -m venv (Join-Path $PathScout ".venv") }
    }
    $py = Join-Path $PathScout ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Host "    Falha ao criar venv." -ForegroundColor Red
        return
    }
    & $py -c "import requests, websocket" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    A instalar requirements.txt..." -ForegroundColor Gray
        & $py -m pip install --no-cache-dir -r $ReqScout
    }
    Write-Host "    Scout Python OK." -ForegroundColor Green
}

function Test-EnvKeysFilled {
    if (-not (Test-Path $PathEnv)) { return $false }
    $ngrok = Get-EnvValue "NGROK_AUTHTOKEN" ""
    $n8nKey = Get-EnvValue "N8N_ENCRYPTION_KEY" ""
    $porteiroToken = Get-EnvValue "PORTEIRO_TOKEN" ""
    $badNgrok = @("", "seu_token_do_ngrok_aqui", "your_ngrok_token")
    $badN8n = @("", "sua_chave_secreta_aqui", "your_secret_key")
    $badPorteiro = @("", "seu_token_porteiro_aqui", "your_porteiro_token")
    if ($badNgrok -contains $ngrok) { return $false }
    if ($badN8n -contains $n8nKey) { return $false }
    if ($badPorteiro -contains $porteiroToken) { return $false }
    return $true
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

function Test-ScoutPythonOk {
    if (-not (Test-ScoutEnabled)) { return $true }
    $py = Join-Path $PathScout ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { return $false }
    & $py -c "import requests, websocket" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Test-DataDirsOk {
    $a = Join-Path $Root "n8n\n8n\data"
    $b = Join-Path $Root "n8n\storage\Porteiro"
    return ((Test-Path $a) -and (Test-Path $b))
}

function Test-PortsFree {
    $ports = @(5677, 5678, 4040, 4050, 8765)
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
            $empty = New-Object byte[] 0
            [System.IO.File]::WriteAllBytes($keep, $empty)
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

        if ($Item.Id -eq "scout_python" -and -not (Test-ScoutEnabled)) {
            $script:Results[$Item.Id] = @{ Status = "OK"; Detail = "USE_SCOUT=0 (nao necessario)" }
            return
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
        Id     = "env_file"
        Name   = "Ficheiro .env"
        Url    = $null
        CanAuto = $true
        Test   = { Test-Path $PathEnv }
        Auto   = {
            if (Test-Path $PathEnvTemplate) {
                Copy-Item -LiteralPath $PathEnvTemplate -Destination $PathEnv -Force
                Write-Host "    .env criado a partir de .env_template" -ForegroundColor Green
            } else {
                Write-Host "    .env_template nao encontrado." -ForegroundColor Red
            }
        }
    },
    @{
        Id     = "env_keys"
        Name   = "Chaves .env (NGROK + N8N + PORTEIRO_TOKEN)"
        Url    = "https://dashboard.ngrok.com/get-started/your-authtoken"
        CanAuto = $true
        Test   = { Test-EnvKeysFilled }
        Auto   = {
            if (Test-Path $PathEnv) {
                # PS 5.1 reparte ArgumentList nos espaços; aspas mantêm o caminho inteiro.
                Start-Process notepad.exe -ArgumentList "`"$PathEnv`""
                Write-Host "    .env aberto no Notepad. Preencha NGROK_AUTHTOKEN, N8N_ENCRYPTION_KEY e PORTEIRO_TOKEN." -ForegroundColor Gray
            } else {
                Write-Host "    Crie .env primeiro (item anterior)." -ForegroundColor Yellow
            }
        }
    },
    @{
        Id     = "docker_install"
        Name   = "Docker instalado"
        Url    = "https://www.docker.com/products/docker-desktop/"
        CanAuto = $false
        Test   = { [bool](Get-Command docker -ErrorAction SilentlyContinue) }
        Auto   = { }
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
        Id     = "scout_python"
        Name   = "Python + deps Scout (USE_SCOUT=1)"
        Url    = "https://www.python.org/downloads/"
        CanAuto = $true
        Test   = { Test-ScoutPythonOk }
        Auto   = { Invoke-AutoScoutPython }
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
        Name   = "Portas livres (5677,5678,4040,4050,8765)"
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

$criticalIds = @("env_file", "env_keys", "docker_install", "docker_running", "docker_network", "nodejs")
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
