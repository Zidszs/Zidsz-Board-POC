"""O Porteiro é consultado antes do upstream de cada app."""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.ip_vivo import decidir_ip  # noqa: E402
from scout.core.mitm_proxy import MitmProxy  # noqa: E402
from scout.core.porta_apps import (  # noqa: E402
    APPS,
    FilaHttp,
    antes_do_porteiro,
    exige_porteiro,
    registrar_app,
)

APPS_ORIGINAIS = dict(APPS)


def setup_function():
    APPS.clear()
    APPS.update(APPS_ORIGINAIS)


def teardown_function():
    APPS.clear()
    APPS.update(APPS_ORIGINAIS)


def _aprovado(origem="orig-1", conta="ana"):
    return {
        "status": "aprovado",
        "ip": "203.0.113.10",
        "conta_vinculada": conta,
        "origem": origem,
        "vinculo": "ativo",
    }


def _consultar(registro):
    chamadas = []

    def consultar(ip, origem, conta, app):
        chamadas.append({"ip": ip, "origem": origem, "conta": conta, "app": app})
        return registro

    return consultar, chamadas


@pytest.mark.parametrize("app", ["n8n", "litellm", "langfuse", "outro"])
def test_cada_app_consulta_o_porteiro_antes_de_ligar(app):
    if app == "outro":
        registrar_app("outro", "127.0.0.1", 4010)
    consultar, chamadas = _consultar(_aprovado())
    acao = exige_porteiro(app, "203.0.113.10", "ana", "orig-1", consultar)
    assert chamadas == [{"ip": "203.0.113.10", "origem": "orig-1", "conta": "ana", "app": app}]
    assert acao["liga"] is True
    assert acao["consultou"] is True
    assert acao["host"] == APPS[app][0]
    assert acao["port"] == APPS[app][1]


@pytest.mark.parametrize("app", ["n8n", "litellm", "langfuse", "outro"])
@pytest.mark.parametrize(
    "registro,origem,conta",
    [
        ({"status": "pendente"}, "orig-1", "ana"),
        ({"status": "bloqueado"}, "orig-1", "ana"),
        (_aprovado(origem="outra"), "orig-1", "ana"),
        (_aprovado(conta="bia"), "orig-1", "ana"),
        (_aprovado(), "", "ana"),
        (_aprovado(), "orig-1", ""),
    ],
)
def test_cada_app_sem_a_triade_nao_abre_upstream(app, registro, origem, conta):
    if app == "outro":
        registrar_app("outro", "127.0.0.1", 4010)
    consultar, chamadas = _consultar(registro)
    acao = exige_porteiro(app, "203.0.113.10", conta, origem, consultar)
    assert chamadas and chamadas[0]["app"] == app
    assert acao["liga"] is False
    assert acao["consultou"] is True
    assert "host" not in acao
    assert b"painel" not in acao["corpo"].lower()


def test_ip_bloqueado_na_fila_nao_abre_o_pipe():
    class Fila:
        def consultar(self, ip, origem, conta, app):
            return {"status": "bloqueado", "ip": ip}

    acao = antes_do_porteiro(Fila())(
        {"headers": {"x-forwarded-for": "203.0.113.10"}},
        "127.0.0.1",
        {},
    )
    assert acao["liga"] is False
    assert acao["status"] == 403


def test_origem_nova_no_ip_aprovado_diz_navegador_novo_e_nao_analise_do_ip():
    registro = {
        "status": "aprovado",
        "ip": "203.0.113.10",
        "conta_vinculada": "ana",
        "vinculo": "ativo",
        "origens": [
            {"origem": "antiga", "status": "aprovado"},
            {"origem": "nova-origem", "status": "pendente"},
        ],
    }
    consultar, _chamadas = _consultar(registro)
    acao = exige_porteiro("n8n", "203.0.113.10", "ana", "nova-origem", consultar)
    assert acao["liga"] is False
    assert acao["status"] == 202
    corpo = acao["corpo"]
    assert b"Navegador novo neste IP" in corpo
    assert b"203.0.113.10" in corpo
    assert b"Acesso em Analise" not in corpo
    assert b"foi enviado" not in corpo
    assert b"painel" not in corpo.lower()
    bloqueada = {
        **registro,
        "origens": [{"origem": "nova-origem", "status": "bloqueado"}],
    }
    consultar_bloqueio, _ = _consultar(bloqueada)
    negado = exige_porteiro("n8n", "203.0.113.10", "ana", "nova-origem", consultar_bloqueio)
    assert negado["status"] == 403
    assert b"Navegador novo neste IP" not in negado["corpo"]


