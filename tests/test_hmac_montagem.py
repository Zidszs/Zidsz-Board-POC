"""Chave HMAC em arquivo, segunda origem no IP aprovado e trilha LIBERADO."""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.hmac_arquivo import MOTIVO_AUSENTE, avisar_hmac, estado_hmac  # noqa: E402
from scout.core.politica_portal import montar_politica  # noqa: E402
from scout.core.porta_apps import antes_do_porteiro  # noqa: E402
from scout.core.trilha import Trilha  # noqa: E402

NGROK = "172.18.0.7"
CLIENTE = "203.0.113.47"
NODES = (
    "n8n-nodes-base.executeCommand",
    "n8n-nodes-base.ssh",
    "n8n-nodes-base.readWriteFile",
    "n8n-nodes-base.localFileTrigger",
    "n8n-nodes-base.readBinaryFile",
    "n8n-nodes-base.readBinaryFiles",
    "n8n-nodes-base.writeBinaryFile",
)


def _dns(monkeypatch):
    def ips(nome):
        if nome == "ngrok_service":
            return {NGROK}
        return set()

    monkeypatch.setattr("scout.core.ip_vivo.ips_do_nome", ips)
    monkeypatch.setenv("N8GROKER_PROXY_NOME", "ngrok_service")
    monkeypatch.setenv("PORTEIRO_TRUSTED_PROXIES", "")


class _Fila:
    def __init__(self):
        self.origens = [{"origem": "orig-1", "status": "aprovado", "dispositivo": "aaa"}]
        self.toques = 0

    def consultar(self, ip, origem, conta, app):
        return {"status": "aprovado", "ip": ip, "origens": list(self.origens)}

    def tocar(self, ip):
        self.toques += 1
        return {"ok": True, "ip": ip}

    def registrar(self, ip, origem, dispositivo):
        for item in self.origens:
            if item["origem"] == origem:
                return {"ok": True, "status": item["status"]}
        self.origens.append({"origem": origem, "status": "pendente", "dispositivo": dispositivo})
        return {"ok": True, "status": "pendente"}


def _politica(tmp_path, fila):
    grade = Trilha(tmp_path / "trilha")
    antes = montar_politica(antes_do_porteiro(fila), trilha=grade, fila=fila)
    return antes, grade


def _painel():
    return {
        "caminho": "/painel",
        "headers": {"x-forwarded-for": CLIENTE},
        "query": "",
    }


def test_estado_da_chave_distingue_pasta_arquivo_e_vazio(tmp_path):
    ausente = tmp_path / "nao-existe.key"
    assert estado_hmac(ausente) == "ausente"
    vazio = tmp_path / "vazio.key"
    vazio.write_bytes(b"")
    assert estado_hmac(vazio) == "vazio"
    pasta = tmp_path / "pasta.key"
    pasta.mkdir()
    assert estado_hmac(pasta) == "diretorio"
    cheia = tmp_path / "cheia.key"
    cheia.mkdir()
    (cheia / "dado.txt").write_text("x", encoding="utf-8")
    assert estado_hmac(cheia) == "diretorio"
    boa = tmp_path / "boa.key"
    boa.write_bytes(b"k" * 32)
    assert estado_hmac(boa) == "ok"


def test_startup_registra_erro_quando_a_chave_e_pasta(tmp_path, caplog):
    pasta = tmp_path / "porteiro-hmac.key"
    pasta.mkdir()
    with caplog.at_level(logging.ERROR, logger="scout.hmac"):
        texto = avisar_hmac(pasta)
    assert "diretório" in texto
    assert caplog.records
    assert caplog.records[-1].levelno == logging.ERROR


def test_painel_sem_chave_grava_bloqueio_e_nao_ok(tmp_path, monkeypatch):
    _dns(monkeypatch)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(tmp_path / "ausente.key"))
    fila = _Fila()
    antes, grade = _politica(tmp_path, fila)
    acao = antes(_painel(), NGROK, {})
    assert acao["liga"] is False
    assert acao["status"] == 503
    assert b"chave HMAC" in acao["corpo"]
    assert "injetar_pedido" not in acao
    frases = grade.legiveis(ip=CLIENTE)
    assert frases
    assert frases[-1].endswith(f"BLOQUEIO ({MOTIVO_AUSENTE})")
    assert not frases[-1].endswith("ok")
    assert " > ok" not in frases[-1]


