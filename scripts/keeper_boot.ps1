# Chave de maquina e PID do Keeper. Sem porta, fora do .env e de volume Docker.
# Dot-source a partir de iniciar_servicos.ps1. A raiz e a pasta do projeto, nao a de scripts/.

function Get-LinhaDeComandoKeeper {
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

function Ensure-ChaveMaquina {
    param([Parameter(Mandatory = $true)][string]$Raiz)
    if ([string]::IsNullOrWhiteSpace($Raiz)) {
        throw "Raiz vazia. maquina.key nao foi gravada."
    }
    $pasta = Join-Path $Raiz ".n8groker"
    if (-not (Test-Path -LiteralPath $pasta)) {
        New-Item -ItemType Directory -Path $pasta | Out-Null
    }
    $arquivo = Join-Path $pasta "maquina.key"
    if (Test-Path -LiteralPath $arquivo -PathType Container) {
        throw "maquina.key e uma pasta. Nada foi apagado. Remova a pasta e rode de novo."
    }
    $gravar = $true
    if (Test-Path -LiteralPath $arquivo -PathType Leaf) {
        $info = Get-Item -LiteralPath $arquivo
        if ($info.Length -gt 0) { $gravar = $false }
    }
    if (-not $gravar) { return }
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    [System.IO.File]::WriteAllBytes($arquivo, $bytes)
    if (Get-Command Protect-ArquivoUsuario -ErrorAction SilentlyContinue) {
        Protect-ArquivoUsuario $arquivo
    }
}

function Stop-KeeperSeVivo {
    param([Parameter(Mandatory = $true)][string]$Raiz)
    if ([string]::IsNullOrWhiteSpace($Raiz)) { return }
    $arquivo = Join-Path $Raiz ".n8groker\keeper.pid"
    if (-not (Test-Path -LiteralPath $arquivo -PathType Leaf)) { return }
    $texto = ""
    try {
        $texto = [System.IO.File]::ReadAllText($arquivo)
    } catch {
        return
    }
    $alvo = 0
    $ok = [int]::TryParse($texto.Trim(), [ref]$alvo)
    if (-not $ok -or $alvo -le 0) {
        Remove-Item -LiteralPath $arquivo -Force -ErrorAction SilentlyContinue
        return
    }
    $vivo = Get-Process -Id $alvo -ErrorAction SilentlyContinue
    if (-not $vivo) {
        Remove-Item -LiteralPath $arquivo -Force -ErrorAction SilentlyContinue
        return
    }
    $linha = Get-LinhaDeComandoKeeper -ProcessId $alvo
    $ehKeeper = $false
    if ($linha) {
        $ehKeeper = $linha.ToLowerInvariant().Contains("control_plane.keeper")
    }
    if ($ehKeeper) {
        Stop-Process -Id $alvo -Force -ErrorAction SilentlyContinue
        for ($espera = 0; $espera -lt 20; $espera++) {
            if (-not (Get-Process -Id $alvo -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 100
        }
        Write-Host "    Encerrando Keeper PID $alvo."
    } elseif (-not $linha) {
        Write-Host "[AVISO] PID $alvo do keeper.pid nao teve linha de comando. Nao encerrei esse processo."
    } else {
        Write-Host "[AVISO] PID $alvo nao e o Keeper. Nao encerrei esse processo."
        Remove-Item -LiteralPath $arquivo -Force -ErrorAction SilentlyContinue
        return
    }
    if (-not (Get-Process -Id $alvo -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $arquivo -Force -ErrorAction SilentlyContinue
    }
}
