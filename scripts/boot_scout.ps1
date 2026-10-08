# Decisao de boot quando o Scout nao sobe. O Control Plane ja esta no ar
# e nenhuma falha daqui chama Stop-Tudo. Q, no loop, continua encerrando.

function Resultado-SubidaScout {
    param(
        [int]$CodigoUp = 0,
        [bool]$SaudeOk = $true,
        [string]$Erro = ""
    )
    if ($Erro) {
        return @{ NoAr = $false; Motivo = $Erro }
    }
    if ($CodigoUp -ne 0) {
        return @{ NoAr = $false; Motivo = "docker compose up falhou (codigo $CodigoUp)" }
    }
    if (-not $SaudeOk) {
        return @{ NoAr = $false; Motivo = "backend nao respondeu em :8765/health" }
    }
    return @{ NoAr = $true; Motivo = "" }
}

function Plano-NgrokAposScout {
    param(
        [bool]$UsarScout,
        [bool]$ScoutNoAr
    )
    if ($UsarScout -and -not $ScoutNoAr) {
        return @{ Subir = $false; Host = "" }
    }
    if ($UsarScout) {
        return @{ Subir = $true; Host = "scout-backend" }
    }
    return @{ Subir = $true; Host = "host.docker.internal" }
}

function Completar-Ngrok {
    param([int]$Codigo)
    if ($Codigo -ne 0) {
        return "docker compose up ngrok falhou (codigo $Codigo). O Control Plane continua no ar."
    }
    return ""
}

function Get-PortaScoutEfetiva {
    $texto = "4050"
    if (Get-Command Get-EnvValue -ErrorAction SilentlyContinue) {
        $texto = Get-EnvValue "SCOUT_PUBLIC_PORT" "4050"
    }
    $pedida = ConvertTo-PortaPedida -Texto "$texto" -Padrao 4050
    return "$(Resolve-PortaSemHud -Pedida $pedida -Segura 4050)"
}

function Estado-ScoutNoLoop {
    param(
        [bool]$ContainerNoAr,
        [bool]$SaudeOk
    )
    # O ngrok continua em scout-backend. Tirar o alvo no meio do loop
    # recreia a URL publica e o n8n. O painel local nao depende desse tunel.
    if (-not $ContainerNoAr) {
        return @{ NoAr = $false; Motivo = "container scout-backend parado" }
    }
    if (-not $SaudeOk) {
        return @{ NoAr = $false; Motivo = "backend nao respondeu em :8765/health" }
    }
    return @{ NoAr = $true; Motivo = "" }
}

function Texto-HudScout {
    param(
        [bool]$ScoutNoAr,
        [string]$Motivo,
        [string]$Porta
    )
    if (-not $ScoutNoAr) {
        return "Scout fora do ar - porta $Porta. Motivo: $Motivo"
    }
    return "Scout Gate ACTIVO - porta $Porta"
}

function Texto-BootNucleo {
    param(
        [bool]$UsarScout,
        [bool]$ScoutNoAr,
        [string]$Motivo,
        [string]$Porta,
        [string[]]$Stacks
    )
    if ($UsarScout -and -not $ScoutNoAr) {
        return "Scout fora do ar. Porta efetiva $Porta. Motivo: $Motivo"
    }
    $lista = @($Stacks | Where-Object { $_ })
    if ($lista.Count -eq 0) {
        return "Nucleo no ar. Stacks no boot: nenhuma (so o nucleo)"
    }
    return "Nucleo no ar. Stacks no boot: " + ($lista -join ", ")
}