def test_pasta_vazia_no_lugar_da_chave_tambem_bloqueia(tmp_path, monkeypatch):
    _dns(monkeypatch)
    pasta = tmp_path / "porteiro-hmac.key"
    pasta.mkdir()
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(pasta))
    antes, grade = _politica(tmp_path, _Fila())
    acao = antes(_painel(), NGROK, {})
    assert acao["status"] == 503
    assert grade.legiveis(ip=CLIENTE)[-1].endswith(f"BLOQUEIO ({MOTIVO_AUSENTE})")


def test_com_chave_o_painel_injeta_o_cabecalho(tmp_path, monkeypatch):
    _dns(monkeypatch)
    chave = tmp_path / "porteiro-hmac.key"
    chave.write_bytes(b"k" * 32)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(chave))
    antes, grade = _politica(tmp_path, _Fila())
    pedido = _painel()
    pedido["headers"] = dict(pedido["headers"])
    pedido["headers"]["cookie"] = "n8groker_origem=orig-1"
    acao = antes(pedido, NGROK, {})
    assert acao["liga"] is True
    assert acao["injetar_pedido"].startswith(f"x-n8groker-client: {CLIENTE}|")
    assert MOTIVO_AUSENTE not in " ".join(grade.legiveis(ip=CLIENTE))


def test_segunda_origem_no_ip_aprovado_fica_pendente_mesmo_sem_chave(tmp_path, monkeypatch):
    _dns(monkeypatch)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(tmp_path / "ausente.key"))
    fila = _Fila()
    antes, _grade = _politica(tmp_path, fila)
    bloqueio = antes(_painel(), NGROK, {})
    assert bloqueio["status"] == 503
    pagina = antes(
        {"caminho": "/painel/origem.html", "headers": {"x-forwarded-for": CLIENTE}, "query": ""},
        NGROK,
        {},
    )
    assert pagina["status"] == 200
    assert b'<script src="/origem.js"></script>' in pagina["corpo"]
    assert "frame-ancestors 'self'" in "\n".join(pagina["headers"])
    assert b"n8groker_sessao" not in pagina["corpo"]
    registro = antes(
        {
            "caminho": "/painel/registrar-origem",
            "headers": {"x-forwarded-for": CLIENTE},
            "query": "origem=orig-dois&dispositivo=bbbbbbbbbbbbbbbb",
        },
        NGROK,
        {},
    )
    assert registro["status"] == 200
    assert fila.origens[0]["status"] == "aprovado"
    assert fila.origens[1]["origem"] == "orig-dois"
    assert fila.origens[1]["status"] == "pendente"


def test_app_aberto_grava_liberado(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from scout.core.origem import cookies_de_prova, gerar_chave, impressao
    from scout.core.sessao_cookie import caminho_geracao, emitir, garantir, iniciar_usuario

    garantir(tmp_path)
    iniciar_usuario(caminho_geracao(tmp_path), "ana")
    privada = (tmp_path / ".n8groker" / "sessao.key").read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    ec_priv, spki = gerar_chave()
    token = emitir(
        privada,
        {
            "u": "ana",
            "ip": CLIENTE,
            "g": 1,
            "app": "n8n",
            "abrir": ["n8n"],
            "origem": "orig-1",
            "dispositivo": impressao(spki),
            "nonce": "nonce-1",
            "sid": "99" * 8,
        },
    )
    prova = cookies_de_prova(ec_priv, spki, "orig-1", "nonce-1", "2026-10-01T12:00:00Z")

    class Fila:
        def consultar(self, ip, origem, conta, app):
            return {
                "status": "aprovado",
                "ip": ip,
                "conta_vinculada": "ana",
                "origem": "orig-1",
                "vinculo": "ativo",
            }

    grade = Trilha(tmp_path / "trilha")
    fila = Fila()
    antes = montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
        trilha=grade,
    )
    aberto = antes(
        {
            "caminho": "/",
            "headers": {"x-forwarded-for": CLIENTE, "cookie": f"n8groker_sessao={token}; {prova}"},
            "query": "",
        },
        "127.0.0.1",
        {},
    )
    assert aberto["liga"] is True
    assert any(frase.endswith("LIBERADO") for frase in grade.legiveis(ip=CLIENTE))