def test_ip_aprovado_deixa_o_pipe_seguir_mesmo_com_origem_pendente():
    class Fila:
        def consultar(self, ip, origem, conta, app):
            return {
                "status": "aprovado",
                "ip": ip,
                "origens": [{"origem": "nova", "status": "pendente"}],
            }

    acao = antes_do_porteiro(Fila())(
        {"headers": {"x-forwarded-for": "203.0.113.10"}},
        "127.0.0.1",
        {},
    )
    assert acao["liga"] is True
    assert acao.get("status") != 202


def test_ip_aprovado_deixa_o_pipe_seguir():
    class Fila:
        def consultar(self, ip, origem, conta, app):
            assert ip == "203.0.113.10"
            assert app == "fila"
            return {"status": "aprovado", "ip": ip}

    acao = antes_do_porteiro(Fila())(
        {"headers": {"x-forwarded-for": "203.0.113.10"}},
        "127.0.0.1",
        {},
    )
    assert acao["liga"] is True
    assert "host" not in acao


def test_fila_sem_token_nao_libera():
    fila = FilaHttp("http://127.0.0.1:9/n8n/fila", "")
    assert fila.consultar("203.0.113.10", "", "", "n8n") is None


def test_decidir_ip_le_xff_no_loopback_e_recusa_docker():
    visto = decidir_ip("127.0.0.1", "203.0.113.10, 10.1.1.1", [])
    assert visto["acao"] == "visitante"
    assert visto["ip"] == "203.0.113.10"
    docker = decidir_ip("172.18.0.5", "203.0.113.10", [])
    assert docker["acao"] == "negar"
    assert docker["ip"] == ""


def _rodar(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("app", ["n8n", "litellm", "langfuse", "outro"])
def test_portal_nao_conecta_o_app_sem_aprovacao(app, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    conexoes = []

    async def upstream(reader, writer):
        conexoes.append(1)
        writer.close()

    async def cenario():
        bruto = await asyncio.start_server(upstream, "127.0.0.1", 0)
        porta = bruto.sockets[0].getsockname()[1]
        registrar_app(app, "127.0.0.1", porta)
        proxy = MitmProxy()

        def consultar(ip, origem, conta, app_id):
            return {"status": "pendente", "ip": ip}

        def antes(pedido, client_ip, entry):
            return exige_porteiro(app, "203.0.113.10", "ana", "orig-1", consultar)

        proxy.antes_de_ligar = antes
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

        frente = await asyncio.start_server(portal, "127.0.0.1", 0)
        pporta = frente.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
        writer.write(b"GET / HTTP/1.1\r\nHost: tunel\r\n\r\n")
        await writer.drain()
        corpo = await asyncio.wait_for(reader.read(1024), timeout=2)
        frente.close()
        bruto.close()
        await frente.wait_closed()
        await bruto.wait_closed()
        return corpo

    corpo = _rodar(cenario())
    assert corpo.startswith(b"HTTP/1.1 202")
    assert conexoes == []
    assert b"painel" not in corpo.lower()


def test_compose_consulta_a_fila_sem_montar_chave():
    texto = (SCOUT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "porteiro-painel.token:/run/porteiro-painel.token:ro" in texto
    assert "PORTEIRO_FILA_URL=http://host.docker.internal:5676/n8n/fila" in texto
    assert "admin.key:" not in texto
    assert "sessao.key:/run/sessao.key:ro" in texto
    servico = (SCOUT / "scout" / "server" / "service.py").read_text(encoding="utf-8")
    assert "antes_de_ligar" in servico
