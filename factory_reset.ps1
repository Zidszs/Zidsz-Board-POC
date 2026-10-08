# N8Groker - factory reset (apaga dados volatil; mantem codigo e templates).
# Invocado por factory_reset.bat apos confirmacao Excluir.
param(
    [switch]$CreateEnvFromTemplate
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot

function Write-Step {
    param([string]$Message)
    Write-Host "[*] $Message" -ForegroundColor Yellow
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Skip {
    param([string]$Message)
    Write-Host "[--] $Message" -ForegroundColor DarkGray
}

function Invoke-ComposeDown {
    param(
        [Parameter(Mandatory = $true)][string]$ComposeFile,
        [string]$EnvFile
    )
    if (-not (Test-Path $ComposeFile)) {
        Write-Skip "Compose nao encontrado: $ComposeFile"
        return
    }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        if ($EnvFile -and (Test-Path $EnvFile)) {
            & docker-compose -f $ComposeFile --env-file $EnvFile down 2>$null | Out-Null
        } else {
            & docker-compose -f $ComposeFile down 2>$null | Out-Null
        }
        Write-Ok "docker compose down: $(Split-Path (Split-Path $ComposeFile -Parent) -Leaf)"
    } catch {
        Write-Skip "docker compose down falhou (Docker offline?): $ComposeFile"
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Remove-TreeContents {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path $Path)) {
        Write-Skip "Nao existe: $Path"
        return
    }
    Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok "Limpo: $Path"
}

function Remove-ItemSafe {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
        Write-Ok "Removido: $Path"
    } else {
        Write-Skip "Nao existe: $Path"
    }
}

function Remove-LogsVolateis {
    # Arquivos-n8n e do usuario. A varredura de *.log nao entra nela.
    $pasta = [System.IO.Path]::GetFullPath((Join-Path $Root "Arquivos-n8n"))
    $sep = [System.IO.Path]::DirectorySeparatorChar
    $prefixo = $pasta.TrimEnd('\', '/') + $sep
    Get-ChildItem -Path $Root -Recurse -File -Filter "*.log" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $full = [System.IO.Path]::GetFullPath($_.FullName)
            if ($full.StartsWith($prefixo, [System.StringComparison]::OrdinalIgnoreCase)) {
                return
            }
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
            Write-Host ('     log: ' + $_.FullName) -ForegroundColor DarkGray
        }
}

function Ensure-GitKeep {
    param([Parameter(Mandatory = $true)][string]$Dir)
    if (-not (Test-Path $Dir)) {
        New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    }
    $keep = Join-Path $Dir ".gitkeep"
    # 0 bytes, igual ao ficheiro versionado. Set-Content -Encoding UTF8 no
    # Windows PowerShell 5.1 grava BOM e CRLF, e o git status fica sujo.
    [System.IO.File]::WriteAllBytes($keep, [byte[]]@())
    Write-Ok "Scaffold: $Dir"
}

Write-Host ""
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host " N8GROKER - FACTORY RESET" -ForegroundColor Cyan
Write-Host " Pasta: $Root" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

Write-Step "Parando Porteiro (Node)..."
$pidFile = Join-Path $Root ".porteiro.pid"
if (Test-Path $pidFile) {
    $pidVal = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($pidVal) {
        $proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -eq "node") {
            Stop-Process -Id $pidVal -Force -ErrorAction SilentlyContinue
            Write-Ok "Porteiro encerrado (PID $pidVal)"
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Skip "Sem .porteiro.pid"
}

Write-Step "Parando containers Docker..."
$envFile = Join-Path $Root ".env"
$composeFiles = @(
    (Join-Path $Root "Scout_OSINT_Docker\docker-compose.yml"),
    (Join-Path $Root "ngrok\docker-compose.yml"),
    (Join-Path $Root "n8n\docker-compose.yml")
)
foreach ($cf in $composeFiles) {
    Invoke-ComposeDown -ComposeFile $cf -EnvFile $envFile
}

Write-Step "Parando Langfuse/LiteLLM e removendo os volumes nomeados dessa stack..."
$llmCompose = Join-Path $Root "llm\docker-compose.yml"
if (Test-Path $llmCompose) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        if (Test-Path $envFile) {
            & docker-compose -f $llmCompose --env-file $envFile down -v 2>$null | Out-Null
        } else {
            & docker-compose -f $llmCompose down -v 2>$null | Out-Null
        }
        Write-Ok "volumes Langfuse/LiteLLM removidos (down -v)"
    } catch {
        Write-Skip "down -v do llm falhou (Docker offline?)"
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

Write-Step "Apagando dados volatil..."

Remove-ItemSafe (Join-Path $Root ".env")
Remove-ItemSafe (Join-Path $Root ".n8groker.supervisor.lock")
Remove-ItemSafe (Join-Path $Root ".n8groker.shutdown.request")
Remove-ItemSafe (Join-Path $Root "Porteiro\banco_acesso.db")
Remove-TreeContents (Join-Path $Root "n8n\n8n\data")
Remove-TreeContents (Join-Path $Root "n8n\data")
Remove-TreeContents (Join-Path $Root "n8n\storage")
Remove-TreeContents (Join-Path $Root "Scout_OSINT_Docker\data")
Remove-ItemSafe (Join-Path $Root "Scout_OSINT_Docker\.venv")

Write-Step "Apagando __pycache__ e *.log..."
Get-ChildItem -Path $Root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
Remove-LogsVolateis

Write-Step "Recriando pastas vazias..."
Ensure-GitKeep (Join-Path $Root "n8n\n8n\data")
Ensure-GitKeep (Join-Path $Root "n8n\storage\Porteiro")
Write-Host "[--] Arquivos-n8n preservada (arquivos do usuario; o reset nao apaga)." -ForegroundColor DarkGray

if ($CreateEnvFromTemplate) {
    $example = Join-Path $Root ".env.example"
    $scriptPy = Join-Path $Root "scripts\init_env.py"
    $py = $null
    if (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
    if ($py -and (Test-Path $scriptPy)) {
        if ($py -eq "py") { & py -3 $scriptPy } else { & python $scriptPy }
        if ($LASTEXITCODE -eq 0) {
            Write-Ok ".env recriado com segredos aleatorios (init_env.py)"
        } else {
            Write-Host "[AVISO] init_env.py falhou." -ForegroundColor Yellow
        }
    } elseif (Test-Path $example) {
        Copy-Item -LiteralPath $example -Destination (Join-Path $Root ".env") -Force
        Write-Host "[AVISO] Python ausente: .env copiado com marcadores. Rode python scripts/init_env.py." -ForegroundColor Yellow
    } else {
        Write-Host '[AVISO] .env.example nao encontrado.' -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=================================================" -ForegroundColor Green
Write-Host " FACTORY RESET CONCLUIDO" -ForegroundColor Green
Write-Host "=================================================" -ForegroundColor Green
Write-Host ""
Write-Host 'Estado: projeto pronto para primeira execucao (estilo GitHub).'
Write-Host ""
if (-not $CreateEnvFromTemplate) {
    Write-Host 'Proximo passo: rode o Setup ou python scripts/init_env.py e preencha o NGROK_AUTHTOKEN.'
} else {
    Write-Host 'Proximo passo: preencha NGROK_AUTHTOKEN no .env. Os outros segredos ja foram gerados.'
}
Write-Host ""
Write-Host '  1. docker network create rede_comunicacao   (se ainda nao existir)'
Write-Host '  2. .\iniciar_servicos.ps1'
Write-Host '  3. Importar Aprovacao de Acesso (Novo).json no n8n'
Write-Host ""
