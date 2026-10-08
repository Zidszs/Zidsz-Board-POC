"""O túnel HTTPS reaproveita a conexão. O iframe tem de gravar n8groker_sessao.

Sobe o que o CI consegue: terminação TLS local, o proxy do Scout com a política
real e o Streamlit de borda (baseUrlPath painel). Não sobe Docker, o container
scout-backend, o ngrok, o processo do Porteiro nem o n8n de verdade. O app n8n
é um HTTP mínimo na porta que o Scout registraria. O Chromium não deixa o teste
gravar X-Forwarded-For, então a visita usa o IP de documentação no lugar do
hop que o ngrok acrescentaria.
"""

import ipaddress
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IP = "203.0.113.10"
_ARGS = ["--no-sandbox", "--ignore-certificate-errors", "--disable-dev-shm-usage"]


def _porta_livre() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _esperar(porta: int, timeout: float = 30) -> bool:
    fim = time.time() + timeout
    while time.time() < fim:
        try:
            with socket.create_connection(("127.0.0.1", porta), 0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _certificado(pasta: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    agora = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=1))
        .not_valid_after(agora + timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]),
            critical=False,
        )
        .sign(chave, hashes.SHA256())
    )
    cert_path = pasta / "cert.pem"
    key_path = pasta / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        chave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _redigir(texto: str) -> str:
    import re

    return re.sub(r"[A-Za-z0-9_=-]{16,}", "[redigido]", texto or "")


class _Fila:
    def __init__(self):
        self.origens = []

    def consultar(self, ip, origem, conta, app):
        itens = []
        for item in self.origens:
            copia = dict(item)
            copia["status"] = "aprovado"
            itens.append(copia)
        return {
            "status": "aprovado",
            "ip": ip,
            "conta_vinculada": "ana",
            "vinculo": "ativo",
            "origem": origem,
            "origens": itens,
        }

    def registrar(self, ip, origem, dispositivo):
        for item in self.origens:
            if item.get("origem") == origem:
                return {"ok": True, "status": "aprovado"}
        self.origens.append({"origem": origem, "status": "aprovado", "dispositivo": dispositivo})
        return {"ok": True, "status": "aprovado"}

    def tocar(self, ip):
        return {"ok": True, "ip": ip}