def test_painel_de_borda_nao_usa_srcdoc():
    texto = (ROOT / "control_plane" / "app.py").read_text(encoding="utf-8")
    assert 'components.iframe("/painel/origem.html"' in texto
    assert "components.html(" not in texto
    borda = (ROOT / "control_plane" / "edge_auth.py").read_text(encoding="utf-8")
    assert "def descrever_chave" in borda
    servico = (SCOUT / "scout" / "server" / "service.py").read_text(encoding="utf-8")
    assert "avisar_hmac" in servico


def test_compose_exclui_shell_e_arquivo_e_deixa_o_code():
    compose = (ROOT / "n8n" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "NODES_EXCLUDE=${NODES_EXCLUDE:-" in compose
    assert "N8N_NODES_EXCLUDE=${N8N_NODES_EXCLUDE:-" not in compose
    for node in NODES:
        assert node in compose
    linha = [item for item in compose.splitlines() if "NODES_EXCLUDE=" in item][0]
    assert "n8n-nodes-base.code" not in linha
    assert "N8N_BLOCK_ENV_ACCESS_IN_NODE=" not in compose
    exemplo = (ROOT / ".env.example").read_text(encoding="utf-8")
    modelo = (ROOT / ".env_template").read_text(encoding="utf-8")
    assert "N8N_NODES_EXCLUDE=" in exemplo
    assert "N8N_NODES_EXCLUDE=" in modelo
    fluxo = (ROOT / "Workflows_para_Autenticação" / "Aprovacao de Acesso (Novo).json").read_text(encoding="utf-8")
    assert "{{ $env.PORTEIRO_N8N_TOKEN }}" in fluxo
    scout = (SCOUT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "porteiro-hmac.key:/run/porteiro-hmac.key:ro" in scout
    assert ".n8groker:/run" not in scout
    assert "admin.key:" not in scout


def _pwsh():
    return shutil.which("pwsh")


def test_script_repara_pasta_vazia_e_confere_o_container(tmp_path):
    pwsh = _pwsh()
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    script = (ROOT / "iniciar_servicos.ps1").resolve()
    texto = script.read_text(encoding="utf-8")
    boot = texto.split("\nInvoke-AutoSetup\n", 1)[1].split("LOOP DO PAINEL", 1)[0]
    assert boot.index("Ensure-ArquivosMontados") < boot.index("Start-Scout")
    assert boot.index("Ensure-ArquivosMontados") < boot.index("COMPOSE_NGROK")
    start = texto.split("function Start-Scout", 1)[1].split("function Write-EnvLinesNoBom", 1)[0]
    assert start.index("Ensure-ArquivosMontados") < start.index('"up", "-d", "--build"')
    assert start.index("Wait-ScoutHealth") < start.index("Assert-MontagensDoScout")
    hud = texto.split("A URL publica do ngrok mudou", 1)[1].split("ReinicioPorUrl", 1)[0]
    assert hud.index("Ensure-ArquivosMontados") < hud.index("COMPOSE_SCOUT")
    binario_fn = texto.split("function Write-BytesAleatorios", 1)[1].split("function Ensure-ArquivoBinario", 1)[0]
    assert "Protect-ArquivoUsuario" in binario_fn
    assert "Write-BytesAleatorios" in texto.split("function Ensure-ArquivoBinario", 1)[1].split("function Ensure-PorteiroTokens", 1)[0]

    binario = tmp_path / "bin"
    binario.mkdir()
    log = tmp_path / "docker.log"
    docker = binario / "docker"
    docker.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\nexit \"${DOCKER_EXIT:-0}\"\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    ausente = tmp_path / "ausente" / "porteiro-hmac.key"
    vazio = tmp_path / "vazio" / "porteiro-hmac.key"
    cheio = tmp_path / "cheio" / "porteiro-hmac.key"
    vazio.parent.mkdir()
    vazio.mkdir()
    cheio.parent.mkdir()
    cheio.mkdir()
    (cheio / "nao-apague.txt").write_text("dado", encoding="utf-8")
    runner = tmp_path / "montagem.ps1"
    runner.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{script.as_posix()}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and @($parseErrors).Count -gt 0) {{ throw ($parseErrors | Out-String) }}
$nomes = @('Protect-ArquivoUsuario', 'Repair-MontagemDeArquivo', 'Write-BytesAleatorios', 'Ensure-ArquivoBinario', 'Assert-ArquivoNoContainer')
$defs = $ast.FindAll({{ param($no) $no -is [System.Management.Automation.Language.FunctionDefinitionAst] -and ($nomes -contains $no.Name) }}, $true)
if (@($defs).Count -ne 5) {{ throw ('funcoes: ' + @($defs).Count) }}
foreach ($def in $defs) {{ Invoke-Expression $def.Extent.Text }}

$ausente = '{ausente.as_posix()}'
Ensure-ArquivoBinario -Caminho $ausente -Rotulo 'porteiro-hmac.key'
if (-not (Test-Path -LiteralPath $ausente -PathType Leaf)) {{ throw 'arquivo ausente nao foi criado' }}
$bytes = [System.IO.File]::ReadAllBytes($ausente)
if ($bytes.Length -ne 32) {{ throw ('tamanho ' + $bytes.Length) }}
Ensure-ArquivoBinario -Caminho $ausente -Rotulo 'porteiro-hmac.key'
$deNovo = [System.IO.File]::ReadAllBytes($ausente)
if ([Convert]::ToBase64String($bytes) -ne [Convert]::ToBase64String($deNovo)) {{ throw 'arquivo valido foi regravado' }}

$vazioArquivo = '{(tmp_path / "zero.key").as_posix()}'
[System.IO.File]::WriteAllBytes($vazioArquivo, (New-Object byte[] 0))
Ensure-ArquivoBinario -Caminho $vazioArquivo -Rotulo 'porteiro-hmac.key'
if ((Get-Item -LiteralPath $vazioArquivo).Length -ne 32) {{ throw 'arquivo vazio nao foi trocado' }}

$vazio = '{vazio.as_posix()}'
$saida = @(Ensure-ArquivoBinario -Caminho $vazio -Rotulo 'porteiro-hmac.key' 6>&1 | ForEach-Object {{ "$_" }}) -join "`n"
if ($saida -notmatch '\\[REPARO\\]') {{ throw ('sem reparo: ' + $saida) }}
if (Test-Path -LiteralPath $vazio -PathType Container) {{ throw 'pasta vazia continuou' }}
if (-not (Test-Path -LiteralPath $vazio -PathType Leaf)) {{ throw 'arquivo nao substituiu a pasta' }}
if ((Get-Item -LiteralPath $vazio).Length -ne 32) {{ throw 'reparo gravou arquivo vazio' }}

$cheio = '{cheio.as_posix()}'
$abortou = $false
try {{
    Repair-MontagemDeArquivo -Caminho $cheio -Rotulo 'porteiro-hmac.key'
}} catch {{
    $abortou = $true
    if ($_.Exception.Message -notmatch 'conteudo') {{ throw $_.Exception.Message }}
}}
if (-not $abortou) {{ throw 'pasta com conteudo nao abortou' }}
if (-not (Test-Path -LiteralPath (Join-Path $cheio 'nao-apague.txt'))) {{ throw 'conteudo foi apagado' }}

$env:PATH = '{binario.as_posix()}' + [IO.Path]::PathSeparator + $env:PATH
$env:DOCKER_LOG = '{log.as_posix()}'
$env:DOCKER_EXIT = '0'
Assert-ArquivoNoContainer -Container 'scout-backend' -Caminho '/run/porteiro-hmac.key' -Conteudo
$chamada = [System.IO.File]::ReadAllText($env:DOCKER_LOG)
if ($chamada -notmatch "exec scout-backend sh -c test -f '/run/porteiro-hmac.key' -a -s '/run/porteiro-hmac.key'") {{
    throw ('docker nao conferiu o arquivo: ' + $chamada)
}}
$env:DOCKER_EXIT = '1'
$falhou = $false
try {{
    Assert-ArquivoNoContainer -Container 'scout-backend' -Caminho '/run/porteiro-hmac.key' -Conteudo
}} catch {{
    $falhou = $true
    if ($_.Exception.Message -notmatch '/run/porteiro-hmac.key') {{ throw $_.Exception.Message }}
}}
if (-not $falhou) {{ throw 'container ruim nao abortou o boot' }}
""",
        encoding="utf-8",
    )
    proc = subprocess.run([pwsh, "-NoProfile", "-File", str(runner)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_navegador_novo_no_ip_aprovado_registra_no_documento_e_escolher_devolve_202(tmp_path, monkeypatch):
    """Sem cookie, /painel não vai ao Streamlit. O prefixo dobrado também não.

    O 202 sai antes de destino_de: o n8n pode estar fechado.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from scout.core.origem import cookies_de_prova, gerar_chave, impressao
    from scout.core.sessao_cookie import caminho_chave, caminho_geracao, garantir, iniciar_usuario
    from scout.core.ticket_sessao import emitir_ticket

    _dns(monkeypatch)
    monkeypatch.delenv("PAINEL_BORDA_HOST", raising=False)
    monkeypatch.delenv("PAINEL_BORDA_PORT", raising=False)
    chave = tmp_path / "porteiro-hmac.key"
    chave.write_bytes(b"k" * 32)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(chave))
    garantir(tmp_path)
    iniciar_usuario(caminho_geracao(tmp_path), "ana")
    privada = caminho_chave(tmp_path).read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()

    class Fila:
        def __init__(self):
            self.origens = [{"origem": "orig-velha", "status": "aprovado", "dispositivo": "aaa"}]

        def consultar(self, ip, origem, conta, app):
            return {
                "status": "aprovado",
                "ip": ip,
                "conta_vinculada": "ana",
                "vinculo": "ativo",
                "origens": list(self.origens),
            }

        def tocar(self, ip):
            return None

        def registrar(self, ip, origem, dispositivo):
            for item in self.origens:
                if item["origem"] == origem:
                    return {"ok": True, "status": item["status"]}
            self.origens.append({"origem": origem, "status": "pendente", "dispositivo": dispositivo})
            return {"ok": True, "status": "pendente"}

    fila = Fila()
    antes, grade = _politica(tmp_path, fila)
    antes = montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
        trilha=grade,
    )

    cru = antes(_painel(), NGROK, {})
    assert cru["liga"] is False
    assert cru["status"] == 200
    assert b"Navegador novo neste IP" in cru["corpo"]
    assert b'<script src="/origem.js"></script>' in cru["corpo"]
    assert b'data-recarregar="agora"' in cru["corpo"]
    assert b"Cole o token" not in cru["corpo"]
    assert "host" not in cru
    assert any("n8groker_desafio=" in item for item in cru.get("headers") or [])
    assert grade.legiveis(ip=CLIENTE) == []

    script = antes(
        {"caminho": "/origem.js", "headers": {"x-forwarded-for": CLIENTE}, "query": ""},
        NGROK,
        {},
    )
    assert script["status"] == 200
    assert b"/painel/registrar-origem" in script["corpo"]
    assert b"Streamlit" not in script["corpo"]

    ec_priv, spki = gerar_chave()
    dispositivo = impressao(spki)
    registro = antes(
        {
            "caminho": "/painel/registrar-origem",
            "headers": {"x-forwarded-for": CLIENTE},
            "query": f"origem=orig-nova&dispositivo={dispositivo}",
        },
        NGROK,
        {},
    )
    assert registro["status"] == 200
    assert fila.origens[0]["status"] == "aprovado"
    assert fila.origens[1]["origem"] == "orig-nova"
    assert fila.origens[1]["status"] == "pendente"

    com_cookie = _painel()
    com_cookie["headers"] = dict(com_cookie["headers"])
    com_cookie["headers"]["cookie"] = "n8groker_origem=orig-nova"
    painel = antes(com_cookie, NGROK, {})
    assert painel["liga"] is True
    assert painel["host"] == "host.docker.internal"
    assert painel["port"] == 8502

    saude = antes(
        {"caminho": "/painel/_stcore/health", "headers": {"x-forwarded-for": CLIENTE}, "query": ""},
        NGROK,
        {},
    )
    assert saude["liga"] is True
    assert saude["port"] == 8502

    dobrado = antes(
        {
            "caminho": "/painel/painel/escolher",
            "headers": {"x-forwarded-for": CLIENTE},
            "query": "app=n8n",
        },
        NGROK,
        {},
    )
    assert dobrado["liga"] is False
    assert dobrado["status"] == 403
    assert b"Cole o token" not in dobrado["corpo"]
    assert "host" not in dobrado

    def _nao_chama_o_app(app):
        raise AssertionError(app)

    monkeypatch.setattr("scout.core.porta_apps.destino_de", _nao_chama_o_app)
    prova = cookies_de_prova(ec_priv, spki, "orig-nova", "nonce-novo", "2026-10-06T12:00:00Z")
    prova += "; n8groker_desafio=nonce-novo"
    ticket = emitir_ticket(chave.read_bytes(), usuario="ana", ip=CLIENTE, geracao=1, abrir=["n8n"])
    for caminho in ("/painel/escolher", "/painel/painel/escolher"):
        espera = antes(
            {
                "caminho": caminho,
                "headers": {"x-forwarded-for": CLIENTE, "cookie": prova},
                "query": "app=n8n&t=" + ticket,
            },
            NGROK,
            {},
        )
        assert espera["liga"] is False, caminho
        assert espera["status"] == 202, caminho
        assert b"Navegador novo neste IP" in espera["corpo"], caminho
        assert "host" not in espera, caminho
        assert "port" not in espera, caminho
    frases = "\n".join(grade.legiveis(ip=CLIENTE))
    assert "LIBERADO" not in frases
    assert "AGUARDANDO" in frases


