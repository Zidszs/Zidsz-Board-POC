"""A sessão escolhe o app. Fora de `abrir`, o Scout nega sem chamar o Porteiro."""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.mitm_proxy import MitmProxy  # noqa: E402
from scout.core.politica_portal import montar_politica  # noqa: E402
from scout.core.porta_apps import APPS, antes_do_porteiro, registrar_app  # noqa: E402
from scout.core.sessao_cookie import (  # noqa: E402
    caminho_chave,
    caminho_geracao,
    emitir,
    garantir,
    iniciar_usuario,
    ler_cookie,
)

ORIGINAIS = dict(APPS)


def setup_function():
    APPS.clear()
    APPS.update(ORIGINAIS)


def teardown_function():
    APPS.clear()
    APPS.update(ORIGINAIS)


class Fila:
    def __init__(self, registro=None):
        self.chamadas = []
        self.registro = registro or {
            "status": "aprovado",
            "conta_vinculada": "ana",
            "origem": "orig-1",
            "vinculo": "ativo",
        }

    def consultar(self, ip, origem, conta, app):
        self.chamadas.append(app)
        return dict(self.registro, ip=ip)


def _par(tmp_path):
    garantir(tmp_path)
    iniciar_usuario(caminho_geracao(tmp_path), "ana")
    privada = caminho_chave(tmp_path).read_bytes()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    return privada, publica


def _politica(tmp_path, fila, abrir, app="", origem="orig-1"):
    from scout.core.origem import cookies_de_prova, gerar_chave, impressao

    privada, publica = _par(tmp_path)
    ec_priv, spki = gerar_chave()
    dispositivo = impressao(spki)
    token = emitir(
        privada,
        {
            "u": "ana",
            "ip": "203.0.113.10",
            "g": 1,
            "app": app,
            "abrir": abrir,
            "origem": origem,
            "dispositivo": dispositivo,
            "nonce": "nonce-1",
        },
    )
    prova = cookies_de_prova(ec_priv, spki, origem, "nonce-1", "2026-10-01T12:00:00Z")
    politica = montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
    )
    return politica, token, prova


def _pedido(caminho, token, query="", prova=""):
    cookie = f"n8groker_sessao={token}"
    if prova:
        cookie += "; " + prova
    return {
        "caminho": caminho,
        "query": query,
        "headers": {
            "x-forwarded-for": "203.0.113.10",
            "cookie": cookie,
        },
    }


def test_conta_com_n8n_nega_os_outros_sem_porteiro(tmp_path):
    fila = Fila()
    politica, token, prova = _politica(tmp_path, fila, ["n8n"], app="")
    for app in ("litellm", "langfuse"):
        acao = politica(_pedido("/painel/escolher", token, "app=" + app, prova), "127.0.0.1", {})
        assert acao["liga"] is False
        assert acao["status"] == 403
        assert b"painel" not in acao["corpo"].lower()
    assert fila.chamadas == []
    sem_n8n = Fila()
    politica2, token2, prova2 = _politica(tmp_path, sem_n8n, ["langfuse"], app="")
    negado = politica2(_pedido("/painel/escolher", token2, "app=n8n", prova2), "127.0.0.1", {})
    assert negado["status"] == 403
    assert sem_n8n.chamadas == []


def test_raiz_sem_app_nao_abre_n8n(tmp_path):
    fila = Fila()
    politica, token, prova = _politica(tmp_path, fila, ["n8n"], app="")
    acao = politica(_pedido("/", token, prova=prova), "127.0.0.1", {})
    assert acao["status"] == 403
    assert fila.chamadas == []
    assert "host" not in acao


def test_escolher_so_redireciona_depois_do_porteiro(tmp_path):
    fila = Fila()
    politica, token, prova = _politica(tmp_path, fila, ["n8n", "litellm", "langfuse"], app="")
    acao = politica(_pedido("/painel/escolher", token, "app=litellm", prova), "127.0.0.1", {})
    assert fila.chamadas == ["litellm"]
    assert acao["status"] == 303
    assert acao["liga"] is False
    assert "Location: /" in acao["headers"]
    cookie = ler_cookie(acao["headers"][1].split(":", 1)[1])
    from scout.core.sessao_cookie import verificar

    publica = _par(tmp_path)[1]
    claims = verificar(cookie, publica, caminho_geracao(tmp_path), "203.0.113.10")
    assert claims["app"] == "litellm"


