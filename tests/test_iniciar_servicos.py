"""Contratos do orquestrador. O script inteiro não sobe a infra; o pwsh só chama Set-EnvValue."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _script() -> str:
    return (ROOT / "iniciar_servicos.ps1").read_text(encoding="utf-8")


def test_compose_mostra_erro_e_desligamento_nao_apaga_a_causa():
    text = _script()
    compose = text.split("function Invoke-Compose", 1)[1].split("function Stop-Porteiro", 1)[0]
    assert "2>$null" not in compose
    assert "SilentlyContinue" not in compose
    assert "[ERRO] Compose falhou" in compose
    stop = text.split("function Stop-Tudo", 1)[1].split("\ntrap {", 1)[0]
    assert "if (-not $script:encerramentoPorFalha)" in stop
    assert "nao foi um desligamento limpo" in stop
    assert "Clear-Host" in stop


def test_autosetup_cobre_o_que_a_subida_chama():
    text = _script()
    assert "function Invoke-AutoSetup" in text
    assert "OpenJS.NodeJS.LTS" in text
    assert "Python.Python.3.12" in text
    assert "Docker.DockerDesktop" in text
    assert "Docker.DockerCompose" in text
    assert "--scope user" in text
    assert "WindowsApps" in text
    assert "docker network create rede_comunicacao" in text
    assert "scripts\\init_env.py" in text
    assert "__GENERATE_" in text
    assert "SCOUT_NGROK_TUNNEL_URL" in text
    assert "admin@example.com" in text
    assert "admin@localhost" in text  # só para reescrever o valor rejeitado
    assert ".n8groker.skip-control-plane-venv" in text
    auto = text.split("function Invoke-AutoSetup", 1)[1]
    # O corpo não instala Git, npm nem o binário ngrok do host.
    assert "git clone" not in auto.lower()
    assert "npm install" not in auto
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env\n" in gitignore or gitignore.startswith(".env\n") or "\n.env\n" in f"\n{gitignore}"
    assert ".n8groker.skip-control-plane-venv" in gitignore
    assert ".venv/" in gitignore
    assert "venv/" in gitignore


def test_winget_so_roda_depois_da_confirmacao():
    text = _script()
    corpo = text.split("function Invoke-WingetInstall", 1)[1].split("function Install-FerramentaSeFaltar", 1)[0]
    confirma = corpo.index("if (-not (Confirm-Sim $texto))")
    chama = corpo.index("& winget")
    assert confirma < chama
    assert "return $false" in corpo[confirma:chama]
    assert '"install", "-e", "--id", $Id' in corpo
    assert "--accept-package-agreements" in corpo
    assert "--accept-source-agreements" in corpo
    assert '"--scope", "user"' in corpo
    auto = text.split("function Invoke-AutoSetup", 1)[1]
    for pacote in ("OpenJS.NodeJS.LTS", "Python.Python.3.12", "Docker.DockerDesktop", "Docker.DockerCompose"):
        assert pacote in auto


def test_compose_so_e_perguntado_com_docker_cli_e_compose_ausente():
    auto = _script().split("function Invoke-AutoSetup", 1)[1]
    desktop = auto.index('WingetId "Docker.DockerDesktop"')
    gate = auto.index("if ($dockerCliOk)")
    compose = auto.index('WingetId "Docker.DockerCompose"')
    assert desktop < gate < compose
    ramo = auto[gate:]
    assert "Docker.DockerDesktop" not in ramo
    assert ramo.index("Resolve-ComposeCommand") < ramo.index("Docker.DockerCompose")
    assert "Sem o comando docker, o Compose nao e perguntado a parte." in auto


def test_set_env_value_nao_usa_set_content():
    corpo = _script().split("function Set-EnvValue", 1)[1].split("function Sync-ScoutPorteiro", 1)[0]
    assert "Set-Content" not in corpo
    assert "Write-EnvLinesNoBom" in corpo
    assert "UTF8Encoding" in _script()


def test_set_env_value_grava_sem_bom_e_com_cifrao(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    script = (ROOT / "iniciar_servicos.ps1").resolve()
    env_lf = tmp_path / "lf.env"
    env_crlf = tmp_path / "crlf.env"
    env_bom = tmp_path / "bom.env"
    env_lf.write_bytes(b"FOO=bar\nLITELLM_MASTER_KEY=sk-old\n")
    env_crlf.write_bytes(b"FOO=bar\r\nKEEP=1\r\n")
    env_bom.write_bytes(b"\xef\xbb\xbfFOO=bar\nOUTRA=1\n")
    runner = tmp_path / "run-set-env.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script.as_posix()}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and @($parseErrors).Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$nomes = @('Write-EnvLinesNoBom', 'Repair-EnvBom', 'Set-EnvValue')
$defs = $ast.FindAll({{ param($no) $no -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($nomes -contains $no.Name) }}, $true)
if (@($defs).Count -ne 3) {{ throw ('funcoes encontradas: ' + @($defs).Count) }}
foreach ($def in $defs) {{ Invoke-Expression $def.Extent.Text }}

function Assert-SemBom([string]$Path) {{
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {{
        throw ('BOM em ' + $Path)
    }}
}}

function Assert-Linha([string]$Texto, [string]$Esperada) {{
    $achou = $false
    foreach ($linha in ($Texto -split "`n")) {{
        if ($linha.TrimEnd([char]13) -eq $Esperada) {{ $achou = $true }}
    }}
    if (-not $achou) {{ throw ('nao achei [' + $Esperada + '] em [' + $Texto + ']') }}
}}

$PATH_ENV = '{env_lf.as_posix()}'
Set-EnvValue -Key 'LITELLM_MASTER_KEY' -Value 'sk-abc$xyz'
Assert-SemBom $PATH_ENV
$utf8 = New-Object System.Text.UTF8Encoding $false
$texto = [System.IO.File]::ReadAllText($PATH_ENV, $utf8)
Assert-Linha $texto 'FOO=bar'
Assert-Linha $texto 'LITELLM_MASTER_KEY=sk-abc$xyz'
if ($texto.Contains([string][char]13)) {{ throw 'LF virou CRLF' }}

$PATH_ENV = '{env_crlf.as_posix()}'
Set-EnvValue -Key 'KEEP' -Value 'pre$post'
Assert-SemBom $PATH_ENV
$textoCrlf = [System.IO.File]::ReadAllText($PATH_ENV, $utf8)
Assert-Linha $textoCrlf 'KEEP=pre$post'
Assert-Linha $textoCrlf 'FOO=bar'
if (-not $textoCrlf.Contains([string]([char]13) + [char]10)) {{ throw 'CRLF nao foi preservado' }}

$PATH_ENV = '{env_bom.as_posix()}'
Set-EnvValue -Key 'OUTRA' -Value '2'
Assert-SemBom $PATH_ENV
$textoBom = [System.IO.File]::ReadAllText($PATH_ENV, $utf8)
Assert-Linha $textoBom 'FOO=bar'
Assert-Linha $textoBom 'OUTRA=2'
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(runner)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_marcador_generate_ignora_comentario():
    init = _script().split("function Initialize-EnvDoProjeto", 1)[1].split("function Ensure-DataDirs", 1)[0]
    assert "Test-EnvTemMarcadorPendente" in init
    assert "-match '__GENERATE_'" not in init
    assert "__GENERATE_" not in init.split("if (Test-EnvTemMarcadorPendente)", 1)[0]


def test_g_abre_o_painel_scout():
    text = _script()
    assert "Pressione G para abrir o painel (Scout)" in text
    assert "Start-ScoutGui" not in text
    assert "Scout_network.py" not in text
    corpo = text.split("function Open-PainelScout", 1)[1].split("function Get-NgrokTunnelTarget", 1)[0]
    assert "Start-ControlPlane" in corpo
    assert "Test-PainelHttp" in corpo
    assert "/?aba=scout" in corpo
    assert "Nao consegui abrir o painel" in corpo
    assert "streamlit.log" in corpo
    loop = text.split("LOOP DO PAINEL", 1)[1]
    assert "Open-PainelScout" in loop
    assert "Start-ScoutGui" not in loop


def _funcao_ps(texto: str, nome: str) -> str:
    marca = f"function {nome}"
    inicio = texto.index(marca)
    abre = texto.index("{", inicio)
    profundidade = 0
    for indice in range(abre, len(texto)):
        if texto[indice] == "{":
            profundidade += 1
        elif texto[indice] == "}":
            profundidade -= 1
            if profundidade == 0:
                return texto[inicio : indice + 1]
    raise AssertionError(nome)


def test_q_le_conin_e_8501_nao_e_gravada_no_boot(tmp_path):
    text = _script()
    assert "Console.In.Peek" not in text
    loop = text.split("LOOP DO PAINEL", 1)[1]
    assert loop.index("Test-ShutdownRequest") < loop.index("Read-TeclaJanela")
    assert "$tecla -eq 81" in loop
    assert "$tecla -eq 71" in loop
    assert "Open-PainelScout" in loop
    assert "KeyAvailable" not in loop
    compose = _funcao_ps(text, "Invoke-Compose")
    assert compose.index("Set-PortasDoCompose") < compose.index("--env-file")
    boot = text.split("Start-ControlPlane", 1)[1].split("LOOP DO PAINEL", 1)[0]
    assert 'throw "docker compose up ngrok' not in boot
    assert 'throw "Scout backend' not in boot
    assert "Plano-NgrokAposScout" in boot
    assert "Texto-BootNucleo" in boot
    assert "$tecla -eq 81" in text.split("LOOP DO PAINEL", 1)[1]
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    tamanho = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(ROOT / "scripts" / "testar_teclado.ps1"), "-SomenteTamanho"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tamanho.returncode == 0, tamanho.stdout + tamanho.stderr
    assert "SizeOf=20" in tamanho.stdout
    assert "lote=Q-no-meio tecla=81" in tamanho.stdout
    assert "lote=unicode-q tecla=81" in tamanho.stdout
    prova = tmp_path / "portas.ps1"
    prova.write_text(
        """
