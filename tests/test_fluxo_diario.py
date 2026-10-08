"""O caminho que o Gabriel achou ao vivo, de uma vez.

IP novo, origem.js de verdade, aprovação, login por token, escolher,
cookie, n8n na raiz, trilha com sid e conta, segunda origem, revogação e o
JWT de admin depois da rotação da chave de usuário.
"""

import asyncio
import json
import os
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
from scout.core.porta_apps import APPS, FilaHttp, antes_do_porteiro, registrar_app  # noqa: E402
from scout.core.sessao_cookie import (  # noqa: E402
    caminho_chave,
    caminho_geracao,
    ler_cookie,
    ler_geracao,
    subir_geracao,
    verificar,
)
from scout.core.trilha import Trilha  # noqa: E402

NGROK = "172.18.0.7"
IP = "203.0.113.50"
CONTA = "ana"
ORIGEM = "orig-um"
ORIGEM_2 = "orig-dois"
TOKEN_FILA = "token-teste"
HORARIO = "2026-10-02T12:00:00Z"
NONCE = "nonce-fluxo-1"
JS = (SCOUT / "scout" / "static" / "origem.js").read_bytes()

ORIGINAIS = dict(APPS)


def setup_function():
    APPS.clear()
    APPS.update(ORIGINAIS)


def teardown_function():
    APPS.clear()
    APPS.update(ORIGINAIS)


def _dns(monkeypatch):
    def ips(nome):
        if nome == "ngrok_service":
            return {NGROK}
        return set()

    monkeypatch.setattr("scout.core.ip_vivo.ips_do_nome", ips)
    monkeypatch.setenv("N8GROKER_PROXY_NOME", "ngrok_service")
    monkeypatch.setenv("PORTEIRO_TRUSTED_PROXIES", "")
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")


class _Estado:
    def __init__(self):
        self.visitantes = {}


def _aprovar(registro, origem):
    if not origem:
        return 400, "Origem obrigatoria. Dispositivo nao aprovado."
    item = next(
        (candidato for candidato in registro.get("origens") or [] if candidato and candidato.get("origem") == origem),
        None,
    )
    if not item:
        return 404, "Origem nao registrada."
    item["status"] = "aprovado"
    registro["status"] = "aprovado"
    return 200, "ok"


def _vincular(registro, conta, origem):
    item = next(
        (candidato for candidato in registro.get("origens") or [] if candidato and candidato.get("origem") == origem),
        None,
    )
    if not item or item.get("status") != "aprovado" or not conta:
        return 400, "nao"
    registro["conta_vinculada"] = conta
    registro["vinculo"] = "ativo"
    return 200, "ok"


def _servidor(estado):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _send(self, status, corpo, tipo="text/plain; charset=utf-8"):
            if isinstance(corpo, str):
                corpo = corpo.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):
            if self.headers.get("X-Admin-Token") != TOKEN_FILA:
                self._send(403, "Token invalido.")
                return
            if self.headers.get("X-Forwarded-For"):
                self._send(403, "xff")
                return
            partes = urlsplit(self.path)
            qs = parse_qs(partes.query)
            ip = (qs.get("ip") or [""])[0]
            if partes.path == "/n8n/fila":
                self._send(200, json.dumps({"visitantes": list(estado.visitantes.values())}), "application/json")
                return
            if partes.path == "/n8n/tocar":
                registro = estado.visitantes.get(ip)
                if registro is None:
                    registro = {
                        "ip": ip,
                        "status": "pendente",
                        "tentativas": 1,
                        "ultima_vista": HORARIO,
                        "pais": "",
                        "origens": [],
                    }
                    estado.visitantes[ip] = registro
                else:
                    registro["tentativas"] = int(registro.get("tentativas") or 1) + 1
                self._send(200, json.dumps({"ok": True}), "application/json")
                return
            if partes.path == "/n8n/registrar-origem":
                registro = estado.visitantes.get(ip)
                if registro is None:
                    self._send(404, "Erro: O IP nao esta na fila de espera.")
                    return
                anotar_origem(registro, (qs.get("origem") or [""])[0], (qs.get("dispositivo") or [""])[0])
                self._send(200, json.dumps({"ok": True, "status": "pendente"}), "application/json")
                return
            registro = estado.visitantes.get(ip)
            if partes.path == "/n8n/aprovar":
                status, texto = _aprovar(registro, (qs.get("origem") or [""])[0])
                self._send(status, texto)
                return
            if partes.path == "/n8n/vincular":
                status, texto = _vincular(registro, (qs.get("conta") or [""])[0], (qs.get("origem") or [""])[0])
                self._send(status, texto)
                return
            self._send(404, "nao")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _pedido(caminho, query="", cookie="", xff=IP):
    headers = {"x-forwarded-for": xff}
    if cookie:
        headers["cookie"] = cookie
    return {"caminho": caminho, "query": query, "headers": headers}


def _eventos(grade: Trilha):
    return [item for item in grade._ler_arquivo(grade.arquivo)]


