"""A porta do portal lê HTTP e WebSocket. As outras rotas copiam bytes."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.mitm_proxy import MitmProxy, eh_portal, ler_pedido_http, porta_do_portal  # noqa: E402


def test_ler_pedido_http_extrai_caminho_e_upgrade():
    bloco = (
        b"GET /painel?x=1 HTTP/1.1\r\n"
        b"Host: tunel.exemplo\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
    )
    pedido = ler_pedido_http(bloco)
    assert pedido["metodo"] == "GET"
    assert pedido["caminho"] == "/painel"
    assert pedido["headers"]["upgrade"] == "websocket"
    assert ler_pedido_http(b"abc") is None


def test_eh_portal_so_na_porta_publica(monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    assert porta_do_portal() == 4050
    assert eh_portal({"listen_port": "4050"}) is True
    assert eh_portal({"listen_port": 4060}) is False
    assert eh_portal({}) is False


def _rodar(coro):
    return asyncio.run(coro)


async def _servir(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    porta = server.sockets[0].getsockname()[1]
    return server, porta


def test_portal_encaminha_websocket_e_chama_on_pedido(monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    vistos = []
    recebido = {}

    async def upstream(reader, writer):
        data = await reader.read(4096)
        recebido["pedido"] = data
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n\r\n"
            b"pong"
        )
        await writer.drain()
        await asyncio.sleep(0.3)
        writer.close()

    async def cenario():
        bruto, porta = await _servir(upstream)
        proxy = MitmProxy()
        proxy.on_pedido = lambda pedido, ip, entry: vistos.append((pedido["caminho"], ip))
        entry = {
            "id": "porteiro-manual",
            "name": "portal",
            "listen_port": 4050,
            "upstream_host": "127.0.0.1",
            "upstream_port": porta,
            "mode": "http",
        }

        async def portal(reader, writer):
            await proxy._handle_client(reader, writer, entry)

        frente, pporta = await _servir(portal)
        reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
        writer.write(
            b"GET /painel HTTP/1.1\r\n"
            b"Host: tunel\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n\r\n"
        )
        await writer.drain()
        resp = await asyncio.wait_for(reader.read(512), timeout=2)
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()
        writer.close()
        return resp

    resp = _rodar(cenario())
    assert b"101" in resp
    assert b"pong" in resp
    assert b"Upgrade: websocket" in recebido["pedido"]
    assert b"GET /painel" in recebido["pedido"]
    assert vistos and vistos[0][0] == "/painel"


def test_portal_nao_inventa_fim_de_cabecalho(monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    recebido = {}

    async def upstream(reader, writer):
        recebido["pedido"] = await reader.read(256)
        writer.close()

    async def cenario():
        bruto, porta = await _servir(upstream)
        proxy = MitmProxy()
        entry = {
            "id": "porteiro-manual",
            "listen_port": 4050,
            "upstream_host": "127.0.0.1",
            "upstream_port": porta,
            "name": "portal",
            "mode": "http",
        }

        async def portal(reader, writer):
            await proxy._handle_client(reader, writer, entry)

        frente, pporta = await _servir(portal)
        _reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
        writer.write(b"NAO-HTTP-AINDA")
        writer.write_eof()
        await asyncio.wait_for(asyncio.sleep(0.2), timeout=2)
        for _ in range(20):
            if "pedido" in recebido:
                break
            await asyncio.sleep(0.05)
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()
        return recebido.get("pedido", b"")

    assert _rodar(cenario()) == b"NAO-HTTP-AINDA"


def test_rota_que_nao_e_portal_copia_bytes_sem_esperar_http(monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    recebido = {}

    async def upstream(reader, writer):
        recebido["pedido"] = await asyncio.wait_for(reader.read(64), timeout=1)
        writer.write(b"eco")
        await writer.drain()
        writer.close()

    async def cenario():
        bruto, porta = await _servir(upstream)
        proxy = MitmProxy()
        entry = {
            "id": "outro",
            "listen_port": 4060,
            "upstream_host": "127.0.0.1",
            "upstream_port": porta,
            "name": "cru",
            "mode": "tcp",
        }

        async def portal(reader, writer):
            await proxy._handle_client(reader, writer, entry)

        frente, pporta = await _servir(portal)
        reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
        writer.write(b"abc")
        await writer.drain()
        eco = await asyncio.wait_for(reader.read(16), timeout=2)
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()
        writer.close()
        return eco

    assert _rodar(cenario()) == b"eco"
    assert recebido["pedido"] == b"abc"


def test_segundo_pedido_na_mesma_conexao_passa_pela_politica(monkeypatch):
    """O navegador reaproveita a conexão do /painel/. O iframe /painel/sessao
    tem de ser lido de novo. Se o proxy emendar o TCP, o Streamlit responde
    e o Set-Cookie do Scout não existe.
    """
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    linhas = []

    async def upstream(reader, writer):
        data = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
        linhas.append(data.split(b"\r\n", 1)[0])
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nConnection: keep-alive\r\n\r\np")
        await writer.drain()
        try:
            extra = await asyncio.wait_for(reader.read(1024), timeout=0.4)
        except asyncio.TimeoutError:
            extra = b""
        if extra:
            linhas.append(extra.split(b"\r\n", 1)[0])
        writer.close()

    async def cenario():
        bruto, porta = await _servir(upstream)

        def antes(pedido, _ip, _entry):
            if pedido.get("caminho") == "/painel/sessao":
                return {
                    "liga": False,
                    "status": 200,
                    "corpo": b"ok",
                    "tipo": "text/html; charset=utf-8",
                    "headers": [
                        "Set-Cookie: n8groker_sessao=abcd.wxyz; HttpOnly; Secure; Path=/",
                    ],
                }
            return {"liga": True, "host": "127.0.0.1", "port": porta}

        proxy = MitmProxy()
        proxy.antes_de_ligar = antes
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

        frente, pporta = await _servir(portal)
        reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
        writer.write(b"GET /painel/ HTTP/1.1\r\nHost: tunel\r\nConnection: keep-alive\r\n\r\n")
        await writer.drain()
        primeira = await asyncio.wait_for(reader.readexactly(len(b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nConnection: keep-alive\r\n\r\np")), timeout=2)
        writer.write(b"GET /painel/sessao?t=1 HTTP/1.1\r\nHost: tunel\r\nConnection: keep-alive\r\n\r\n")
        await writer.drain()
        segunda = await asyncio.wait_for(reader.read(1024), timeout=2)
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()
        writer.close()
        return primeira, segunda

    primeira, segunda = _rodar(cenario())
    assert b"Content-Length: 1" in primeira
    assert b"n8groker_sessao=abcd.wxyz" in segunda
    assert segunda.startswith(b"HTTP/1.1 200")
    assert linhas == [b"GET /painel/ HTTP/1.1"]


def test_corpo_grande_nao_engole_o_pedido_seguinte(monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    corpo = b"x" * 200000

    async def upstream(reader, writer):
        await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: "
            + str(len(corpo)).encode("ascii")
            + b"\r\n\r\n"
            + corpo
        )
        await writer.drain()
        writer.close()

    async def cenario():
        bruto, porta = await _servir(upstream)

        def antes(pedido, _ip, _entry):
            if pedido.get("caminho") == "/painel/sessao":
                return {
                    "liga": False,
                    "status": 200,
                    "corpo": b"ok",
                    "headers": ["Set-Cookie: n8groker_sessao=abcd.wxyz; HttpOnly; Secure; Path=/"],
                }
            return {"liga": True, "host": "127.0.0.1", "port": porta}

        proxy = MitmProxy()
        proxy.antes_de_ligar = antes
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

        frente, pporta = await _servir(portal)
        reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
        writer.write(b"GET /painel/grande HTTP/1.1\r\nHost: tunel\r\n\r\n")
        await writer.drain()
        cabeca = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
        miolo = await asyncio.wait_for(reader.readexactly(len(corpo)), timeout=2)
        writer.write(b"GET /painel/sessao?t=1 HTTP/1.1\r\nHost: tunel\r\n\r\n")
        await writer.drain()
        segunda = await asyncio.wait_for(reader.read(512), timeout=2)
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()
        writer.close()
        return cabeca, miolo, segunda

    cabeca, miolo, segunda = _rodar(cenario())
    assert b"Content-Length: 200000" in cabeca
    assert miolo == corpo
    assert b"n8groker_sessao=abcd.wxyz" in segunda


def test_set_cookie_no_repasse_nao_corta_o_html_do_n8n(monkeypatch):
    """O n8n responde chunked ou com tamanho. O Set-Cookie da renovação não entra no corpo."""
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    html = b"<html><title>n8n</title></html>"
    cookie = "n8groker_sessao=abcd.wxyz; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=900"

    async def upstream(reader, writer):
        pedido = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
        if b"GET /chunked" in pedido:
            pedaco = format(len(html), "x").encode("ascii") + b"\r\n" + html + b"\r\n0\r\n\r\n"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n" + pedaco
            )
        else:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                + str(len(html)).encode("ascii")
                + b"\r\nConnection: keep-alive\r\n\r\n"
                + html
            )
        await writer.drain()
        writer.close()

    async def um(caminho):
        bruto, porta = await _servir(upstream)

        def antes(pedido, _ip, _entry):
            return {
                "liga": True,
                "host": "127.0.0.1",
                "port": porta,
                "set_cookie": cookie,
            }

        proxy = MitmProxy()
        proxy.antes_de_ligar = antes
        entry = {
            "id": "n8n",
            "listen_port": 4050,
            "upstream_host": "127.0.0.1",
            "upstream_port": 9,
            "name": "portal",
            "mode": "http",
        }

        async def portal(reader, writer):
            await proxy._handle_client(reader, writer, entry)

        frente, pporta = await _servir(portal)
        reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
        writer.write(f"GET {caminho} HTTP/1.1\r\nHost: tunel\r\nConnection: keep-alive\r\n\r\n".encode("ascii"))
        await writer.drain()
        bruto_resp = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
        if b"Transfer-Encoding: chunked" in bruto_resp:
            linha = await asyncio.wait_for(reader.readuntil(b"\r\n"), timeout=2)
            tamanho = int(linha.split(b";", 1)[0], 16)
            miolo = await asyncio.wait_for(reader.readexactly(tamanho), timeout=2)
            await asyncio.wait_for(reader.readexactly(2), timeout=2)
        else:
            miolo = await asyncio.wait_for(reader.readexactly(len(html)), timeout=2)
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()
        writer.close()
        return bruto_resp, miolo

    async def cenario():
        com_tamanho = await um("/tamanho")
        em_chunk = await um("/chunked")
        return com_tamanho, em_chunk

    com_tamanho, em_chunk = _rodar(cenario())
    cabeca, miolo = com_tamanho
    assert b"Set-Cookie: " + cookie.encode("ascii") in cabeca
    assert miolo == html
    cabeca, miolo = em_chunk
    assert b"Set-Cookie: " + cookie.encode("ascii") in cabeca
    assert miolo == html
