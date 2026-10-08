"""A página 202 registra a origem antes do Aprovar, e cada /painel soma a visita."""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.mitm_proxy import MitmProxy  # noqa: E402
from scout.core.origem import anotar_origem, cookies_de_prova, gerar_chave, impressao  # noqa: E402
from scout.core.politica_portal import montar_politica  # noqa: E402
from scout.core.porta_apps import APPS, FilaHttp, antes_do_porteiro  # noqa: E402
from scout.core.sessao_cookie import (  # noqa: E402
    caminho_chave,
    caminho_geracao,
    emitir,
    garantir,
    iniciar_usuario,
    ler_cookie,
)
from scout.core.trilha import Trilha  # noqa: E402

IP = "203.0.113.81"
OUTRO = "203.0.113.68"
TOKEN = "token-teste"
ORIGEM = "orig-flow"
DISPOSITIVO = "abcdef0123456789"


class _Estado:
    def __init__(self):
        self.visitantes = {}
        self.pedidos = []


def _aprovar(registro, origem):
    if not origem:
        return 400, "Origem obrigatoria. Dispositivo nao aprovado."
    if not registro:
        return 404, "Erro: O IP nao esta na fila de espera."
    item = next(
        (candidato for candidato in registro.get("origens") or [] if candidato and candidato.get("origem") == origem),
        None,
    )
    if not item:
        return 404, "Origem nao registrada."
    item["status"] = "aprovado"
    registro["status"] = "aprovado"
    return 200, f"Sucesso! A origem {origem} do IP {registro['ip']} esta aprovada."


def _servidor(estado):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, corpo, tipo="text/plain; charset=utf-8"):
            if isinstance(corpo, str):
                corpo = corpo.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):
            partes = urlsplit(self.path)
            qs = parse_qs(partes.query)
            if self.headers.get("X-Admin-Token") != TOKEN:
                self._send(403, "Token invalido.")
                return
            if self.headers.get("X-Forwarded-For"):
                self._send(403, "xff")
                return
            ip = (qs.get("ip") or [""])[0]
            estado.pedidos.append(
                {
                    "caminho": partes.path,
                    "ip": ip,
                    "host": self.headers.get("Host") or "",
                    "xff": self.headers.get("X-Forwarded-For") or "",
                }
            )
            if partes.path == "/n8n/fila":
                self._send(200, json.dumps({"visitantes": list(estado.visitantes.values())}), "application/json")
                return
            if partes.path == "/n8n/tocar":
                agora = "2026-10-01T21:00:00Z"
                registro = estado.visitantes.get(ip)
                if registro is None:
                    registro = {
                        "ip": ip,
                        "status": "pendente",
                        "tentativas": 1,
                        "ultima_vista": agora,
                        "pais": "",
                        "origens": [],
                    }
                    estado.visitantes[ip] = registro
                else:
                    registro["tentativas"] = int(registro.get("tentativas") or 1) + 1
                    registro["ultima_vista"] = agora
                self._send(200, json.dumps({"ok": True, "visitante": registro}), "application/json")
                return
            if partes.path == "/n8n/registrar-origem":
                origem = (qs.get("origem") or [""])[0]
                dispositivo = (qs.get("dispositivo") or [""])[0]
                registro = estado.visitantes.get(ip)
                if registro is None:
                    self._send(404, "Erro: O IP nao esta na fila de espera.")
                    return
                anotado = anotar_origem(registro, origem, dispositivo)
                registro["ultima_vista"] = "2026-10-01T21:00:01Z"
                registro["origem"] = origem
                registro["dispositivo"] = dispositivo
                self._send(
                    200,
                    json.dumps({"ok": True, "origem": origem, "status": anotado["item"]["status"]}),
                    "application/json",
                )
                return
            if partes.path == "/n8n/aprovar":
                status, texto = _aprovar(estado.visitantes.get(ip), (qs.get("origem") or [""])[0])
                self._send(status, texto)
                return
            if partes.path == "/n8n/vincular":
                origem = (qs.get("origem") or [""])[0]
                conta = (qs.get("conta") or [""])[0]
                registro = estado.visitantes.get(ip)
                item = next(
                    (
                        candidato
                        for candidato in (registro or {}).get("origens") or []
                        if candidato and candidato.get("origem") == origem
                    ),
                    None,
                )
                if not item or item.get("status") != "aprovado":
                    self._send(409, "O par IP e origem precisa estar aprovado antes do vinculo.")
                    return
                registro["conta_vinculada"] = conta
                registro["vinculo"] = "ativo"
                self._send(200, "vinculado")
                return
            self._send(404, "rota")

        def log_message(self, fmt, *args):
            return

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    return servidor, thread


