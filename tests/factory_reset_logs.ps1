# Testa que factory_reset nao apaga *.log dentro de Arquivos-n8n.
# A varredura antiga apagava qualquer .log a partir da raiz: este teste falhava.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$fonte = Get-Content -LiteralPath (Join-Path $repo "factory_reset.ps1") -Raw -Encoding UTF8

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

$funcao = Get-BlocoFuncao "Remove-LogsVolateis" $fonte
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("n8groker-logtest-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $tmp "Arquivos-n8n\sub") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tmp "n8n\storage\Porteiro") -Force | Out-Null

$userLog = Join-Path $tmp "Arquivos-n8n\entrada.log"
$userNested = Join-Path $tmp "Arquivos-n8n\sub\notas.log"
$runtimeLog = Join-Path $tmp "n8n\storage\Porteiro\registro_portaria.log"
$lookalike = Join-Path $tmp "Arquivos-n8n-extra.log"

Set-Content -LiteralPath $userLog -Value "user" -Encoding Ascii
Set-Content -LiteralPath $userNested -Value "nested" -Encoding Ascii
Set-Content -LiteralPath $runtimeLog -Value "runtime" -Encoding Ascii
Set-Content -LiteralPath $lookalike -Value "lookalike" -Encoding Ascii

$filho = Join-Path ([System.IO.Path]::GetTempPath()) ("n8g-logs-" + [guid]::NewGuid().ToString("N") + ".ps1")
$prefixo = @"
`$ErrorActionPreference = 'Stop'
`$Root = '$($tmp.Replace("'", "''"))'
"@
$sufixo = @'
Remove-LogsVolateis
'@
$tudo = $prefixo + "`r`n" + $funcao + "`r`n" + $sufixo + "`r`n"
[System.IO.File]::WriteAllText($filho, $tudo)
try {
    $exe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $proc = Start-Process -FilePath $exe -ArgumentList @("-NoProfile", "-File", $filho) -Wait -PassThru -NoNewWindow
    if ($proc.ExitCode -ne 0) {
        Write-Host ("FAIL a funcao saiu " + $proc.ExitCode)
        exit 1
    }
} finally {
    Remove-Item -LiteralPath $filho -Force -ErrorAction SilentlyContinue
}

$failed = $false
if (-not (Test-Path -LiteralPath $userLog)) {
    Write-Host "FAIL: apagou Arquivos-n8n/entrada.log"
    $failed = $true
}
if (-not (Test-Path -LiteralPath $userNested)) {
    Write-Host "FAIL: apagou Arquivos-n8n/sub/notas.log"
    $failed = $true
}
if (Test-Path -LiteralPath $runtimeLog) {
    Write-Host "FAIL: manteve log de runtime"
    $failed = $true
}
if (Test-Path -LiteralPath $lookalike) {
    Write-Host "FAIL: manteve Arquivos-n8n-extra.log (nome parecido, fora da pasta)"
    $failed = $true
}

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
if ($failed) { exit 1 }
Write-Host "OK"
exit 0
