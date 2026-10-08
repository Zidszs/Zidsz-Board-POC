"""Cookie em cada pedido e em cada WebSocket. A queda não volta ao painel."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.mitm_proxy import MitmProxy  # noqa: E402
from scout.core.politica_portal import montar_politica  # noqa: E402
from scout.core.porta_apps import APPS, antes_do_porteiro, registrar_app  # noqa: E402
from scout.core.sessao_cookie import (  # noqa: E402
    cabecalho_cookie,
    emitir,
    garantir,
    iniciar_usuario,
    ler_cookie,
    renovar,
    subir_geracao,
    verificar,
    caminho_geracao,
    caminho_chave,
)


class _Fila:
    def consultar(self, ip, origem, conta, app):
        return {
            "status": "aprovado",
            "ip": ip,
            "conta_vinculada": "ana",
            "origem": "orig-1",
            "vinculo": "ativo",
        }


def _chaves(tmp_path):
    garantir(tmp_path)
    iniciar_usuario(caminho_geracao(tmp_path), "ana")
    privada = caminho_chave(tmp_path).read_bytes()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    return privada, publica, caminho_geracao(tmp_path)


def _token(privada, ip="203.0.113.10", app="n8n"):
    from scout.core.origem import cookies_de_prova, gerar_chave, impressao

    ec_priv, spki = gerar_chave()
    dispositivo = impressao(spki)
    token = emitir(
        privada,
        {
            "u": "ana",
            "ip": ip,
            "g": 1,
            "app": app,
            "abrir": ["n8n"],
            "origem": "orig-1",
            "dispositivo": dispositivo,
            "nonce": "nonce-1",
        },
    )
    prova = cookies_de_prova(ec_priv, spki, "orig-1", "nonce-1", "2026-10-01T12:00:00Z")
    return token, prova


def _politica(tmp_path):
    privada, publica, geracoes = _chaves(tmp_path)
    fila_obj = _Fila()
    return (
        montar_politica(antes_do_porteiro(fila_obj), publica, privada, geracoes, fila=fila_obj),
        privada,
        publica,
        geracoes,
    )


def _rodar(coro):
    return asyncio.run(coro)


async def _servir(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_renovacao_deslizante_aumenta_o_prazo(tmp_path):
    privada, publica, geracoes = _chaves(tmp_path)
    token, _prova = _token(privada)
    primeiro = verificar(token, publica, geracoes, "203.0.113.10")
    assert primeiro is not None
    novo = renovar(primeiro, privada, agora=primeiro["iat"] + 30)
    segundo = verificar(novo, publica, geracoes, "203.0.113.10", agora=primeiro["iat"] + 30)
    assert segundo["exp"] > primeiro["exp"]
    assert segundo["g"] == 1
    assert segundo["app"] == "n8n"
    assert segundo["origem"] == "orig-1"
    assert "HttpOnly" in cabecalho_cookie(novo)
    assert "Secure" in cabecalho_cookie(novo)


def test_ip_diferente_e_geracao_velha_recusam(tmp_path):
    privada, publica, geracoes = _chaves(tmp_path)
    token, _prova = _token(privada)
    assert verificar(token, publica, geracoes, "203.0.113.99") is None
    subir_geracao(geracoes, "ana")
    assert verificar(token, publica, geracoes, "203.0.113.10") is None


def test_revogacao_sobe_a_geracao_e_a_lista_nao(tmp_path):
    from control_plane.auth import create_user, desativar_conta, load_users, update_lists
    from control_plane.user_token import conferir, emitir, revogar_jti
    from scout.core.sessao_cookie import ler_geracao

    create_user(tmp_path, "ana", ver=["n8n"], operar=[], abrir=["n8n"])
    privada, publica, geracoes = _chaves(tmp_path)
    cookie, _prova = _token(privada)
    assert verificar(cookie, publica, geracoes, "203.0.113.10") is not None
    jwt = emitir(tmp_path, "ana", ver=["n8n"], operar=[], abrir=["n8n"], por="admin", ip="127.0.0.1")
    geracao = ler_geracao(geracoes, "ana")["g"]
    revogar_jti(tmp_path, conferir(tmp_path, jwt)["jti"], por="admin", ip="127.0.0.1")
    assert verificar(cookie, publica, geracoes, "203.0.113.10") is None
    assert ler_geracao(geracoes, "ana")["g"] == geracao + 1
    update_lists(tmp_path, "ana", ver=["n8n"], operar=["n8n"], abrir=["n8n"])
    assert ler_geracao(geracoes, "ana")["g"] == geracao + 1
    desativar_conta(tmp_path, "ana")
    assert load_users(tmp_path)[0]["status"] == "inativo"
    assert ler_geracao(geracoes, "ana")["ativo"] is False


async def _cenario_raiz(politica):
    conexoes = []

    async def upstream(reader, writer):
        conexoes.append(1)
        writer.close()

    bruto, porta = await _servir(upstream)
    proxy = MitmProxy()
    proxy.antes_de_ligar = politica
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
    reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
    writer.write(b"GET / HTTP/1.1\r\nHost: tunel\r\nX-Forwarded-For: 203.0.113.10\r\n\r\n")
    await writer.drain()
    corpo = await asyncio.wait_for(reader.read(1024), timeout=2)
    frente.close()
    bruto.close()
    await frente.wait_closed()
    await bruto.wait_closed()
    return corpo, conexoes


def test_raiz_sem_cookie_nao_abre_upstream(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    politica, *_ = _politica(tmp_path)
    corpo, conexoes = _rodar(_cenario_raiz(politica))
    assert corpo.startswith(b"HTTP/1.1 403")
    assert b"painel" not in corpo.lower()
    assert conexoes == []


async def _troca(proxy, entry, bloco):
    async def portal(reader, writer):
        await proxy._handle_client(reader, writer, entry)

    frente, porta = await _servir(portal)
    reader, writer = await asyncio.open_connection("127.0.0.1", porta)
    writer.write(bloco)
    await writer.drain()
    corpo = await asyncio.wait_for(reader.read(2048), timeout=2)
    writer.close()
    frente.close()
    await frente.wait_closed()
    return corpo


def test_websocket_reconecta_no_mesmo_app_sem_ir_ao_painel(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    politica, privada, publica, geracoes = _politica(tmp_path)
    token, prova = _token(privada)
    vistos = []

    async def upstream(reader, writer):
        data = await reader.read(1024)
        vistos.append(data)
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()

    async def cenario():
        bruto, porta = await _servir(upstream)
        proxy = MitmProxy()
        proxy.antes_de_ligar = politica
        entry = {
            "id": "porteiro-manual",
            "listen_port": 4050,
            "upstream_host": "127.0.0.1",
            "upstream_port": porta,
            "name": "portal",
            "mode": "http",
        }
        bloco = (
            b"GET / HTTP/1.1\r\nHost: tunel\r\n"
            + f"X-Forwarded-For: 203.0.113.10\r\nCookie: n8groker_sessao={token}; {prova}\r\n".encode()
            + b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
        )
        registrar_app("n8n", "127.0.0.1", porta)
        primeira = await _troca(proxy, entry, bloco)
        segunda = await _troca(proxy, entry, bloco)
        bruto.close()
        await bruto.wait_closed()
        return primeira, segunda

    antes = APPS["n8n"]
    try:
        primeira, segunda = _rodar(cenario())
    finally:
        APPS["n8n"] = antes
    assert primeira.startswith(b"HTTP/1.1 101")
    assert segunda.startswith(b"HTTP/1.1 101")
    assert b"location: /painel" not in primeira.lower()
    assert b"location: /painel" not in segunda.lower()
    assert b"n8groker_sessao=" in primeira.lower()
    assert len(vistos) == 2
    linha = next(
        item for item in primeira.split(b"\r\n") if item.lower().startswith(b"set-cookie:")
    )
    novo = ler_cookie(linha.split(b":", 1)[1].decode("iso-8859-1"))
    assert verificar(novo, publica, geracoes, "203.0.113.10")["app"] == "n8n"


def test_websocket_sem_cookie_nao_completa_101(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    politica, *_ = _politica(tmp_path)

    async def upstream(reader, writer):
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
        await writer.drain()

    async def cenario():
        bruto, porta = await _servir(upstream)
        proxy = MitmProxy()
        proxy.antes_de_ligar = politica
        entry = {
            "id": "porteiro-manual",
            "listen_port": 4050,
            "upstream_host": "127.0.0.1",
            "upstream_port": porta,
            "name": "portal",
            "mode": "http",
        }
        corpo = await _troca(
            proxy,
            entry,
            b"GET / HTTP/1.1\r\nHost: tunel\r\nX-Forwarded-For: 203.0.113.10\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n",
        )
        bruto.close()
        await bruto.wait_closed()
        return corpo

    corpo = _rodar(cenario())
    assert corpo.startswith(b"HTTP/1.1 403")
    assert b"101" not in corpo


def test_revogacao_corta_o_proximo_quadro(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    politica, privada, _publica, geracoes = _politica(tmp_path)
    token, prova = _token(privada)
    recebido = bytearray()
    liberar = asyncio.Event()

    async def upstream(reader, writer):
        while b"UM" not in recebido:
            pedaco = await asyncio.wait_for(reader.read(64), timeout=2)
            if not pedaco:
                break
            recebido.extend(pedaco)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        liberar.set()
        extra = await reader.read(64)
        if extra:
            recebido.extend(extra)
        writer.close()

    async def cenario():
        bruto, porta = await _servir(upstream)
        registrar_app("n8n", "127.0.0.1", porta)
        proxy = MitmProxy()
        proxy.antes_de_ligar = politica
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
        writer.write(
            (
                b"POST / HTTP/1.1\r\nHost: tunel\r\nX-Forwarded-For: 203.0.113.10\r\n"
                + f"Cookie: n8groker_sessao={token}; {prova}\r\n".encode()
                + b"Content-Length: 7\r\n\r\nUM"
            )
        )
        await writer.drain()
        await asyncio.wait_for(liberar.wait(), timeout=2)
        subir_geracao(geracoes, "ana")
        writer.write(b"DOIS!!")
        await writer.drain()
        await asyncio.sleep(0.3)
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()

    antes = APPS["n8n"]
    try:
        _rodar(cenario())
    finally:
        APPS["n8n"] = antes
    assert b"UM" in recebido
    assert b"DOIS" not in recebido