def test_gravacao_preserva_o_inode_quando_o_replace_falha(tmp_path, monkeypatch):
    from scout.core.sessao_cookie import garantir, iniciar_usuario

    garantir(tmp_path)
    iniciar_usuario(caminho_geracao(tmp_path), CONTA)
    path = caminho_geracao(tmp_path)
    inode = path.stat().st_ino

    def estourou(*_args, **_kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(os, "replace", estourou)
    subir_geracao(path, CONTA)
    assert path.stat().st_ino == inode
    assert ler_geracao(path, CONTA)["g"] == 2


def test_script_confirma_nodes_exclude_sem_login():
    texto = (ROOT / "iniciar_servicos.ps1").read_text(encoding="utf-8")
    assert "printenv NODES_EXCLUDE" in texto
    trecho = texto.split("Iniciando n8n", 1)[1].split("Aguardando estabilizacao", 1)[0]
    assert trecho.index("ConvertTo-NodesExcludeJson") < trecho.index("up")
    assert trecho.index("Show-NodesExclude") > trecho.index("up")
    assert 'Get-EnvValue "N8N_NODES_EXCLUDE"' in trecho


def test_fluxo_diario_da_borda(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from control_plane.admin_token import conferir, init_keys, issue
    from control_plane.auth import AuthError, create_user, entrar_com_token
    from control_plane.sessao_borda import ticket_de_login
    from control_plane.user_token import conferir as conferir_usuario
    from control_plane.user_token import emitir as emitir_usuario
    from control_plane.user_token import rotacionar

    _dns(monkeypatch)
    chave = b"k" * 32
    hmac_path = tmp_path / ".n8groker" / "porteiro-hmac.key"
    hmac_path.parent.mkdir(parents=True)
    hmac_path.write_bytes(chave)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(hmac_path))

    estado = _Estado()
    httpd = _servidor(estado)
    porta_fila = httpd.server_address[1]
    fila = FilaHttp(f"http://127.0.0.1:{porta_fila}/n8n/fila", TOKEN_FILA)
    grade = Trilha(tmp_path / "trilha")
    conexoes = []

    def _porta_n8n():
        loop = asyncio.new_event_loop()
        pronto = threading.Event()
        caixa = {}

        async def _run():
            async def handler(reader, writer):
                dados = await reader.read(4096)
                conexoes.append(dados)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await writer.drain()
                writer.close()

            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            caixa["porta"] = server.sockets[0].getsockname()[1]
            pronto.set()
            await asyncio.Event().wait()

        def _thread():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_run())

        threading.Thread(target=_thread, daemon=True).start()
        assert pronto.wait(3)
        return caixa["porta"]

    try:
        porta_n8n = _porta_n8n()
        registrar_app("n8n", "127.0.0.1", porta_n8n)

        create_user(tmp_path, CONTA, abrir=["n8n"])
        jwt_usuario = emitir_usuario(
            tmp_path, CONTA, ver=[], operar=[], abrir=["n8n"], por="admin", ip="127.0.0.1"
        )
        privada = caminho_chave(tmp_path).read_bytes()
        publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
        politica = montar_politica(
            antes_do_porteiro(fila),
            publica,
            privada,
            caminho_geracao(tmp_path),
            fila=fila,
            trilha=grade,
        )

        painel = politica(_pedido("/painel"), NGROK, {})
        assert painel["status"] == 202
        assert b"painel" not in painel["corpo"].lower()

        for caminho in ("/painel/origem.js", "/painel/painel/origem.js", "/origem.js"):
            script = politica(_pedido(caminho), NGROK, {})
            assert script["status"] == 200, caminho
            assert script["tipo"].startswith("text/javascript"), caminho
            assert script["corpo"] == JS
            assert b"Streamlit" not in script["corpo"]
            assert not script["corpo"].lstrip().startswith(b"<")

        html = politica(_pedido("/painel/origem.html"), NGROK, {})
        assert html["tipo"].startswith("text/html")
        assert b"<script src=\"/origem.js\"></script>" in html["corpo"]
        assert b"Streamlit" not in html["corpo"]

        ec_priv, spki = gerar_chave()
        dispositivo = impressao(spki)
        registro = politica(
            _pedido("/painel/registrar-origem", f"origem={ORIGEM}&dispositivo={dispositivo}"),
            NGROK,
            {},
        )
        assert registro["status"] == 200
        assert estado.visitantes[IP]["origens"][0]["status"] == "pendente"

        assert _aprovar(estado.visitantes[IP], ORIGEM)[0] == 200
        assert _vincular(estado.visitantes[IP], CONTA, ORIGEM)[0] == 200
        pasta_fila = tmp_path / "n8n" / "storage" / "Porteiro"
        pasta_fila.mkdir(parents=True)
        (pasta_fila / "controle_acesso.json").write_text(
            json.dumps(
                {
                    "visitantes": [
                        {
                            "ip": IP,
                            "status": "aprovado",
                            "conta_vinculada": CONTA,
                            "vinculo": "ativo",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        sessao = entrar_com_token(tmp_path, jwt_usuario, ip=IP, modo="edge")
        assert sessao["must_change"] is False
        assert sessao["aguardando"] is False
        assert sessao["abrir"] == ["n8n"]
        ticket = ticket_de_login(tmp_path, CONTA, ["n8n"], IP)
        prova = cookies_de_prova(ec_priv, spki, ORIGEM, NONCE, HORARIO)
        prova += "; n8groker_desafio=" + NONCE

        sem_nada = politica(_pedido("/painel/escolher", "app=n8n"), NGROK, {})
        assert sem_nada["status"] == 403
        assert b"painel" not in sem_nada["corpo"].lower()
        assert any(item.get("passo") == "/painel/escolher" and item.get("resultado") == "BLOQUEIO" for item in _eventos(grade))

        escolha = politica(
            _pedido("/painel/escolher", "app=n8n&t=" + ticket, prova),
            NGROK,
            {},
        )
        assert escolha["status"] == 303
        assert "Location: /" in escolha["headers"]
        cookie = ler_cookie(next(item.split(":", 1)[1] for item in escolha["headers"] if item.startswith("Set-Cookie:")))
        claims = verificar(cookie, publica, caminho_geracao(tmp_path), IP)
        assert claims["u"] == CONTA
        assert claims["app"] == "n8n"
        assert claims["sid"]

        raiz = politica(_pedido("/", cookie="n8groker_sessao=" + cookie + "; " + prova), NGROK, {})
        assert raiz["liga"] is True
        assert raiz["host"] == "127.0.0.1"
        assert raiz["port"] == porta_n8n

        liberados = [item for item in _eventos(grade) if item.get("resultado") == "LIBERADO"]
        assert liberados
        assert liberados[-1]["sid"] == claims["sid"]
        assert liberados[-1]["conta"] == CONTA
        assert any(item.get("passo") == "login" and item.get("sid") == claims["sid"] and item.get("conta") == CONTA for item in _eventos(grade))
        assert any(item.get("passo") == "escolher" and item.get("sid") == claims["sid"] for item in _eventos(grade))

        segundo = politica(
            _pedido("/painel/registrar-origem", f"origem={ORIGEM_2}&dispositivo={dispositivo}"),
            NGROK,
            {},
        )
        assert segundo["status"] == 200
        origens = {item["origem"]: item["status"] for item in estado.visitantes[IP]["origens"]}
        assert origens[ORIGEM] == "aprovado"
        assert origens[ORIGEM_2] == "pendente"

        prova_b = cookies_de_prova(ec_priv, spki, ORIGEM, NONCE, HORARIO) + "; n8groker_desafio=" + NONCE
        outra = politica(_pedido("/painel/escolher", "app=n8n&t=" + ticket, prova_b), NGROK, {})
        cookie_b = ler_cookie(next(item.split(":", 1)[1] for item in outra["headers"] if item.startswith("Set-Cookie:")))
        claims_b = verificar(cookie_b, publica, caminho_geracao(tmp_path), IP)
        assert claims_b["sid"] != claims["sid"]

        grade.revogar(claims["sid"])
        morta = politica(_pedido("/", cookie="n8groker_sessao=" + cookie + "; " + prova), NGROK, {})
        assert morta["status"] == 403
        viva = politica(_pedido("/", cookie="n8groker_sessao=" + cookie_b + "; " + prova_b), NGROK, {})
        assert viva["liga"] is True

        async def pelo_fio():
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

            async def falar(pedido: bytes) -> bytes:
                reader, writer = await asyncio.open_connection("127.0.0.1", pporta)
                writer.write(pedido)
                await writer.drain()
                buf = b""
                while True:
                    pedaco = await reader.read(65536)
                    if not pedaco:
                        break
                    buf += pedaco
                writer.close()
                return buf

            script = await falar(
                f"GET /painel/origem.js?v=1 HTTP/1.1\r\nHost: tunel\r\nX-Forwarded-For: {IP}\r\nConnection: close\r\n\r\n".encode()
            )
            app = await falar(
                (
                    "GET / HTTP/1.1\r\nHost: tunel\r\n"
                    f"X-Forwarded-For: {IP}\r\n"
                    f"Cookie: n8groker_sessao={cookie_b}; {prova_b}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
            )
            frente.close()
            await frente.wait_closed()
            return script, app

        script_http, app_http = asyncio.run(pelo_fio())
        assert b"Content-Type: text/javascript" in script_http
        assert b"Unexpected identifier" not in script_http
        assert b"Streamlit" not in script_http
        assert script_http.split(b"\r\n\r\n", 1)[1].startswith(b"/*")
        assert app_http.startswith(b"HTTP/1.1 200")
        assert conexoes and b"GET /" in conexoes[-1]

        init_keys(tmp_path)
        admin = issue(tmp_path)
        assert conferir(tmp_path, admin)["sub"] == "admin"
        rotacionar(tmp_path, por="admin", ip="127.0.0.1")
        assert conferir(tmp_path, admin)["sub"] == "admin"
        assert verificar(cookie_b, publica, caminho_geracao(tmp_path), IP) is None
        try:
            conferir_usuario(tmp_path, jwt_usuario, ip=IP)
        except AuthError as exc:
            assert exc.motivo == "rotacionado"
        else:
            raise AssertionError("o token antigo ainda valeu depois da rotação")
    finally:
        httpd.shutdown()
