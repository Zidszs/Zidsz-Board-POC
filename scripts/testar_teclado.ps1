# Diagnostico do teclado da janela. Rode no Windows 10, PowerShell 5.1,
# numa janela comum:  powershell -NoProfile -File scripts\testar_teclado.ps1
# Cada tecla lida imprime o codigo, o modo (CONIN$ ou fallback) e o SizeOf.
# Q encerra. -SomenteTamanho so confere o struct (tem de ser 20) e sai.
param([switch]$SomenteTamanho)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "teclado_janela.ps1")

$tamanho = 0
try {
    Initialize-TecladoJanela
    $tamanho = [int][N8Groker.TecladoJanela]::Tamanho()
} catch {
    Write-Output ("SizeOf=0 erro=" + $_.Exception.Message)
    exit 1
}
Write-Output ("SizeOf=" + $tamanho)
if ($tamanho -ne 20) {
    Write-Output "O struct do teclado nao tem 20 bytes. O CONIN$ nao e confiavel."
    exit 1
}
if ($SomenteTamanho) {
    # Q no meio do lote tem de vencer. A ultima tecla so vale quando nao ha Q nem G.
    $qMeio = [N8Groker.TecladoJanela]::Escolher([int[]]@(1, 1, 1), [int[]]@(1, 1, 1), [int[]]@(65, 81, 71), [int[]]@(0, 0, 0))
    $qUnicode = [N8Groker.TecladoJanela]::Escolher([int[]]@(1), [int[]]@(1), [int[]]@(0), [int[]]@(113))
    $qMaiuscula = [N8Groker.TecladoJanela]::Escolher([int[]]@(1), [int[]]@(1), [int[]]@(0), [int[]]@(81))
    $gUnicode = [N8Groker.TecladoJanela]::Escolher([int[]]@(1), [int[]]@(1), [int[]]@(0), [int[]]@(103))
    $ultima = [N8Groker.TecladoJanela]::Escolher([int[]]@(1, 1), [int[]]@(1, 1), [int[]]@(65, 66), [int[]]@(0, 0))
    Write-Output ("lote=Q-no-meio tecla=" + $qMeio + " modo=CONIN$ SizeOf=" + $tamanho)
    Write-Output ("lote=unicode-q tecla=" + $qUnicode + " modo=CONIN$ SizeOf=" + $tamanho)
    Write-Output ("lote=unicode-Q tecla=" + $qMaiuscula + " modo=CONIN$ SizeOf=" + $tamanho)
    Write-Output ("lote=unicode-g tecla=" + $gUnicode + " modo=CONIN$ SizeOf=" + $tamanho)
    Write-Output ("lote=ultima tecla=" + $ultima + " modo=CONIN$ SizeOf=" + $tamanho)
    if ($qMeio -ne 81 -or $qUnicode -ne 81 -or $qMaiuscula -ne 81 -or $gUnicode -ne 71 -or $ultima -ne 66) {
        Write-Output "O lote nao foi lido inteiro, ou UnicodeChar foi ignorado."
        exit 1
    }
    exit 0
}

Write-Output "Aperte teclas nesta janela. Q encerra."
Write-Output "Cada linha: tecla, modo (CONIN$ ou fallback), SizeOf."
while ($true) {
    $tecla = Read-TeclaJanela
    if ($tecla -ne 0) {
        Write-Output ("tecla=" + $tecla + " modo=" + $script:TecladoModo + " SizeOf=" + $tamanho)
        if ($tecla -eq 81) { break }
    }
    Start-Sleep -Milliseconds 100
}
exit 0
