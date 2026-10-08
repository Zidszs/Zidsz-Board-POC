# Confirma que a parada limpa nao chama Read-Host quando o stdin nao e um console.
# Falha se Confirm-Interactive ainda ler o teclado com a entrada redirecionada.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$fonte = Get-Content -LiteralPath (Join-Path $repo "iniciar_servicos.ps1") -Raw -Encoding UTF8

function Get-BlocoFuncao {
    param([string]$Nome, [string]$Texto)
    $marca = "function $Nome"
    $inicio = $Texto.IndexOf($marca)
    if ($inicio -lt 0) { throw "falta $Nome" }
    $abre = $Texto.IndexOf("{", $inicio)
    if ($abre -lt 0) { throw "falta corpo de $Nome" }
    $nivel = 0
    for ($i = $abre; $i -lt $Texto.Length; $i++) {
        $c = $Texto[$i]
        if ($c -eq "{") { $nivel++ }
        elseif ($c -eq "}") {
            $nivel--
            if ($nivel -eq 0) {
                return $Texto.Substring($inicio, $i - $inicio + 1)
            }
        }
    }
    throw "fim de $Nome nao encontrado"
}

$funcoes = (Get-BlocoFuncao "Test-ConsoleInterativo" $fonte) + "`r`n" + (Get-BlocoFuncao "Confirm-Interactive" $fonte)
$filho = Join-Path ([System.IO.Path]::GetTempPath()) ("n8g-sem-console-" + [guid]::NewGuid().ToString("N") + ".ps1")
$vazio = Join-Path ([System.IO.Path]::GetTempPath()) ("n8g-stdin-" + [guid]::NewGuid().ToString("N") + ".txt")
[System.IO.File]::WriteAllBytes($vazio, [byte[]]@())
$prefixo = @'
$script:chamou = $false
function Read-Host { $script:chamou = $true; return '' }
'@
$sufixo = @'
Confirm-Interactive
if ($script:chamou) { exit 2 }
exit 0
'@
$tudo = $prefixo + "`r`n" + $funcoes + "`r`n" + $sufixo + "`r`n"
[System.IO.File]::WriteAllText($filho, $tudo)
try {
    $exe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $proc = Start-Process -FilePath $exe -ArgumentList @("-NoProfile", "-NonInteractive", "-File", $filho) -RedirectStandardInput $vazio -Wait -PassThru -NoNewWindow
    if ($proc.ExitCode -ne 0) {
        Write-Host ("FAIL exit " + $proc.ExitCode)
        exit 1
    }
} finally {
    Remove-Item -LiteralPath $filho -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $vazio -Force -ErrorAction SilentlyContinue
}
Write-Host "OK"
exit 0