def test_cookie_sem_fila_e_html_do_painel_voltam_ao_registro(tmp_path, monkeypatch):
    """Cookie gravado antes do 200, ou /painel/painel, não abre o Streamlit."""
    _dns(monkeypatch)
    monkeypatch.delenv("PAINEL_BORDA_HOST", raising=False)
    monkeypatch.delenv("PAINEL_BORDA_PORT", raising=False)
    chave = tmp_path / "porteiro-hmac.key"
    chave.write_bytes(b"k" * 32)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(chave))

    class Fila:
        def __init__(self):
            self.origens = [{"origem": "orig-velha", "status": "aprovado", "dispositivo": "aaa"}]

        def consultar(self, ip, origem, conta, app):
            return {"status": "aprovado", "ip": ip, "origens": list(self.origens)}

        def tocar(self, ip):
            return None

        def registrar(self, ip, origem, dispositivo):
            return {"ok": False}

    fila = Fila()
    antes, _grade = _politica(tmp_path, fila)

    def pedir(caminho, cookie="", accept="", dest="", modo=""):
        headers = {"x-forwarded-for": CLIENTE}
        if cookie:
            headers["cookie"] = cookie
        if accept:
            headers["accept"] = accept
        if dest:
            headers["sec-fetch-dest"] = dest
        if modo:
            headers["sec-fetch-mode"] = modo
        return antes({"caminho": caminho, "headers": headers, "query": ""}, NGROK, {})

    def eh_registro(acao):
        assert acao["liga"] is False
        assert acao["status"] == 200
        assert b"Navegador novo neste IP" in acao["corpo"]
        assert b"Ative o JavaScript para continuar" in acao["corpo"]
        assert b"<noscript>" in acao["corpo"]
        assert "host" not in acao

    recusa = antes(
        {
            "caminho": "/painel/registrar-origem",
            "headers": {"x-forwarded-for": CLIENTE},
            "query": "origem=orig-nova&dispositivo=bbbbbbbbbbbbbbbb",
        },
        NGROK,
        {},
    )
    assert recusa["status"] == 503
    assert [item["origem"] for item in fila.origens] == ["orig-velha"]

    eh_registro(pedir("/painel", "n8groker_origem=orig-nova"))
    assert [item["origem"] for item in fila.origens] == ["orig-velha"]
    fila.origens.append({"origem": "orig-bloqueada", "status": "bloqueado", "dispositivo": "bbb"})
    eh_registro(pedir("/painel", "n8groker_origem=orig-bloqueada"))
    eh_registro(pedir("/painel/painel"))
    eh_registro(pedir("/painel/componente", accept="text/html"))
    eh_registro(pedir("/painel/componente", dest="document"))
    eh_registro(pedir("/painel/outra", modo="navigate"))

    saude = pedir("/painel/_stcore/health", accept="text/html")
    assert saude["liga"] is True
    assert saude["port"] == 8502
    estatico = pedir("/painel/static/app.js", accept="text/html")
    assert estatico["liga"] is True
    aninhado = pedir("/painel/painel/static/app.js", accept="text/html")
    assert aninhado["liga"] is True
    xhr = pedir("/painel/componente", accept="application/json")
    assert xhr["liga"] is True

    fila.origens.append({"origem": "orig-nova", "status": "pendente", "dispositivo": "ccc"})
    pendente = pedir("/painel", "n8groker_origem=orig-nova")
    assert pendente["liga"] is True
    assert pendente["port"] == 8502
    dobrado = pedir("/painel/painel", "n8groker_origem=orig-nova", accept="text/html")
    assert dobrado["liga"] is True
    velha = pedir("/painel", "n8groker_origem=orig-velha")
    assert velha["liga"] is True