$ErrorActionPreference = 'Stop'
. (Join-Path '%s' 'scripts/portas_compose.ps1')
. (Join-Path '%s' 'scripts/teclado_janela.ps1')
function Get-EnvValue {
    param($Key, $Default)
    if ($Key -eq 'SCOUT_PUBLIC_PORT') { return '8501' }
    if ($Key -eq 'PAINEL_BORDA_PORT') { return '8501' }
    return $Default
}
Set-PortasDoCompose
if ($env:SCOUT_PUBLIC_PORT -ne '4050') { throw "publica=$env:SCOUT_PUBLIC_PORT" }
if ($env:PAINEL_BORDA_PORT -ne '8502') { throw "borda=$env:PAINEL_BORDA_PORT" }
Initialize-TecladoJanela
function Read-TeclaFallback {
    $script:TecladoModo = 'fallback'
    $script:FallbackRodou = $true
    return 0
}
$script:TecladoJanelaEstado = 'ok'
$script:HandleTeclado = [IntPtr]1
$script:FallbackRodou = $false
[void](Read-TeclaJanela)
if (-not $script:FallbackRodou) { throw 'leitura vazia nao caiu no fallback' }
if ($script:TecladoModo -ne 'fallback') { throw $script:TecladoModo }
Write-Output 'portas=4050,8502 fallback=1'
"""
        % (ROOT, ROOT),
        encoding="utf-8",
    )
    portas = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(prova)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert portas.returncode == 0, portas.stdout + portas.stderr
    assert "portas=4050,8502 fallback=1" in portas.stdout
    decisao = tmp_path / "boot_scout.ps1"
    decisao.write_text(
        """