def _fila(servidor):
    porta = servidor.server_address[1]
    return FilaHttp(f"http://127.0.0.1:{porta}/n8n/fila", TOKEN, timeout=2)


def _pedir_admin(servidor, caminho):
    import urllib.request

    porta = servidor.server_address[1]
    pedido = urllib.request.Request(
        f"http://127.0.0.1:{porta}{caminho}",
        headers={"X-Admin-Token": TOKEN, "Host": "127.0.0.1"},
        method="GET",
    )
    with urllib.request.urlopen(pedido, timeout=2) as resposta:
        return resposta.status, resposta.read().decode("utf-8")


def _politica(servidor, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    garantir(tmp_path)
    iniciar_usuario(caminho_geracao(tmp_path), "ana")
    privada = caminho_chave(tmp_path).read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    fila = _fila(servidor)
    return montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
    ), privada


def _visita(caminho, query=""):
    return {
        "caminho": caminho,
        "query": query,
        "headers": {"x-forwarded-for": IP},
    }


def test_painel_com_borda_8501_sobe_em_8502(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    monkeypatch.setenv("PAINEL_BORDA_PORT", "8501")
    monkeypatch.delenv("PAINEL_BORDA_HOST", raising=False)
    chave = tmp_path / "porteiro-hmac.key"
    chave.write_bytes(b"k" * 32)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(chave))
    garantir(tmp_path)
    privada = caminho_chave(tmp_path).read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()

    class _Fila:
        def consultar(self, ip, origem, conta, app):
            return {
                "status": "aprovado",
                "ip": ip,
                "origens": [{"origem": "orig-1", "status": "aprovado"}],
            }

        def tocar(self, ip):
            return None

    fila = _Fila()
    politica = montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
    )
    script = politica(
        {"caminho": "/origem.js", "query": "", "headers": {"x-forwarded-for": IP}},
        "127.0.0.1",
        {},
    )
    assert script["status"] == 200
    assert b"/painel/registrar-origem" in script["corpo"]
    cru = politica(_visita("/painel"), "127.0.0.1", {})
    assert cru["liga"] is False
    assert cru["status"] == 200
    assert b"Navegador novo neste IP" in cru["corpo"]
    com_cookie = _visita("/painel")
    com_cookie["headers"]["cookie"] = "n8groker_origem=orig-1"
    acao = politica(com_cookie, "127.0.0.1", {})
    assert acao["liga"] is True
    assert acao["port"] == 8502


def test_cada_painel_soma_tentativa_e_ultima_vista(tmp_path):
    estado = _Estado()
    estado.visitantes[OUTRO] = {
        "ip": OUTRO,
        "status": "pendente",
        "tentativas": 68,
        "origens": [],
        "pais": "",
    }
    servidor, thread = _servidor(estado)
    try:
        politica, _privada = _politica(servidor, tmp_path)
        pedido = {
            "caminho": "/painel",
            "query": "",
            "headers": {"x-forwarded-for": OUTRO},
        }
        primeira = politica(pedido, "127.0.0.1", {})
        assert primeira["status"] == 202
        assert estado.visitantes[OUTRO]["tentativas"] == 69
        assert estado.visitantes[OUTRO]["ultima_vista"]
        politica(pedido, "127.0.0.1", {})
        assert estado.visitantes[OUTRO]["tentativas"] == 70
        visto = estado.visitantes[OUTRO]["ultima_vista"]
        registro = politica(
            {
                "caminho": "/painel/registrar-origem",
                "query": "origem=orig-68&dispositivo=abcdef12",
                "headers": {"x-forwarded-for": OUTRO},
            },
            "127.0.0.1",
            {},
        )
        assert registro["status"] == 200
        assert estado.visitantes[OUTRO]["tentativas"] == 70
        assert estado.visitantes[OUTRO]["ultima_vista"] != visto
        assert estado.visitantes[OUTRO]["origens"][0]["origem"] == "orig-68"
        assert all(item["xff"] == "" for item in estado.pedidos)
        assert all(item["host"].startswith("127.0.0.1") for item in estado.pedidos)
    finally:
        servidor.shutdown()
        thread.join(timeout=2)


