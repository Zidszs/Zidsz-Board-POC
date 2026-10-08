"""Boot da maquina.key e a tecla Q matando o PID do Keeper."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_boot_cria_maquina_fora_dos_volumes():
    texto = (ROOT / "iniciar_servicos.ps1").read_text(encoding="utf-8")
    montados = texto.split("function Ensure-ArquivosMontados", 1)[1].split("\nfunction ", 1)[0]
    assert "Ensure-ChaveMaquina" not in montados
    assert "maquina.key" not in montados
    assert "Ensure-ChaveMaquina -Raiz $PSScriptRoot" in texto
    stop = texto.split("function Stop-Tudo", 1)[1].split("\ntrap {", 1)[0]
    assert "Stop-KeeperSeVivo -Raiz $PSScriptRoot" in stop
    for relativo in (
        "Scout_OSINT_Docker/docker-compose.yml",
        "n8n/docker-compose.yml",
        "llm/docker-compose.yml",
        "ngrok/docker-compose.yml",
    ):
        compose = (ROOT / relativo).read_text(encoding="utf-8")
        assert "maquina.key" not in compose


def test_ensure_chave_e_stop_pid(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    script = ROOT / "scripts" / "keeper_boot.ps1"
    runner = tmp_path / "run-keeper-boot.ps1"
    raiz = tmp_path / "projeto"
    runner.write_text(
        f"""
$ErrorActionPreference = 'Stop'
. '{script.as_posix()}'
$raiz = '{raiz.as_posix()}'
Ensure-ChaveMaquina -Raiz $raiz
$arquivo = Join-Path $raiz '.n8groker/maquina.key'
if (-not (Test-Path -LiteralPath $arquivo)) {{ throw 'nao criou' }}
if ((Get-Item -LiteralPath $arquivo).Length -ne 32) {{ throw 'tamanho' }}
$antes = [System.IO.File]::ReadAllBytes($arquivo)
Ensure-ChaveMaquina -Raiz $raiz
$depois = [System.IO.File]::ReadAllBytes($arquivo)
if (@(Compare-Object $antes $depois).Count -gt 0) {{ throw 'reescreveu' }}
$envPath = Join-Path $raiz '.env'
[System.IO.File]::WriteAllText($envPath, "SCOUT=1`n")
$envAntes = [System.IO.File]::ReadAllText($envPath)
Ensure-ChaveMaquina -Raiz $raiz
if ([System.IO.File]::ReadAllText($envPath) -ne $envAntes) {{ throw '.env mudou' }}
[System.IO.File]::WriteAllBytes($arquivo, [byte[]]@())
Ensure-ChaveMaquina -Raiz $raiz
if ((Get-Item -LiteralPath $arquivo).Length -ne 32) {{ throw 'vazio nao foi preenchido' }}
$pastaChave = Join-Path $raiz '.n8groker/maquina.key'
Remove-Item -LiteralPath $pastaChave -Force
New-Item -ItemType Directory -Path $pastaChave | Out-Null
$erroPasta = $false
try {{
  Ensure-ChaveMaquina -Raiz $raiz
}} catch {{
  $erroPasta = $true
}}
if (-not $erroPasta) {{ throw 'pasta foi aceita' }}
if (-not (Test-Path -LiteralPath $pastaChave -PathType Container)) {{ throw 'pasta foi apagada' }}
Remove-Item -LiteralPath $pastaChave -Force
Ensure-ChaveMaquina -Raiz $raiz

$shell = if (Get-Command powershell.exe -ErrorAction SilentlyContinue) {{ 'powershell.exe' }} else {{ (Get-Command pwsh).Source }}
$keeper = Start-Process -FilePath $shell -ArgumentList '-NoProfile','-Command',"Start-Sleep -Seconds 90; 'control_plane.keeper'" -PassThru
$outro = Start-Process -FilePath $shell -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 90' -PassThru
try {{
  $linha = ''
  for ($i = 0; $i -lt 30; $i++) {{
    $linha = Get-LinhaDeComandoKeeper -ProcessId $keeper.Id
    if ($linha -and $linha.ToLowerInvariant().Contains('control_plane.keeper')) {{ break }}
    Start-Sleep -Milliseconds 200
  }}
  if (-not $linha.ToLowerInvariant().Contains('control_plane.keeper')) {{ throw ('linha keeper: ' + $linha) }}
  $pidFile = Join-Path $raiz '.n8groker/keeper.pid'
  Set-Content -LiteralPath $pidFile -Value $keeper.Id -Encoding ASCII
  Stop-KeeperSeVivo -Raiz $raiz
  if (Get-Process -Id $keeper.Id -ErrorAction SilentlyContinue) {{ throw 'nao matou o keeper' }}
  if (-not (Get-Process -Id $outro.Id -ErrorAction SilentlyContinue)) {{ throw 'matou processo alheio no primeiro stop' }}
  Set-Content -LiteralPath $pidFile -Value $outro.Id -Encoding ASCII
  Stop-KeeperSeVivo -Raiz $raiz
  if (-not (Get-Process -Id $outro.Id -ErrorAction SilentlyContinue)) {{ throw 'matou pid que nao e o keeper' }}
}} finally {{
  Stop-Process -Id $keeper.Id -Force -ErrorAction SilentlyContinue
  Stop-Process -Id $outro.Id -Force -ErrorAction SilentlyContinue
}}
""",
        encoding="utf-8",
    )
    proc = subprocess.run([pwsh, "-NoProfile", "-File", str(runner)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
