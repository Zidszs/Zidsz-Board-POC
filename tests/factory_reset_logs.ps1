# Testa que factory_reset nao apaga *.log dentro de Arquivos-n8n.
# A varredura antiga apagava qualquer .log a partir da raiz: este teste falhava.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$reset = Join-Path $repo "factory_reset.ps1"
. $reset

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

Remove-ProjectLogs -Root $tmp

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