def _preparar(base: Path) -> str:
    import json

    from control_plane.auth import create_user
    from control_plane.user_token import emitir, garantir

    (base / ".n8groker").mkdir(parents=True)
    (base / "n8n" / "storage" / "Porteiro").mkdir(parents=True)
    (base / ".n8groker" / "porteiro-hmac.key").write_bytes(b"k" * 32)
    garantir(base)
    create_user(base, "ana", ver=["n8n"], operar=[], abrir=["n8n"])
    token = emitir(base, "ana", ver=["n8n"], operar=[], abrir=["n8n"], por="admin", ip="127.0.0.1")
    (base / "n8n" / "storage" / "Porteiro" / "controle_acesso.json").write_text(
        json.dumps(
            {
                "visitantes": [
                    {
                        "ip": IP,
                        "status": "aprovado",
                        "conta_vinculada": "ana",
                        "vinculo": "ativo",
                        "origens": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return token


def _subir_n8n(porta: int):
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            corpo = b"n8n-ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *_args):
            return

    httpd = http.server.HTTPServer(("127.0.0.1", porta), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _subir_tls(porta_tls: int, porta_mitm: int, cert: Path, key: Path):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    bruto = socket.socket()
    bruto.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bruto.bind(("127.0.0.1", porta_tls))
    bruto.listen(64)

    def ponte(seguro):
        try:
            upstream = socket.create_connection(("127.0.0.1", porta_mitm))
        except OSError:
            seguro.close()
            return

        def copiar(origem, destino):
            try:
                while True:
                    data = origem.recv(65536)
                    if not data:
                        break
                    destino.sendall(data)
            except OSError:
                pass
            try:
                destino.shutdown(socket.SHUT_WR)
            except OSError:
                pass

        ida = threading.Thread(target=copiar, args=(seguro, upstream), daemon=True)
        volta = threading.Thread(target=copiar, args=(upstream, seguro), daemon=True)
        ida.start()
        volta.start()
        ida.join()
        volta.join()
        seguro.close()
        upstream.close()

    def aceitar():
        while True:
            try:
                cliente, _ = bruto.accept()
            except OSError:
                return
            try:
                seguro = ctx.wrap_socket(cliente, server_side=True)
            except ssl.SSLError:
                cliente.close()
                continue
            threading.Thread(target=ponte, args=(seguro,), daemon=True).start()

    threading.Thread(target=aceitar, daemon=True).start()
    return bruto


def _lancar(playwright):
    erros = []
    tentativas = [{"headless": True, "args": _ARGS}]
    if os.path.isfile("/usr/bin/google-chrome"):
        tentativas.append({"executable_path": "/usr/bin/google-chrome", "headless": True, "args": _ARGS})
    for kwargs in tentativas:
        try:
            return playwright.chromium.launch(**kwargs)
        except Exception as exc:
            erros.append(str(exc))
    mensagem = "navegador nao subiu: " + " | ".join(erros)
    if os.environ.get("CI"):
        pytest.fail(mensagem)
    pytest.skip(mensagem)


def test_navegador_grava_o_cookie_e_abrir_lista_a_sessao(tmp_path, monkeypatch):
    if os.environ.get("CI"):
        from playwright.sync_api import sync_playwright
    else:
        sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    import asyncio

    import scout.core.politica_portal as pol
    from scout.core.mitm_proxy import MitmProxy
    from scout.core.porta_apps import APPS, antes_do_porteiro, registrar_app
    from scout.core.trilha import Trilha

    base = tmp_path / "raiz"
    token = _preparar(base)
    porta_borda = _porta_livre()
    porta_n8n = _porta_livre()
    porta_mitm = _porta_livre()
    porta_tls = _porta_livre()
    cert, key = _certificado(tmp_path)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(base / ".n8groker" / "porteiro-hmac.key"))
    monkeypatch.setenv("SESSAO_KEY_ARQUIVO", str(base / ".n8groker" / "sessao.key"))
    monkeypatch.setenv("SESSAO_GERACAO_ARQUIVO", str(base / ".n8groker" / "sessoes-geracao.json"))
    monkeypatch.setenv("PAINEL_BORDA_HOST", "127.0.0.1")
    monkeypatch.setenv("PAINEL_BORDA_PORT", str(porta_borda))
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", str(porta_mitm))
    monkeypatch.setenv("N8GROKER_TRILHA_DIR", str(base / ".n8groker" / "trilha"))

    _subir_n8n(porta_n8n)
    log = open(tmp_path / "streamlit.log", "w", encoding="utf-8")
    env = os.environ.copy()
    env["PANEL_MODE"] = "edge"
    env["N8GROKER_ROOT"] = str(base)
    env["STREAMLIT_SERVER_HEADLESS"] = "true"
    painel = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "control_plane" / "app.py"),
            "--server.headless",
            "true",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(porta_borda),
            "--server.baseUrlPath",
            "painel",
            "--server.enableCORS",
            "false",
            "--server.enableXsrfProtection",
            "false",
            "--browser.gatherUsageStats",
            "false",
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    apps_antes = dict(APPS)
    bruto = None
    try:
        assert _esperar(porta_borda, 40), _redigir((tmp_path / "streamlit.log").read_text(encoding="utf-8")[-1500:])
        def decidir_fixo(socket_ip, xff, nome_proxy="", proxies=None):
            return {"acao": "visitante", "ip": IP, "via": "teste", "socket": socket_ip or "127.0.0.1"}

        monkeypatch.setattr(pol, "decidir_visita", decidir_fixo)
        fila = _Fila()
        grade = Trilha(base / ".n8groker" / "trilha")
        registrar_app("n8n", "127.0.0.1", porta_n8n)
        proxy = MitmProxy()
        proxy.antes_de_ligar = pol.montar_politica(
            antes_do_porteiro(fila),
            fila=fila,
            trilha=grade,
        )
        entry = {
            "id": "porteiro-manual",
            "listen_port": porta_mitm,
            "upstream_host": "127.0.0.1",
            "upstream_port": 9,
            "name": "portal",
            "mode": "http",
        }

        def servir():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def main():
                async def handler(reader, writer):
                    await proxy._handle_client(reader, writer, entry)

                server = await asyncio.start_server(handler, "127.0.0.1", porta_mitm)
                await server.serve_forever()

            loop.run_until_complete(main())

        threading.Thread(target=servir, daemon=True).start()
        assert _esperar(porta_mitm, 5)
        bruto = _subir_tls(porta_tls, porta_mitm, cert, key)
        time.sleep(0.2)

        with sync_playwright() as playwright:
            browser = _lancar(playwright)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            ruims = []
            page.on(
                "response",
                lambda resp: ruims.append(f"{resp.status} {resp.url.split(str(porta_tls))[-1][:140]}")
                if resp.status >= 400
                else None,
            )
            page.on(
                "console",
                lambda msg: ruims.append(f"console {msg.type} {_redigir(msg.text)[:180]}")
                if msg.type in {"error", "warning"}
                else None,
            )
            page.goto(f"https://127.0.0.1:{porta_tls}/painel/", wait_until="domcontentloaded", timeout=30000)
            page.get_by_role("button", name="Entrar").wait_for(timeout=20000)
            page.get_by_label("Token").fill(token)
            page.get_by_role("button", name="Entrar").click()
            prazo = time.time() + 15
            nomes = []
            while time.time() < prazo:
                nomes = [item["name"] for item in context.cookies()]
                if "n8groker_sessao" in nomes:
                    break
                page.wait_for_timeout(200)
            assert "n8groker_sessao" in nomes, "cookies=" + ",".join(nomes) + " rede=" + ",".join(ruims[-6:])
            abrir = page.locator('a[href*="/painel/escolher"]').filter(has_text="Abrir")
            try:
                abrir.first.wait_for(timeout=20000)
            except Exception as exc:
                texto = _redigir(page.inner_text("body")[:300].replace("\n", " | "))
                raise AssertionError(
                    f"sem Abrir. cookies={','.join(nomes)} rede={ruims[-12:]} tela={texto}"
                ) from exc
            abrir.first.click()
            prazo = time.time() + 15
            sessoes = []
            while time.time() < prazo:
                sessoes = grade.sessoes()
                if sessoes and any("LIBERADO" in linha for linha in grade.legiveis(ip=IP)):
                    break
                page.wait_for_timeout(200)
            assert sessoes, "nenhuma sessao listada"
            assert any("LIBERADO" in linha for linha in grade.legiveis(ip=IP))
            browser.close()
    finally:
        painel.terminate()
        try:
            painel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            painel.kill()
        log.close()
        if bruto is not None:
            bruto.close()
        APPS.clear()
        APPS.update(apps_antes)