def test_fluxo_analise_registra_origem_aprova_e_abre_o_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    monkeypatch.delenv("PAINEL_BORDA_HOST", raising=False)
    chave = tmp_path / "porteiro-hmac.key"
    chave.write_bytes(b"k" * 32)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(chave))
    estado = _Estado()
    servidor, thread = _servidor(estado)
    n8n_antes = APPS.get("n8n")
    try:
        politica, privada = _politica(servidor, tmp_path)
        analise = politica(_visita("/painel"), "127.0.0.1", {})
        assert analise["status"] == 202
        assert b"Acesso em Analise" in analise["corpo"]
        assert b"/origem.js" in analise["corpo"]
        assert b"origem-aviso" in analise["corpo"]
        assert b"JavaScript esta desligado" in analise["corpo"]
        assert b"painel" not in analise["corpo"].lower()
        assert TOKEN.encode() not in analise["corpo"]
        csp = "\n".join(analise["headers"])
        assert "script-src 'self'" in csp
        assert "connect-src 'self'" in csp
        assert "script-src 'unsafe-inline'" not in csp
        assert estado.visitantes[IP]["tentativas"] == 1
        assert estado.visitantes[IP]["status"] == "pendente"
        assert estado.visitantes[IP]["origens"] == []

        script = politica(_visita("/origem.js"), "127.0.0.1", {})
        assert script["status"] == 200
        assert b"/painel/registrar-origem" in script["corpo"]
        assert estado.visitantes[IP]["tentativas"] == 1

        registro = politica(
            _visita(
                "/painel/registrar-origem",
                f"origem={ORIGEM}&dispositivo={DISPOSITIVO}&ip=198.51.100.9",
            ),
            "127.0.0.1",
            {},
        )
        assert registro["status"] == 200
        assert json.loads(registro["corpo"].decode("utf-8"))["ok"] is True
        assert estado.visitantes[IP]["tentativas"] == 1
        assert estado.visitantes[IP]["origens"][0]["status"] == "pendente"
        assert all(item["ip"] != "198.51.100.9" for item in estado.pedidos)
        assert "198.51.100.9" not in estado.visitantes

        import urllib.error

        try:
            _pedir_admin(servidor, f"/n8n/aprovar?ip={IP}")
            raise AssertionError("aprovar sem origem")
        except urllib.error.HTTPError as erro:
            assert erro.code == 400
            assert "Origem obrigatoria. Dispositivo nao aprovado." in erro.read().decode("utf-8")
        assert estado.visitantes[IP]["status"] == "pendente"

        status, texto = _pedir_admin(servidor, f"/n8n/aprovar?ip={IP}&origem={ORIGEM}")
        assert status == 200
        assert "aprovada" in texto
        assert estado.visitantes[IP]["status"] == "aprovado"

        cru = politica(_visita("/painel"), "127.0.0.1", {})
        assert cru["liga"] is False
        assert cru["status"] == 200
        assert b"Navegador novo neste IP" in cru["corpo"]
        assert b"/origem.js" in cru["corpo"]
        com_cookie = _visita("/painel")
        com_cookie["headers"]["cookie"] = f"n8groker_origem={ORIGEM}"
        login = politica(com_cookie, "127.0.0.1", {})
        assert login["liga"] is True
        assert login["host"] == "host.docker.internal"
        assert login["port"] == 8502

        status, _texto = _pedir_admin(servidor, f"/n8n/vincular?ip={IP}&conta=ana&origem={ORIGEM}")
        assert status == 200

        ec_priv, spki = gerar_chave()
        dispositivo = impressao(spki)
        token = emitir(
            privada,
            {
                "u": "ana",
                "ip": IP,
                "g": 1,
                "app": "",
                "abrir": ["n8n"],
                "origem": ORIGEM,
                "dispositivo": dispositivo,
                "nonce": "nonce-1",
            },
        )
        prova = cookies_de_prova(ec_priv, spki, ORIGEM, "nonce-1", "2026-10-01T12:00:00Z")
        escolha = politica(
            {
                "caminho": "/painel/escolher",
                "query": "app=n8n",
                "headers": {"x-forwarded-for": IP, "cookie": f"n8groker_sessao={token}; {prova}"},
            },
            "127.0.0.1",
            {},
        )
        assert escolha["status"] == 303
        novo = ler_cookie(escolha["headers"][1].split(":", 1)[1])

        async def abrir():
            conexoes = []

            async def upstream(reader, writer):
                conexoes.append(await reader.read(256))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await writer.drain()
                writer.close()

            bruto = await asyncio.start_server(upstream, "127.0.0.1", 0)
            porta = bruto.sockets[0].getsockname()[1]
            from scout.core.porta_apps import registrar_app

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
            reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
            writer.write(
                (
                    b"GET / HTTP/1.1\r\nHost: tunel\r\nX-Forwarded-For: "
                    + IP.encode()
                    + b"\r\nCookie: n8groker_sessao="
                    + novo.encode()
                    + b"; "
                    + prova.encode()
                    + b"\r\n\r\n"
                )
            )
            await writer.drain()
            corpo = await asyncio.wait_for(reader.read(256), timeout=2)
            frente.close()
            bruto.close()
            await frente.wait_closed()
            await bruto.wait_closed()
            return corpo, conexoes

        corpo, conexoes = asyncio.run(abrir())
        assert corpo.startswith(b"HTTP/1.1 200")
        assert conexoes and b"GET /" in conexoes[0]
        if n8n_antes:
            APPS["n8n"] = n8n_antes

        segunda = politica(
            _visita("/painel/registrar-origem", "origem=orig-dois&dispositivo=bbbbbbbbbbbbbbbb"),
            "127.0.0.1",
            {},
        )
        assert segunda["status"] == 200
        origens = estado.visitantes[IP]["origens"]
        assert origens[0]["status"] == "aprovado"
        assert origens[1]["origem"] == "orig-dois"
        assert origens[1]["status"] == "pendente"
        ec2, spki2 = gerar_chave()
        token2 = emitir(
            privada,
            {
                "u": "ana",
                "ip": IP,
                "g": 1,
                "app": "n8n",
                "abrir": ["n8n"],
                "origem": "orig-dois",
                "dispositivo": impressao(spki2),
                "nonce": "nonce-2",
            },
        )
        prova2 = cookies_de_prova(ec2, spki2, "orig-dois", "nonce-2", "2026-10-01T12:00:00Z")
        travado = politica(
            {
                "caminho": "/",
                "query": "",
                "headers": {"x-forwarded-for": IP, "cookie": f"n8groker_sessao={token2}; {prova2}"},
            },
            "127.0.0.1",
            {},
        )
        assert travado["liga"] is False
        assert travado["status"] == 202
        segue = politica(
            {
                "caminho": "/",
                "query": "",
                "headers": {"x-forwarded-for": IP, "cookie": f"n8groker_sessao={novo}; {prova}"},
            },
            "127.0.0.1",
            {},
        )
        assert segue["liga"] is True
        assert segue["host"] == "n8n_app"
    finally:
        if n8n_antes:
            APPS["n8n"] = n8n_antes
        servidor.shutdown()
        thread.join(timeout=2)


