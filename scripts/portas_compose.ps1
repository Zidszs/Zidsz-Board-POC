function Resolve-PortaSemHud {
    param(
        [int]$Pedida,
        [int]$Segura,
        [int]$Atual = 0
    )
    if ($Pedida -eq 8501) {
        if ($Atual -gt 0 -and $Atual -ne 8501) {
            return $Atual
        }
        return $Segura
    }
    return $Pedida
}

function ConvertTo-PortaPedida {
    param(
        [string]$Texto,
        [int]$Padrao
    )
    $limpo = ""
    if ($Texto) { $limpo = $Texto.Trim() }
    if ($limpo -match '^\d+$') {
        $numero = [int]$limpo
        if ($numero -ge 1 -and $numero -le 65535) { return $numero }
    }
    return $Padrao
}

function Set-PortasDoCompose {
    # O processo do Compose herda estas variaveis. Elas ganham do --env-file,
    # entao SCOUT_PUBLIC_PORT=8501 no .env nao publica 127.0.0.1:8501.
    $pedidaPublica = ConvertTo-PortaPedida -Texto (Get-EnvValue "SCOUT_PUBLIC_PORT" "4050") -Padrao 4050
    $publica = Resolve-PortaSemHud -Pedida $pedidaPublica -Segura 4050
    $pedidaBorda = ConvertTo-PortaPedida -Texto (Get-EnvValue "PAINEL_BORDA_PORT" "8502") -Padrao 8502
    $borda = Resolve-PortaSemHud -Pedida $pedidaBorda -Segura 8502
    if ($publica -ne $pedidaPublica) {
        Write-Host "[AVISO] Porta $pedidaPublica e o HUD-admin. O Compose publica $publica. O Control Plane continua." -ForegroundColor Yellow
    }
    if ($borda -ne $pedidaBorda) {
        Write-Host "[AVISO] PAINEL_BORDA_PORT $pedidaBorda e o HUD-admin. O Compose usa $borda." -ForegroundColor Yellow
    }
    $env:SCOUT_PUBLIC_PORT = "$publica"
    $env:PAINEL_BORDA_PORT = "$borda"
}