$ErrorActionPreference = 'Stop'
. (Join-Path '%s' 'scripts/portas_compose.ps1')
. (Join-Path '%s' 'scripts/boot_scout.ps1')
function Get-EnvValue {
    param($Key, $Default)
    if ($Key -eq 'SCOUT_PUBLIC_PORT') { return '8501' }
    return $Default
}
$porta = Get-PortaScoutEfetiva
if ($porta -ne '4050') { throw "porta=$porta" }
$up = Resultado-SubidaScout -CodigoUp 1 -SaudeOk $false
if ($up.NoAr) { throw 'up falho nao pode ficar no ar' }
$saude = Resultado-SubidaScout -CodigoUp 0 -SaudeOk $false
if ($saude.NoAr) { throw 'saude falha nao pode ficar no ar' }
if ($saude.Motivo -notmatch '8765') { throw $saude.Motivo }
$plano = Plano-NgrokAposScout -UsarScout $true -ScoutNoAr $false
if ($plano.Subir) { throw 'ngrok apontou para o scout caido' }
if ($plano.Host -eq 'scout-backend') { throw 'host scout-backend' }
$noAr = Plano-NgrokAposScout -UsarScout $true -ScoutNoAr $true
if (-not $noAr.Subir -or $noAr.Host -ne 'scout-backend') { throw 'scout no ar precisa do ngrok em scout-backend' }
$aviso = Completar-Ngrok -Codigo 7
if ($aviso -notmatch 'Control Plane continua') { throw $aviso }
$texto = Texto-BootNucleo -UsarScout $true -ScoutNoAr $false -Motivo $up.Motivo -Porta $porta -Stacks @()
if ($texto -match 'Nucleo no ar') { throw $texto }
if ($texto -notmatch 'Scout fora do ar') { throw $texto }
if ($texto -notmatch '4050') { throw $texto }
$hud = Texto-HudScout -ScoutNoAr $false -Motivo $up.Motivo -Porta $porta
if ($hud -match 'ACTIVO') { throw $hud }
if ($hud -notmatch '4050') { throw $hud }
$ok = Texto-BootNucleo -UsarScout $true -ScoutNoAr $true -Motivo '' -Porta $porta -Stacks @()
if ($ok -notmatch 'Nucleo no ar') { throw $ok }
Write-Output 'boot-scout=ok'
"""
        % (ROOT, ROOT),
        encoding="utf-8",
    )
    saida = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(decisao)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert saida.returncode == 0, saida.stdout + saida.stderr
    assert "boot-scout=ok" in saida.stdout


def test_venv_nao_devolve_true_no_pipeline():
    auto = _script().split("function Invoke-AutoSetup", 1)[1]
    chamadas = [linha for linha in auto.splitlines() if "Ensure-ProjectVenv" in linha]
    assert len(chamadas) == 1
    assert all(linha.rstrip().endswith("| Out-Null") for linha in chamadas)
    corpo = _script().split("function Ensure-ProjectVenv", 1)[1].split("function Invoke-AutoSetup", 1)[0]
    assert "return $true" not in corpo
    assert "return $false" not in corpo


def test_boot_de_app_so_entra_se_stacks_boot_pedir():
    text = _script()
    main = text.split("\nInvoke-AutoSetup\n", 1)[1]
    boot = main.split("LOOP DO PAINEL", 1)[0]
    assert "STACKS_BOOT" in text
    assert "STACKS_NO_BOOT" not in text
    assert "function Get-StacksBoot" in text
    assert boot.count("COMPOSE_N8N") == 1
    assert boot.index('@($stacksBoot) -contains "n8n"') < boot.index("COMPOSE_N8N")
    assert "param(" in text.split("$PATH_N8N", 1)[0]
    assert '[ValidateSet("", "n8n", "llm")]' in text
    assert '[ValidateSet("iniciar", "parar", "reiniciar")]' in text
    auto = text.split("function Invoke-AutoSetup", 1)[1].split("\nInvoke-AutoSetup\n", 1)[0]
    assert "if ($Stack) { return }" in auto
    stop = text.split("function Stop-Tudo", 1)[1].split("\ntrap {", 1)[0]
    assert "$script:acaoAvulsa" in stop
    assert text.index("Exit $codigoAvulso") < text.index("\n    Register-Supervisor\n")
    exemplo = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "STACKS_BOOT=" in exemplo
    assert "STACKS_NO_BOOT" not in exemplo
    modelo = (ROOT / ".env_template").read_text(encoding="utf-8")
    assert "STACKS_NO_BOOT" not in modelo
    assert "sk-" not in exemplo.split("STACKS_BOOT=", 1)[0].split("USE_SCOUT=1", 1)[1]


def test_ngrok_antes_do_primeiro_up_de_n8n_e_scout():
    main = _script().split("\nInvoke-AutoSetup\n", 1)[1]
    boot = main.split("LOOP DO PAINEL", 1)[0]
    assert boot.count("COMPOSE_NGROK") == 1
    assert boot.count("COMPOSE_N8N") == 1
    assert boot.index("Start-Scout") < boot.index("COMPOSE_NGROK")
    assert boot.index("COMPOSE_NGROK") < boot.index("Wait-NgrokPublicUrl")
    assert boot.index("Wait-NgrokPublicUrl") < boot.index("COMPOSE_N8N")
    assert "$env:NGROK_TUNNEL_HOST = $planoNgrok.Host" in boot
    plano = (ROOT / "scripts" / "boot_scout.ps1").read_text(encoding="utf-8")
    assert 'Host = "scout-backend"' in plano
    assert "Injetando nova URL" not in _script()
    hud = main.split("LOOP DO PAINEL", 1)[1]
    assert "reinicio esperado, nao e falha" in hud
    cor = _script().split("function Get-CorStatusContainer", 1)[1].split("function Get-TextoStatusContainer", 1)[0]
    assert cor.index("health: starting") < cor.index('match "Up"')


def test_scout_parado_no_loop_mostra_fora_do_ar(tmp_path):
    text = _script()
    loop = text.split("LOOP DO PAINEL", 1)[1]
    assert loop.index("Estado-ScoutNoLoop") < loop.index("Texto-HudScout")
    subida = loop.split("$containerScout = $true", 1)[0].split("if ($script:UseScout)", 1)[-1]
    assert "health: starting" not in subida
    meio = loop.split("Estado-ScoutNoLoop", 1)[1].split("Texto-HudScout", 1)[0]
    assert "Stop-Tudo" not in meio
    assert "NGROK_TUNNEL_HOST" not in meio
    assert "Plano-NgrokAposScout" not in loop.split("} finally {", 1)[0]
    assert "ScoutSaudeDesteQuadro" in loop.split("Texto-HudScout", 1)[1]
    plano = (ROOT / "scripts" / "boot_scout.ps1").read_text(encoding="utf-8")
    assert plano.index("function Estado-ScoutNoLoop") < plano.index("function Texto-HudScout")
    trecho = plano.split("function Estado-ScoutNoLoop", 1)[1].split("function Texto-HudScout", 1)[0]
    assert "container scout-backend parado" in trecho
    assert "scout-backend" in trecho
    pwsh = shutil.which("pwsh")
    if not pwsh:
        return
    prova = tmp_path / "scout_loop.ps1"
    prova.write_text(
        """