def test_alerta_trilha_bate_no_webhook_do_fluxo(tmp_path, monkeypatch):
    fluxo = json.loads(
        (ROOT / "Workflows_para_Autenticação" / "Alerta de trilha.json").read_text(encoding="utf-8")
    )
    texto = json.dumps(fluxo)
    assert "PORTEIRO_TOKEN" not in texto
    webhook = next(no for no in fluxo["nodes"] if no["type"] == "n8n-nodes-base.webhook")
    assert webhook["parameters"]["httpMethod"] == "GET"
    assert webhook["parameters"]["path"] == "alerta-trilha"
    email = next(no for no in fluxo["nodes"] if no["type"] == "n8n-nodes-base.emailSend")
    assert email["disabled"] is True
    campos = ("regra", "ip", "conta", "sid", "origem", "app", "detalhe", "hora")
    for campo in campos:
        assert campo in texto

    recebido = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            recebido["caminho"] = self.path
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, fmt, *args):
            return

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    try:
        porta = servidor.server_address[1]
        monkeypatch.delenv("SCOUT_BACKEND", raising=False)
        monkeypatch.setenv(
            "N8GROKER_ALERTA_WEBHOOK",
            f"http://127.0.0.1:{porta}/webhook/alerta-trilha",
        )
        grade = Trilha(tmp_path / "trilha")
        grade.anotar(
            ip=IP,
            sid="ab" * 8,
            conta="ana",
            origem=ORIGEM,
            app="n8n",
            passo="n8n",
            resultado="LIBERADO",
        )
    finally:
        servidor.shutdown()
        thread.join(timeout=2)
    assert recebido["caminho"].startswith("/webhook/alerta-trilha?")
    consulta = parse_qs(urlsplit(recebido["caminho"]).query)
    for campo in campos:
        assert consulta.get(campo)
    assert consulta["regra"] == ["pulou_etapas"]
    assert consulta["ip"] == [IP]
    assert consulta["conta"] == ["ana"]