@pytest.mark.parametrize("app", ["n8n", "litellm", "langfuse", "outro"])
def test_cada_app_so_conecta_depois_da_consulta(app, tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    fila = Fila()
    politica, token, prova = _politica(tmp_path, fila, [app], app=app)
    conexoes = []

    async def upstream(reader, writer):
        conexoes.append(await reader.read(128))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    async def cenario():
        bruto = await asyncio.start_server(upstream, "127.0.0.1", 0)
        porta = bruto.sockets[0].getsockname()[1]
        registrar_app(app, "127.0.0.1", porta)
        proxy = MitmProxy()
        proxy.antes_de_ligar = politica
        entry = {
            "id": "porteiro-manual",
            "listen_port": 4050,
            "upstream_host": "127.0.0.1",
            "upstream_port": 9,
            "name": "portal",
            "mode": "http",
        }

        async def portal(reader, writer):
            await proxy._handle_client(reader, writer, entry)

        frente = await asyncio.start_server(portal, "127.0.0.1", 0)
        pporta = frente.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
        writer.write(
            (
                b"GET / HTTP/1.1\r\nHost: tunel\r\nX-Forwarded-For: 203.0.113.10\r\n"
                + f"Cookie: n8groker_sessao={token}; {prova}\r\n\r\n".encode()
            )
        )
        await writer.drain()
        corpo = await asyncio.wait_for(reader.read(256), timeout=2)
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()
        return corpo

    corpo = asyncio.run(cenario())
    assert corpo.startswith(b"HTTP/1.1 200")
    assert b"location: /painel" not in corpo.lower()
    assert fila.chamadas[0] == app
    assert conexoes and b"GET /" in conexoes[0]


def test_papel_ver_nao_opera(monkeypatch):
    from control_plane.actor import allows, set_actor

    monkeypatch.setenv("PANEL_MODE", "edge")
    set_actor({"username": "ana", "ver": ["n8n"], "operar": [], "abrir": [], "admin": False})
    try:
        assert allows("ver", "n8n") is True
        assert allows("operar", "n8n") is False
    finally:
        set_actor(None)


def test_atalho_da_borda_volta_ao_painel():
    texto = (ROOT / "control_plane" / "app.py").read_text(encoding="utf-8")
    assert "/painel/escolher?app=" in texto


def test_abrir_do_acesso_rapido_aponta_para_o_topo(monkeypatch):
    """O link_button do Streamlit usa target=_blank. O clique fica em /painel/."""
    import control_plane.app as painel

    vistos = []

    class _Tela:
        def markdown(self, texto, unsafe_allow_html=False):
            vistos.append(("markdown", texto, unsafe_allow_html))

        def link_button(self, *args, **kwargs):
            vistos.append(("link", args, kwargs))

    monkeypatch.setattr(painel, "st", _Tela())
    painel._link("Abrir", "/painel/escolher?app=n8n&t=abc")
    assert vistos[0][0] == "markdown"
    assert vistos[0][2] is True
    html = vistos[0][1]
    assert 'target="_top"' in html
    assert 'href="/painel/escolher?app=n8n&amp;t=abc"' in html
    assert "_blank" not in html
    painel._link("Scout", "http://127.0.0.1:8501")
    assert vistos[-1][0] == "link"


def _cookie_de(bruto: bytes, nome: str) -> str:
    cabeca = bruto.split(b"\r\n\r\n", 1)[0].decode("latin1")
    for linha in cabeca.split("\r\n"):
        if linha.lower().startswith("set-cookie:"):
            valor = ler_cookie(linha.split(":", 1)[1], nome)
            if valor:
                return valor
    return ""


def test_iframe_de_sessao_nao_recarrega_o_painel_e_abrir_libera(tmp_path, monkeypatch):
    """Porteiro assina o cabeçalho, o iframe grava o cookie e Abrir chega em LIBERADO.

    /painel/sessao não pode responder 303 Location: /painel. O iframe seguiria
    esse endereço, o Streamlit do pai perderia a sessão e nenhuma visita ficaria LIBERADO.
    """
    from scout.core.origem import cookies_de_prova, gerar_chave
    from scout.core.ticket_sessao import emitir_ticket
    from scout.core.trilha import Trilha

    ip = "203.0.113.10"
    chave = tmp_path / "porteiro-hmac.key"
    chave.write_bytes(b"k" * 32)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(chave))
    monkeypatch.setenv("PAINEL_BORDA_HOST", "127.0.0.1")
    fila = Fila(
        {
            "status": "aprovado",
            "conta_vinculada": "ana",
            "origem": "orig-1",
            "vinculo": "ativo",
            "origens": [{"origem": "orig-1", "status": "aprovado"}],
        }
    )
    grade = Trilha(tmp_path / "trilha")
    privada, publica = _par(tmp_path)
    ec_priv, spki = gerar_chave()
    prova = cookies_de_prova(ec_priv, spki, "orig-1", "nonce-1", "2026-10-01T12:00:00Z")
    prova += "; n8groker_desafio=nonce-1"
    ticket = emitir_ticket(chave.read_bytes(), usuario="ana", ip=ip, geracao=1, abrir=["n8n"])
    politica = montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
        trilha=grade,
    )

    async def cenario():
        async def upstream(reader, writer):
            pedido = await reader.read(4096)
            conexoes.append(pedido)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
            writer.close()

        conexoes = []
        bruto = await asyncio.start_server(upstream, "127.0.0.1", 0)
        porta = bruto.sockets[0].getsockname()[1]
        monkeypatch.setenv("PAINEL_BORDA_PORT", str(porta))
        registrar_app("n8n", "127.0.0.1", porta)
        proxy = MitmProxy()
        proxy.antes_de_ligar = politica
        entry = {
            "id": "porteiro-manual",
            "listen_port": 4050,
            "upstream_host": "127.0.0.1",
            "upstream_port": 9,
            "name": "portal",
            "mode": "http",
        }

        async def portal(reader, writer):
            await proxy._handle_client(reader, writer, entry)

        frente = await asyncio.start_server(portal, "127.0.0.1", 0)
        pporta = frente.sockets[0].getsockname()[1]

        async def pedir(linhas: bytes) -> bytes:
            reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
            writer.write(linhas)
            await writer.drain()
            pedacos = []
            while True:
                bloco = await asyncio.wait_for(reader.read(8192), timeout=2)
                if not bloco:
                    break
                pedacos.append(bloco)
            writer.close()
            return b"".join(pedacos)

        comum = (
            b"Host: tunel\r\n"
            b"Accept: text/html\r\n"
            b"Sec-Fetch-Mode: navigate\r\n"
            b"Sec-Fetch-Site: same-origin\r\n"
            + f"X-Forwarded-For: {ip}\r\n".encode()
        )
        await pedir(
            b"GET /painel/ HTTP/1.1\r\n"
            + comum
            + b"Sec-Fetch-Dest: document\r\n"
            + f"Cookie: {prova}\r\n\r\n".encode()
        )
        sessao = await pedir(
            b"GET /painel/sessao?t="
            + ticket.encode("ascii")
            + b" HTTP/1.1\r\n"
            + comum
            + b"Sec-Fetch-Dest: iframe\r\n"
            + f"Cookie: {prova}\r\n\r\n".encode()
        )
        cookie = _cookie_de(sessao, "n8groker_sessao")
        escolher = b""
        raiz = b""
        if cookie:
            escolher = await pedir(
                b"GET /painel/escolher?app=n8n HTTP/1.1\r\n"
                + comum
                + b"Sec-Fetch-Dest: document\r\n"
                + f"Cookie: n8groker_sessao={cookie}; {prova}\r\n\r\n".encode()
            )
            seguinte = _cookie_de(escolher, "n8groker_sessao") or cookie
            raiz = await pedir(
                b"GET / HTTP/1.1\r\n"
                + comum
                + b"Sec-Fetch-Dest: document\r\n"
                + f"Cookie: n8groker_sessao={seguinte}; {prova}\r\n\r\n".encode()
            )
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()
        return sessao, escolher, raiz, conexoes

    sessao, escolher, raiz, conexoes = asyncio.run(cenario())
    cabeca = sessao.split(b"\r\n\r\n", 1)[0].lower()
    assert sessao.startswith(b"HTTP/1.1 200")
    assert b"location:" not in cabeca
    assert b"n8groker_sessao=" in sessao
    assert b"cache-control: no-store" in cabeca
    corpo = sessao.split(b"\r\n\r\n", 1)[1]
    assert corpo
    assert b"streamlit" not in corpo.lower()
    assert escolher.startswith(b"HTTP/1.1 303")
    assert b"location: /" in escolher.lower()
    assert raiz.startswith(b"HTTP/1.1 200")
    assert conexoes and f"x-n8groker-client: {ip}|".encode() in conexoes[0]
    assert b"GET /painel/" in conexoes[0]
    assert any(b"GET / HTTP" in pedido for pedido in conexoes)
    frases = grade.legiveis(ip=ip)
    assert any(frase.endswith("LIBERADO") for frase in frases)
    vivas = grade.sessoes()
    assert len(vivas) == 1
    assert vivas[0]["sid"]
    assert vivas[0]["conta"] == "ana"