$ErrorActionPreference = 'Stop'
. (Join-Path '%s' 'scripts/portas_compose.ps1')
. (Join-Path '%s' 'scripts/boot_scout.ps1')
function Get-EnvValue {
    param($Key, $Default)
    if ($Key -eq 'SCOUT_PUBLIC_PORT') { return '8501' }
    return $Default
}
$porta = Get-PortaScoutEfetiva
$parado = Estado-ScoutNoLoop -ContainerNoAr $false -SaudeOk $false
if ($parado.NoAr) { throw 'container parado ficou no ar' }
if ($parado.Motivo -notmatch 'container scout-backend parado') { throw $parado.Motivo }
$semSaude = Estado-ScoutNoLoop -ContainerNoAr $true -SaudeOk $false
if ($semSaude.NoAr) { throw 'saude falha ficou no ar' }
if ($semSaude.Motivo -notmatch '8765') { throw $semSaude.Motivo }
$vivo = Estado-ScoutNoLoop -ContainerNoAr $true -SaudeOk $true
if (-not $vivo.NoAr -or $vivo.Motivo) { throw 'scout vivo' }
$hud = Texto-HudScout -ScoutNoAr $parado.NoAr -Motivo $parado.Motivo -Porta $porta
if ($hud -match 'ACTIVO') { throw $hud }
if ($hud -notmatch 'Scout fora do ar') { throw $hud }
if ($hud -notmatch '4050') { throw $hud }
$activo = Texto-HudScout -ScoutNoAr $vivo.NoAr -Motivo $vivo.Motivo -Porta $porta
if ($activo -notmatch 'ACTIVO') { throw $activo }
Write-Output 'scout-loop=ok'
"""
        % (ROOT, ROOT),
        encoding="utf-8",
    )
    saida = subprocess.run([pwsh, "-NoProfile", "-File", str(prova)], capture_output=True, text=True, check=False)
    assert saida.returncode == 0, saida.stdout + saida.stderr
    assert "scout-loop=ok" in saida.stdout


def test_marcador_pendente_so_em_valor(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    script = (ROOT / "iniciar_servicos.ps1").resolve()
    so_comentario = tmp_path / "comentario.env"
    com_valor = tmp_path / "valor.env"
    so_comentario.write_text(
        "# troca cada __GENERATE_*__ por um segredo\nN8N_ENCRYPTION_KEY=abc123\nUSE_SCOUT=1\n",
        encoding="utf-8",
    )
    com_valor.write_text(
        "# comentario\nN8N_ENCRYPTION_KEY=__GENERATE_HEX_32__\n",
        encoding="utf-8",
    )
    runner = tmp_path / "marcador.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script.as_posix()}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and @($parseErrors).Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$nomes = @('Get-EnvFileMap', 'Test-EnvTemMarcadorPendente')
$defs = $ast.FindAll({{ param($no) $no -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($nomes -contains $no.Name) }}, $true)
if (@($defs).Count -ne 2) {{ throw ('funcoes: ' + @($defs).Count) }}
foreach ($def in $defs) {{ Invoke-Expression $def.Extent.Text }}
$PATH_ENV = '{so_comentario.as_posix()}'
if (Test-EnvTemMarcadorPendente) {{ throw 'comentario disparou o marcador' }}
$PATH_ENV = '{com_valor.as_posix()}'
if (-not (Test-EnvTemMarcadorPendente)) {{ throw 'valor marcado nao disparou' }}
""",
        encoding="utf-8",
    )
    proc = subprocess.run([pwsh, "-NoProfile", "-File", str(runner)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ensure_project_venv_nao_imprime_true(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    script = (ROOT / "iniciar_servicos.ps1").resolve()
    bindir = tmp_path / "venv" / "Scripts"
    bindir.mkdir(parents=True)
    exe = bindir / "python.exe"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    req = tmp_path / "req.txt"
    req.write_text("streamlit\n", encoding="utf-8")
    runner = tmp_path / "venv.ps1"
    venv = (tmp_path / "venv").as_posix()
    req_ps = req.as_posix()
    runner.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script.as_posix()}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and @($parseErrors).Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$nomes = @('Get-FileSha256', 'Ensure-ProjectVenv')
$defs = $ast.FindAll({{ param($no) $no -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($nomes -contains $no.Name) }}, $true)
if (@($defs).Count -ne 2) {{ throw ('funcoes: ' + @($defs).Count) }}
foreach ($def in $defs) {{ Invoke-Expression $def.Extent.Text }}
$hash = Get-FileSha256 '{req_ps}'
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText('{venv}/.n8groker-requirements.sha256', ($hash + "`n"), $utf8)
$tudo = @(Ensure-ProjectVenv -Nome 'Venv do Scout' -VenvDir '{venv}' -Requirements '{req_ps}' -ImportCheck 'import x' -Obrigatorio $true -Motivo 'teste' 6>&1)
$extra = @($tudo | Where-Object {{ -not ($_ -is [System.Management.Automation.InformationRecord]) }})
if ($extra.Count -ne 0) {{ throw ('saida extra: ' + ($extra | Out-String)) }}
$texto = @($tudo | ForEach-Object {{ "$_" }}) -join "`n"
if ($texto -notmatch '\\[OK\\] Venv do Scout') {{ throw ('sem OK: ' + $texto) }}
if ($texto -match '(?m)^True$') {{ throw 'True vazou' }}
""",
        encoding="utf-8",
    )
    proc = subprocess.run([pwsh, "-NoProfile", "-File", str(runner)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_venv_antigo_so_com_streamlit_reinstala(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    script = (ROOT / "iniciar_servicos.ps1").resolve()
    bindir = tmp_path / "venv" / "Scripts"
    bindir.mkdir(parents=True)
    calls = tmp_path / "calls.txt"
    pip_feito = tmp_path / "pip.ok"
    exe = bindir / "python.exe"
    exe.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "$N8_CALLS"
case "$*" in
  *pip*install*)
    : > "$N8_PIP_FEITO"
    exit 0
    ;;
esac
case "$*" in
  *cryptography*)
    if [ ! -f "$N8_PIP_FEITO" ]; then exit 1; fi
    exit 0
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    exe.chmod(0o755)
    req = tmp_path / "req.txt"
    req.write_text("streamlit\ncryptography\n", encoding="utf-8")
    venv = (tmp_path / "venv").as_posix()
    runner = tmp_path / "venv-antigo.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script.as_posix()}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and @($parseErrors).Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$nomes = @('Get-FileSha256', 'Ensure-ProjectVenv')
$defs = $ast.FindAll({{ param($no) $no -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($nomes -contains $no.Name) }}, $true)
if (@($defs).Count -ne 2) {{ throw ('funcoes: ' + @($defs).Count) }}
foreach ($def in $defs) {{ Invoke-Expression $def.Extent.Text }}
$env:N8_CALLS = '{calls.as_posix()}'
$env:N8_PIP_FEITO = '{pip_feito.as_posix()}'
$tudo = @(Ensure-ProjectVenv -Nome 'Control Plane' -VenvDir '{venv}' -Requirements '{req.as_posix()}' -ImportCheck 'import streamlit; import cryptography' -Obrigatorio $false -Motivo 'teste' 6>&1)
$texto = @($tudo | ForEach-Object {{ "$_" }}) -join "`n"
if ($texto -notmatch '\\[OK\\]') {{ throw ('sem OK: ' + $texto) }}
$hash = Get-FileSha256 '{req.as_posix()}'
$stamp = ([System.IO.File]::ReadAllText('{venv}/.n8groker-requirements.sha256')).Trim()
if ($stamp -ne $hash) {{ throw ('carimbo: ' + $stamp) }}
""",
        encoding="utf-8",
    )
    proc = subprocess.run([pwsh, "-NoProfile", "-File", str(runner)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = calls.read_text(encoding="utf-8")
    assert "pip" in log and "install" in log
    assert "-r" in log


def test_script_grava_token_e_confere_cryptography():
    text = _script()
    porteiro = text.split("function Start-Porteiro", 1)[1].split("function Test-LinhaEhPainel", 1)[0]
    assert "Ensure-PorteiroTokens" in porteiro
    assert "import streamlit; import cryptography" in text
    assert ".n8groker-requirements.sha256" in text
    assert "porteiro-painel.token" in text
    assert "porteiro-n8n.env" in text


def test_painel_sobe_no_boot_e_so_mata_o_proprio_processo():
    text = _script()
    main = text.split("\nInvoke-AutoSetup\n", 1)[1]
    boot = main.split("LOOP DO PAINEL", 1)[0]
    assert boot.index("Start-ControlPlane") < boot.index("Start-Porteiro")
    stop = text.split("function Stop-Tudo", 1)[1].split("\ntrap {", 1)[0]
    assert "Stop-ControlPlane" in stop
    assert "porta 8501" in stop
    start = text.split("function Start-ControlPlane", 1)[1].split("function Stop-ControlPlane", 1)[0]
    assert "--server.headless true" in start
    assert "--server.address 127.0.0.1" in start
    assert "--server.address localhost" not in start
    assert "--server.port" in start
    assert "streamlit.log" in start
    assert "skip-control-plane-venv" in start
    assert "nao e este Control Plane" in start
    assert "Nao encerrei esse processo" in start
    arvore = text.split("function Stop-ArvoreProcesso", 1)[1].split("function Test-PainelHttp", 1)[0]
    assert "/T" in arvore
    assert "taskkill" in arvore
    assert "_stcore/health" in text
    assert "http://localhost:8501" in text
    assert "Write-StatusPainel" in main.split("LOOP DO PAINEL", 1)[1]
    stop_cp = text.split("function Stop-ControlPlane", 1)[1].split("function Test-ScoutHealth", 1)[0]
    assert stop_cp.index("Test-ArvoreEhPainel") < stop_cp.index("Stop-ArvoreProcesso")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".n8groker.control-plane.pid" in gitignore


def test_ollama_e_docker_so_fecham_se_o_script_abriu():
    text = _script()
    main = text.split("\nInvoke-AutoSetup\n", 1)[1]
    boot = main.split("LOOP DO PAINEL", 1)[0]
    assert boot.index("Start-ControlPlane") < boot.index("Start-OllamaLocal") < boot.index("Start-Porteiro")
    stop = text.split("function Stop-Tudo", 1)[1].split("\ntrap {", 1)[0]
    down = stop.rindex('ComposeArgs @("down")')
    assert down < stop.index("Stop-OllamaLocal")
    assert stop.index("Stop-OllamaLocal") < stop.index("Stop-DockerDesktopIniciado")
    saida = text.split("function Stop-SePendencias", 1)[1].split("function Confirm-Sim", 1)[0]
    assert "Stop-OllamaLocal" in saida
    assert "Stop-DockerDesktopIniciado" in saida
    ollama = text.split("function Start-OllamaLocal", 1)[1].split("function Stop-OllamaLocal", 1)[0]
    assert ollama.index("Test-OllamaHttp") < ollama.index("OllamaIniciadoPeloScript = $true")
    assert "USE_OLLAMA_LOCAL=0" in ollama
    assert "Ollama.Ollama" in text
    assert "ollama serve" in text or 'ArgumentList @("serve")' in text
    docker_stop = text.split("function Stop-DockerDesktopIniciado", 1)[1].split("function Write-StatusOllama", 1)[0]
    assert docker_stop.index("DockerIniciadoPeloScript") < docker_stop.index("docker desktop stop")
    motor = text.split("Motor Docker em execucao. Este script nao vai fecha-lo no Q.", 1)[1].split("Abrindo $exeDocker", 1)[0]
    assert "DockerIniciadoPeloScript = $false" in motor
    assert "DockerIniciadoPeloScript = $true" in text.split("Abrindo $exeDocker", 1)[1].split("Nao achei Docker Desktop.exe", 1)[0]
    hud = main.split("LOOP DO PAINEL", 1)[1]
    assert "Write-StatusOllama" in hud
    assert "Write-StatusDockerDono" in hud
    exemplo = (ROOT / ".env.example").read_text(encoding="utf-8")
    template = (ROOT / ".env_template").read_text(encoding="utf-8")
    assert "USE_OLLAMA_LOCAL=1" in exemplo
    assert "USE_OLLAMA_LOCAL=1" in template
    assert "OLLAMA_BASE_URL=http://localhost:11434" in exemplo
    assert "SUPPORT_CHAT_MODEL=qwen2.5:7b-instruct" in exemplo
    assert "11434" not in (ROOT / "llm" / "docker-compose.yml").read_text(encoding="utf-8")


def test_parar_ollama_respeita_quem_abriu(tmp_path):
    pwsh = shutil.which("pwsh")
    python = shutil.which("python3") or shutil.which("python")
    if not pwsh or not python:
        pytest.skip("pwsh ou python ausente neste ambiente")
    script = (ROOT / "iniciar_servicos.ps1").resolve()
    alheio = tmp_path / "dorme.py"
    alheio.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    binario = tmp_path / "bin"
    binario.mkdir()
    nosso = binario / "ollama"
    nosso.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(120)\n", encoding="utf-8")
    nosso.chmod(0o755)
    runner = tmp_path / "ollama.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script.as_posix()}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and @($parseErrors).Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$nomes = @(
    'Test-LinhaEhOllama','Get-LinhaDeComando','Get-PidsFilhos','Stop-ArvoreProcesso',
    'Test-OllamaHttp','Stop-OllamaLocal','Test-DockerEngine','Test-DockerCli','Stop-DockerDesktopIniciado'
)
$defs = $ast.FindAll({{ param($no) $no -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($nomes -contains $no.Name) }}, $true)
if (@($defs).Count -ne $nomes.Count) {{ throw ('funcoes: ' + @($defs).Count) }}
foreach ($def in $defs) {{ Invoke-Expression $def.Extent.Text }}
if (-not (Test-LinhaEhOllama 'C:\\Ollama\\ollama.exe serve')) {{ throw 'cli do ollama nao reconhecida' }}
if (Test-LinhaEhOllama 'python dorme.py') {{ throw 'processo alheio parece ollama' }}
if (Test-LinhaEhOllama 'C:\\tmp\\test_parar_ollama_x\\dorme.py') {{ throw 'pasta com o nome ollama parece o executavel' }}

$py = '{python}'
$outro = Start-Process -FilePath $py -ArgumentList @('{alheio.as_posix()}') -PassThru
$marca = Start-Process -FilePath $py -ArgumentList @('{nosso.as_posix()}') -PassThru
try {{
    Start-Sleep -Milliseconds 300
    $script:OllamaIniciadoPeloScript = $false
    $script:OllamaPid = $outro.Id
    $script:UrlOllama = 'http://127.0.0.1:9'
    Stop-OllamaLocal
    if (-not (Get-Process -Id $outro.Id -ErrorAction SilentlyContinue)) {{ throw 'flag falsa encerrou processo alheio' }}

    $script:OllamaIniciadoPeloScript = $true
    $script:OllamaPid = $outro.Id
    Stop-OllamaLocal
    if (-not (Get-Process -Id $outro.Id -ErrorAction SilentlyContinue)) {{ throw 'linha sem ollama foi encerrada' }}

    $script:DockerIniciadoPeloScript = $false
    Stop-DockerDesktopIniciado
    if (-not (Get-Process -Id $outro.Id -ErrorAction SilentlyContinue)) {{ throw 'docker com flag falsa encerrou processo alheio' }}
    if (-not (Get-Process -Id $marca.Id -ErrorAction SilentlyContinue)) {{ throw 'docker com flag falsa encerrou o processo marcado' }}

    $script:DockerIniciadoPeloScript = $true
    Stop-DockerDesktopIniciado
    if (-not (Get-Process -Id $outro.Id -ErrorAction SilentlyContinue)) {{ throw 'parada do docker encerrou processo que nao e o Desktop' }}
    if (-not (Get-Process -Id $marca.Id -ErrorAction SilentlyContinue)) {{ throw 'parada do docker encerrou o dummy do ollama' }}

    $script:OllamaIniciadoPeloScript = $true
    $script:OllamaPid = $marca.Id
    Stop-OllamaLocal
    Start-Sleep -Milliseconds 400
    if (Get-Process -Id $marca.Id -ErrorAction SilentlyContinue) {{ throw 'ollama marcado pelo script sobreviveu' }}
    if (-not (Get-Process -Id $outro.Id -ErrorAction SilentlyContinue)) {{ throw 'encerrar o ollama do script matou o processo alheio' }}
}} finally {{
    foreach ($id in @($outro.Id, $marca.Id)) {{
        if (Get-Process -Id $id -ErrorAction SilentlyContinue) {{
            Stop-ArvoreProcesso -ProcessId $id
        }}
    }}
}}
""",
        encoding="utf-8",
    )
    proc = subprocess.run([pwsh, "-NoProfile", "-File", str(runner)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_porta_alheia_nao_e_morta_e_arvore_sim(tmp_path):
    pwsh = shutil.which("pwsh")
    python = shutil.which("python3") or shutil.which("python")
    if not pwsh or not python:
        pytest.skip("pwsh ou python ausente neste ambiente")
    script = (ROOT / "iniciar_servicos.ps1").resolve()
    pidfile = tmp_path / "painel.pid"
    pai_py = tmp_path / "pai.py"
    pai_py.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    runner = tmp_path / "painel.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script.as_posix()}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and @($parseErrors).Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$nomes = @('Test-LinhaEhPainel','Get-LinhaDeComando','Get-PidOuvinte','Get-PidOuvinteProc','Get-PidsFilhos','Test-ArvoreEhPainel','Stop-ArvoreProcesso','Stop-ControlPlane')
$defs = $ast.FindAll({{ param($no) $no -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($nomes -contains $no.Name) }}, $true)
if (@($defs).Count -ne $nomes.Count) {{ throw ('funcoes: ' + @($defs).Count) }}
foreach ($def in $defs) {{ Invoke-Expression $def.Extent.Text }}

if (-not (Test-LinhaEhPainel 'python -m streamlit run control_plane/app.py')) {{ throw 'painel real nao reconhecido' }}
if (Test-LinhaEhPainel 'python -m http.server 9') {{ throw 'http.server parece painel' }}
if (Test-LinhaEhPainel '') {{ throw 'linha vazia parece painel' }}

$py = '{python}'
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = [int]$listener.LocalEndpoint.Port
$listener.Stop()
$alheio = Start-Process -FilePath $py -ArgumentList @('-m','http.server',"$port",'--bind','127.0.0.1') -PassThru
$pai = $null
try {{
    $ouv = 0
    for ($i = 0; $i -lt 20; $i++) {{
        $ouv = Get-PidOuvinte -Port $port
        if ($ouv -gt 0) {{ break }}
        Start-Sleep -Milliseconds 150
    }}
    if ($ouv -le 0) {{ throw 'porta de teste sem ouvinte' }}
    $linha = Get-LinhaDeComando -ProcessId $ouv
    if (Test-LinhaEhPainel $linha) {{ throw ('ouvinte alheio classificado como painel: ' + $linha) }}
    $script:PidPainel = $ouv
    $PATH_PID_PAINEL = '{pidfile.as_posix()}'
    Set-Content -LiteralPath $PATH_PID_PAINEL -Value $ouv -Encoding ASCII
    Stop-ControlPlane
    if (-not (Get-Process -Id $ouv -ErrorAction SilentlyContinue)) {{ throw 'Stop-ControlPlane encerrou processo que nao e o painel' }}

    $pai = Start-Process -FilePath $py -ArgumentList @('{pai_py.as_posix()}') -PassThru
    $filhos = @()
    for ($i = 0; $i -lt 20; $i++) {{
        $filhos = @(Get-PidsFilhos -ProcessId $pai.Id)
        if ($filhos.Count -ge 1) {{ break }}
        Start-Sleep -Milliseconds 150
    }}
    if ($filhos.Count -lt 1) {{ throw 'processo pai nao mostrou filho' }}
    Stop-ArvoreProcesso -ProcessId $pai.Id
    Start-Sleep -Milliseconds 400
    if (Get-Process -Id $pai.Id -ErrorAction SilentlyContinue) {{ throw 'pai sobreviveu a Stop-ArvoreProcesso' }}
    foreach ($filho in $filhos) {{
        if (Get-Process -Id $filho -ErrorAction SilentlyContinue) {{ throw ('filho sobreviveu: ' + $filho) }}
    }}
}} finally {{
    if ($alheio -and (Get-Process -Id $alheio.Id -ErrorAction SilentlyContinue)) {{
        Stop-ArvoreProcesso -ProcessId $alheio.Id
    }}
    if ($pai -and (Get-Process -Id $pai.Id -ErrorAction SilentlyContinue)) {{
        Stop-ArvoreProcesso -ProcessId $pai.Id
    }}
}}
""",
        encoding="utf-8",
    )
    proc = subprocess.run([pwsh, "-NoProfile", "-File", str(runner)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ip_do_n8n_e_descoberto_e_nao_e_fixo():
    text = _script()
    trecho = text.split("function Update-N8nContainerIp", 1)[1].split("function Start-Porteiro", 1)[0]
    assert "docker inspect" in trecho
    assert "n8n_app" in trecho
    assert "n8n-container-ip" in trecho
    assert "172.18." not in trecho
    assert "172.16." not in trecho
    boot = text.split("\nInvoke-AutoSetup\n", 1)[1]
    assert boot.index("docker compose up n8n") < boot.index("Update-N8nContainerIp")
    assert "Update-N8nContainerIp" in text.split("LOOP DO PAINEL", 1)[1]


def test_stacks_boot_vazio_nao_e_placeholder(tmp_path):
    """Vazio é o padrão (só o núcleo). Não pede valor, não gera segredo, não bloqueia."""
    corpo = _script().split("function Test-DevePedirValor", 1)[1].split("function Get-MotivoChave", 1)[0]
    assert '$Key -eq "STACKS_BOOT"' in corpo
    assert corpo.index("STACKS_BOOT") < corpo.index("Test-ValorPlaceholder")
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    script = (ROOT / "iniciar_servicos.ps1").resolve()
    exemplo = tmp_path / ".env.example"
    env = tmp_path / ".env"
    exemplo.write_text(
        "PORTEIRO_USER=seu_usuario\nN8N_ENCRYPTION_KEY=__GENERATE_HEX_32__\nSTACKS_BOOT=\n",
        encoding="utf-8",
    )
    env.write_text(
        "PORTEIRO_USER=seu_usuario\nN8N_ENCRYPTION_KEY=ja-definida-nao-rotacionar\nSTACKS_BOOT=\n",
        encoding="utf-8",
    )
    antes = env.read_bytes()
    runner = tmp_path / "stacks-boot.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script.as_posix()}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and @($parseErrors).Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$nomes = @(
    'Get-EnvFileMap', 'Test-ValorPlaceholder', 'Test-DevePedirValor', 'Get-MotivoChave',
    'Add-Pendencia', 'Confirm-ValoresManuais', 'Get-EnvValue', 'Get-StacksBoot'
)
$defs = $ast.FindAll({{ param($no) $no -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($nomes -contains $no.Name) }}, $true)
if (@($defs).Count -ne $nomes.Count) {{ throw ('funcoes: ' + @($defs).Count) }}
foreach ($def in $defs) {{
    $texto = $def.Extent.Text
    if ($def.Name -eq 'Confirm-ValoresManuais') {{
        $texto = $texto.Replace('$PSScriptRoot', "'{tmp_path.as_posix()}'")
    }}
    Invoke-Expression $texto
}}

$script:Pendencias = New-Object System.Collections.Generic.List[object]
$script:Perguntas = New-Object System.Collections.Generic.List[string]
$script:HostLines = New-Object System.Collections.Generic.List[string]
function Confirm-Sim {{
    param([Parameter(Mandatory = $true)][string]$Pergunta)
    $script:Perguntas.Add($Pergunta) | Out-Null
    if ($Pergunta -match 'STACKS_BOOT') {{ throw ('pediu STACKS_BOOT: ' + $Pergunta) }}
    return $false
}}
function Write-Host {{
    param(
        [Parameter(Position = 0)][object]$Object,
        [object]$ForegroundColor,
        [object]$BackgroundColor,
        [object]$NoNewline,
        [object]$Separator
    )
    $script:HostLines.Add([string]$Object) | Out-Null
}}

function Assert-NaoPede([string]$Atual, [bool]$Tem) {{
    if (Test-DevePedirValor -Key 'STACKS_BOOT' -Current $Atual -Example '' -HasCurrent $Tem) {{
        throw ('STACKS_BOOT pediu valor para [' + $Atual + '] tem=' + $Tem)
    }}
}}
Assert-NaoPede '' $true
Assert-NaoPede '' $false
Assert-NaoPede 'n8n,llm' $true
Assert-NaoPede 'placeholder' $true
if (-not (Test-DevePedirValor -Key 'PORTEIRO_USER' -Current 'seu_usuario' -Example 'seu_usuario' -HasCurrent $true)) {{
    throw 'PORTEIRO_USER deixou de ser pedido'
}}

$PATH_ENV = '{env.as_posix()}'
Confirm-ValoresManuais
$nomesPend = @($script:Pendencias | ForEach-Object {{ $_.Nome }})
if ($nomesPend -contains 'STACKS_BOOT') {{ throw ('pendencia indevida: ' + ($nomesPend -join ',')) }}
if ($nomesPend -notcontains 'PORTEIRO_USER') {{ throw 'PORTEIRO_USER nao entrou na pendencia' }}
$perguntas = $script:Perguntas -join "`n"
if ($perguntas -match 'STACKS_BOOT') {{ throw 'pergunta citou STACKS_BOOT' }}
if ($perguntas -notmatch 'PORTEIRO_USER') {{ throw 'nao perguntou PORTEIRO_USER' }}
$depois = [System.IO.File]::ReadAllBytes($PATH_ENV)
$cruAntes = [System.IO.File]::ReadAllBytes('{env.as_posix()}')
if ($depois.Length -ne $cruAntes.Length) {{ throw 'o .env mudou de tamanho' }}
$textoEnv = [System.IO.File]::ReadAllText($PATH_ENV)
if ($textoEnv -notmatch '(?m)^STACKS_BOOT=$') {{ throw 'STACKS_BOOT deixou de estar vazio' }}
if ($textoEnv -notmatch 'N8N_ENCRYPTION_KEY=ja-definida-nao-rotacionar') {{ throw 'segredo foi rotacionado' }}

function Assert-Boot([string]$Bruto, [string[]]$Esperados, [string]$Aviso, [switch]$Ausente) {{
    $dir = '{tmp_path.as_posix()}'
    $arquivo = Join-Path $dir 'caso.env'
    if ($Ausente) {{
        Set-Content -LiteralPath $arquivo -Value "USE_SCOUT=1" -Encoding utf8
    }} else {{
        Set-Content -LiteralPath $arquivo -Value ("STACKS_BOOT=" + $Bruto) -Encoding utf8
    }}
    $PATH_ENV = $arquivo
    $script:HostLines.Clear()
    $lista = Get-StacksBoot
    if ($null -eq $lista) {{ $lista = @() }}
    $tokens = @($lista)
    if ($tokens.Count -ne $Esperados.Count) {{
        throw ('contagem ' + $tokens.Count + ' para [' + $Bruto + '] = ' + ($tokens -join ','))
    }}
    for ($i = 0; $i -lt $Esperados.Count; $i++) {{
        if ($tokens[$i] -ne $Esperados[$i]) {{ throw ('token ' + $tokens[$i] + ' != ' + $Esperados[$i]) }}
    }}
    $hostTxt = $script:HostLines -join "`n"
    if ($Aviso) {{
        if ($hostTxt -notmatch [regex]::Escape($Aviso)) {{ throw ('sem aviso [' + $Aviso + '] em ' + $hostTxt) }}
    }} else {{
        if ($hostTxt -match 'AVISO') {{ throw ('aviso indevido: ' + $hostTxt) }}
    }}
}}

Assert-Boot -Bruto '' -Esperados @() -Aviso ''
Assert-Boot -Bruto 'n8n,llm' -Esperados @('n8n', 'llm') -Aviso ''
Assert-Boot -Bruto 'n8n, foo ,llm' -Esperados @('n8n', 'llm') -Aviso 'foo'
Assert-Boot -Ausente -Esperados @() -Aviso ''
""",
        encoding="utf-8",
    )
    proc = subprocess.run([pwsh, "-NoProfile", "-File", str(runner)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert env.read_bytes() == antes
