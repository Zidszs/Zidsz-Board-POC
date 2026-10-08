"""Cookie em cada pedido do portal, e a fila continua obrigatória.

Fora de `/painel`, o upstream é o app gravado na sessão. A consulta ao
Porteiro acontece depois da lista `abrir` e antes de abrir o socket.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import urllib.parse
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scout.core.hmac_arquivo import MOTIVO_AUSENTE, ler_chave_hmac
from scout.core.ip_vivo import decidir_visita
from scout.core.origem import conferir, impressao, spki_do_texto
from scout.core.porta_apps import exige_porteiro
from scout.core.ticket_sessao import ler_ticket
from scout.core.trilha import sid_de
from scout.core.sessao_cookie import (
    cabecalho_cookie,
    emitir,
    ler_cookie,
    ler_geracao,
    renovar,
    verificar,
)


def _seco():
    return {
        "liga": False,
        "status": 403,
        "corpo": b"Acesso negado.",
        "tipo": "text/plain; charset=utf-8",
        "consultou": False,
    }


def _chaves_do_ambiente():
    caminho = os.environ.get("SESSAO_KEY_ARQUIVO", "/run/sessao.key")
    geracoes = Path(os.environ.get("SESSAO_GERACAO_ARQUIVO", "/run/sessoes-geracao.json"))
    try:
        privada = Path(caminho).read_bytes()
        publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    except Exception:
        return None, None, geracoes
    return publica, privada, geracoes


def _param(query: str, nome: str) -> str:
    for parte in str(query or "").split("&"):
        if parte.startswith(nome + "="):
            return urllib.parse.unquote(parte.split("=", 1)[1])
    return ""


def _caminho_de(pedido) -> str:
    bruto = urllib.parse.unquote(str((pedido or {}).get("caminho") or "/"))
    if not bruto.startswith("/"):
        bruto = "/" + bruto
    while "//" in bruto:
        bruto = bruto.replace("//", "/")
    if len(bruto) > 1 and bruto.endswith("/"):
        bruto = bruto[:-1]
    return bruto or "/"


def _tipo_origem(caminho: str) -> str:
    nome = caminho.rsplit("/", 1)[-1].lower()
    if nome == "origem.js":
        return "js"
    if nome == "origem.html":
        return "html"
    return ""


def _cabecalho_desafio(nonce: str) -> str:
    return "n8groker_desafio=" + nonce + "; Secure; SameSite=Lax; Path=/; Max-Age=900"


def _com_desafio(acao: dict, headers: dict) -> dict:
    """O navegador assina este nonce. Sem ele o escolher não tem prova."""
    if not isinstance(acao, dict):
        return acao
    if ler_cookie((headers or {}).get("cookie", ""), "n8groker_desafio"):
        return acao
    linha = _cabecalho_desafio(secrets.token_hex(16))
    saida = dict(acao)
    if saida.get("liga"):
        cookies = [item for item in (saida.get("cookies") or []) if isinstance(item, str)]
        cookies.append(linha)
        saida["cookies"] = cookies
        return saida
    extras = [item for item in (saida.get("headers") or []) if isinstance(item, str)]
    extras.append("Set-Cookie: " + linha)
    saida["headers"] = extras
    return saida


def _painel(caminho: str) -> bool:
    return caminho == "/painel" or caminho.startswith("/painel/")


def _id_ok(valor: str) -> bool:
    if not valor or len(valor) > 128:
        return False
    return all(c.isalnum() or c in "-_" for c in valor)


def _texto(status: int, corpo: str) -> dict:
    return {
        "liga": False,
        "status": status,
        "corpo": corpo.encode("utf-8"),
        "tipo": "text/plain; charset=utf-8",
        "consultou": True,
    }


_CSP_ORIGEM_HTML = (
    "default-src 'none'; script-src 'self'; connect-src 'self'; "
    "style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'self'"
)


def _html_origem() -> dict:
    pagina = (
        "<!DOCTYPE html><html><head>"
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP_ORIGEM_HTML}">'
        "</head><body>"
        '<p id="origem-aviso"></p>'
        '<script src="/origem.js"></script>'
        "</body></html>"
    )
    return {
        "liga": False,
        "status": 200,
        "corpo": pagina.encode("utf-8"),
        "tipo": "text/html; charset=utf-8",
        "consultou": False,
        "headers": [
            "Content-Security-Policy: " + _CSP_ORIGEM_HTML,
            "X-Content-Type-Options: nosniff",
            "Cache-Control: no-store",
            "Referrer-Policy: no-referrer",
        ],
    }


def _e_escolher(caminho: str) -> bool:
    """O baseUrlPath do Streamlit pode dobrar o prefixo: /painel/painel/escolher."""
    return caminho == "/painel/escolher" or (
        caminho.startswith("/painel/") and caminho.endswith("/escolher")
    )


def _cabecalho(headers, nome: str) -> str:
    if not isinstance(headers, dict):
        return ""
    alvo = nome.lower()
    for chave, valor in headers.items():
        if str(chave).lower() == alvo:
            return str(valor or "")
    return ""


def _documento_do_painel(caminho: str, headers) -> bool:
    """Página HTML do Streamlit. Health, estático e origem.* continuam no proxy.

    /painel e /painel/painel são o documento, mesmo quando o curl não manda
    Accept. Outro caminho só entra com text/html ou navegação.
    """
    if caminho != "/painel" and not caminho.startswith("/painel/"):
        return False
    if _e_escolher(caminho) or caminho in ("/painel/registrar-origem", "/painel/sessao"):
        return False
    pedacos = [parte for parte in caminho.split("/") if parte]
    if any(parte in ("_stcore", "static") for parte in pedacos):
        return False
    nome = pedacos[-1].lower() if pedacos else ""
    if nome.startswith("origem."):
        return False
    if pedacos and all(parte == "painel" for parte in pedacos):
        return True
    if "text/html" in _cabecalho(headers, "accept").lower():
        return True
    if _cabecalho(headers, "sec-fetch-dest").lower() == "document":
        return True
    return _cabecalho(headers, "sec-fetch-mode").lower() == "navigate"


def _origem_registrada(fila, ip: str, headers) -> bool:
    """O cookie só vale se esta origem já está na fila deste IP."""
    origem = ler_cookie((headers or {}).get("cookie", ""), "n8groker_origem")
    if not origem or fila is None:
        return False
    consultar = getattr(fila, "consultar", None)
    if not callable(consultar):
        return False
    try:
        registro = consultar(ip, origem, "", "painel")
    except Exception:
        return False
    if not isinstance(registro, dict):
        return False
    origens = registro.get("origens")
    if not isinstance(origens, list):
        return False
    for item in origens:
        if (
            isinstance(item, dict)
            and item.get("origem") == origem
            and item.get("status") in ("pendente", "aprovado")
        ):
            return True
    return False


def _pagina_registrar_navegador() -> dict:
    """Documento de topo, no túnel. O iframe do Streamlit não registra a origem.

    IP já aprovado sem cookie de origem ia direto ao painel. O origem.js
    no iframe sanduichado não grava o cookie que o /painel/sessao lê, e a
    origem nova não vira pendente. Esta página roda o script no documento
    principal, em HTTPS, e só então recarrega o painel.
    """
    pagina = (
        "<!DOCTYPE html><html><head>"
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP_ORIGEM_HTML}">'
        "</head><body>"
        "<h1>Navegador novo neste IP</h1>"
        "<p>Navegador novo neste IP. Aguarde a aprovação deste navegador.</p>"
        "<noscript><p>Ative o JavaScript para continuar.</p></noscript>"
        '<p id="origem-aviso" data-recarregar="agora">Registrando este navegador.</p>'
        '<script src="/origem.js"></script>'
        "</body></html>"
    )
    return {
        "liga": False,
        "status": 200,
        "corpo": pagina.encode("utf-8"),
        "tipo": "text/html; charset=utf-8",
        "consultou": True,
        "headers": [
            "Content-Security-Policy: " + _CSP_ORIGEM_HTML,
            "X-Content-Type-Options: nosniff",
            "Cache-Control: no-store",
            "Referrer-Policy: no-referrer",
        ],
    }


def _js_origem() -> dict:
    arquivo = Path(__file__).resolve().parents[1] / "static" / "origem.js"
    try:
        corpo = arquivo.read_bytes()
    except OSError:
        return _seco()
    return {
        "liga": False,
        "status": 200,
        "corpo": corpo,
        "tipo": "text/javascript; charset=utf-8",
        "consultou": False,
        "headers": [
            "X-Content-Type-Options: nosniff",
            "Cache-Control: no-store",
            "Content-Security-Policy: default-src 'none'",
        ],
    }


def _registrar_origem(fila, ip: str, pedido) -> dict:
    query = (pedido or {}).get("query") or ""
    origem = _param(query, "origem")
    dispositivo = _param(query, "dispositivo")
    if not _id_ok(origem) or not _id_ok(dispositivo):
        return _texto(400, "Origem invalida.")
    fn = getattr(fila, "registrar", None)
    if not callable(fn):
        return _texto(503, "Fila indisponivel.")
    try:
        dados = fn(ip, origem, dispositivo)
    except Exception:
        dados = None
    if not isinstance(dados, dict) or not dados.get("ok"):
        return _texto(503, "Origem nao registrada.")
    corpo = json.dumps(
        {"ok": True, "origem": origem, "status": dados.get("status") or "pendente"},
        ensure_ascii=True,
    ).encode("utf-8")
    return {
        "liga": False,
        "status": 200,
        "corpo": corpo,
        "tipo": "application/json; charset=utf-8",
        "consultou": True,
        "headers": ["Cache-Control: no-store", "X-Content-Type-Options: nosniff"],
    }


def montar_politica(fila_antes, publica=None, privada=None, geracoes=None, fila=None, varredura=None, trilha=None):
    if publica is None or privada is None or geracoes is None:
        publica, privada, geracoes = _chaves_do_ambiente()

    estado = {"ip": "", "headers": {}}

    def _negar(alvo):
        if varredura is not None and estado["ip"]:
            varredura.observar(estado["ip"], alvo, negado=True)
        return _seco()

    def _sid_do_cookie():
        """O ticket pode falhar e o cookie da visita ainda ter o sid da trilha."""
        if publica is None or geracoes is None or not estado["ip"]:
            return ""
        token = ler_cookie((estado.get("headers") or {}).get("cookie", ""))
        if not token:
            return ""
        corpo = verificar(token, publica, geracoes, estado["ip"])
        if not isinstance(corpo, dict):
            return ""
        return sid_de(corpo)

    def _marcar(passo, resultado, motivo, claims=None, app=""):
        if trilha is None or not estado["ip"]:
            return
        conta = ""
        identificador = ""
        origem = ""
        if isinstance(claims, dict):
            conta = str(claims.get("u") or "")
            identificador = sid_de(claims)
            origem = str(claims.get("origem") or "")
            if not app:
                app = str(claims.get("app") or "")
        if not identificador:
            identificador = _sid_do_cookie()
        try:
            trilha.anotar(
                ip=estado["ip"],
                sid=identificador,
                conta=conta,
                origem=origem,
                app=app,
                passo=passo,
                resultado=resultado,
                motivo=motivo,
            )
            if identificador and resultado == "LIBERADO":
                trilha.ver_sessao(
                    sid=identificador,
                    conta=conta,
                    ip=estado["ip"],
                    origem=origem,
                    app=app,
                )
            trilha.gravar_auth_sessao(
                ip=estado["ip"],
                conta=conta,
                motivo_caminho=str(motivo or ""),
                sid=identificador,
            )
        except Exception:
            return

    def antes(pedido, client_ip, entry):
        headers = (pedido or {}).get("headers") or {}
        estado["headers"] = headers
        caminho = _caminho_de(pedido)
        visto = decidir_visita(
            client_ip,
            headers.get("x-forwarded-for", ""),
            os.environ.get("N8GROKER_PROXY_NOME", ""),
            os.environ.get("PORTEIRO_TRUSTED_PROXIES", ""),
        )
        if visto["acao"] == "negar" or not visto.get("ip"):
            estado["ip"] = visto.get("socket") or str(client_ip or "") or "sem-ip"
            _marcar(visto.get("via") or "negar", "BLOQUEIO", visto.get("motivo") or "negado")
            estado["ip"] = ""
            return _seco()
        ip = visto["ip"]
        estado["ip"] = ip

        if varredura is not None and varredura.bloqueado(ip):
            _marcar("varredura", "BLOQUEIO", "varredura")
            return _seco()
        # Qualquer prefixo (baseUrlPath do Streamlit dobra /painel) ainda é o
        # arquivo do Scout. Se isto cair no proxy, o navegador recebe o HTML
        # do Streamlit e o script quebra com Unexpected identifier 'Streamlit'.
        tipo = _tipo_origem(caminho)
        if tipo == "js":
            return _com_desafio(_js_origem(), headers)
        if tipo == "html":
            return _com_desafio(_html_origem(), headers)
        if caminho == "/painel/registrar-origem":
            return _registrar_origem(fila, ip, pedido)
        if caminho == "/painel/sessao":
            return _abrir_sessao(headers, pedido, ip, privada, geracoes)
        painel = _painel(caminho)
        claims = None
        if publica is not None and geracoes is not None:
            token = ler_cookie(headers.get("cookie", ""))
            if token:
                claims = verificar(token, publica, geracoes, ip)
        if isinstance(claims, dict) and trilha is not None and trilha.revogada(sid_de(claims)):
            _marcar("sessao", "BLOQUEIO", "sessao revogada", claims)
            claims = None
            if not painel:
                return _negar(caminho)
        if claims is None and not painel:
            _marcar(f"{caminho} direto", "BLOQUEIO", "rota sem painel")
            return _negar(caminho)
        if _e_escolher(caminho):
            if not isinstance(claims, dict):
                claims, motivo = _do_ticket(headers, pedido, ip, geracoes)
                if motivo or not isinstance(claims, dict) or not claims.get("origem"):
                    _marcar(
                        "/painel/escolher",
                        "BLOQUEIO",
                        motivo or "sem sessao",
                        claims if isinstance(claims, dict) else None,
                    )
                    return _negar(caminho)
            falha = _motivo_prova(headers, claims)
            if falha:
                _marcar("/painel/escolher", "BLOQUEIO", falha, claims)
                return _negar(caminho)
            return _escolher(claims, pedido, ip, privada)
        if not painel:
            if not _prova(headers, claims):
                return _negar(caminho)
            return _app(claims, ip, privada, geracoes)
        if caminho in ("/painel", "/painel/"):
            tocar = getattr(fila, "tocar", None)
            if callable(tocar):
                try:
                    tocar(ip)
                except Exception:
                    pass
        acao = fila_antes(pedido, ip, entry)
        if not acao or not acao.get("liga"):
            codigo = int((acao or {}).get("status") or 403)
            _marcar("/painel", "BLOQUEIO" if codigo == 403 else "AGUARDANDO", "painel", claims)
            if codigo == 202:
                return _com_desafio(acao, headers)
            return acao
        cabecalho = _hmac_pedido(ip)
        if not cabecalho:
            _marcar("/painel", "BLOQUEIO", MOTIVO_AUSENTE, claims)
            return _texto(
                503,
                "O cabecalho do Porteiro nao foi assinado. A chave HMAC nao esta legivel.",
            )
        if _documento_do_painel(caminho, headers) and not _origem_registrada(fila, ip, headers):
            return _com_desafio(_pagina_registrar_navegador(), headers)
        _marcar("/painel", "ok", "painel", claims)
        if isinstance(claims, dict):
            _marcar("login ok", "ok", "login", claims)
        saida = dict(acao)
        from scout.core.route_config import porta_sem_hud

        host_painel = os.environ.get("PAINEL_BORDA_HOST", "").strip() or "host.docker.internal"
        saida["host"] = host_painel
        saida["port"] = porta_sem_hud(os.environ.get("PAINEL_BORDA_PORT", "8502"), 8502)
        saida["injetar_pedido"] = cabecalho
        if claims is not None and privada is not None:
            _deslizar(saida, claims, privada, geracoes)
        if caminho in ("/painel", "/painel/"):
            return _com_desafio(saida, headers)
        return saida

    def _escolher(claims, pedido, ip, privada_local):
        if claims is None or privada_local is None:
            return _seco()
        claims = dict(claims)
        primeiro = not sid_de(claims)
        if primeiro:
            claims["sid"] = secrets.token_hex(8)
        app = _param((pedido or {}).get("query") or "", "app")
        abrir = claims.get("abrir") or []
        if not app or app not in abrir:
            _marcar("/" + (app or "escolher"), "BLOQUEIO", "fora de abrir", claims, app)
            return _negar("/" + (app or "escolher"))
        consultar = getattr(fila, "consultar", None)
        if consultar is None:
            _marcar("/" + (app or "escolher"), "BLOQUEIO", "sem porteiro", claims, app)
            return _negar("/" + (app or "escolher"))
        acao = exige_porteiro(app, ip, claims["u"], str(claims.get("origem") or ""), consultar)
        if not acao.get("liga"):
            codigo = int(acao.get("status") or 403)
            _marcar("Porteiro", "BLOQUEIO" if codigo == 403 else "AGUARDANDO", "porteiro", claims, app)
            return acao
        _marcar("Porteiro ok", "ok", "porteiro", claims, app)
        if primeiro:
            _marcar("/painel", "ok", "painel", claims)
            _marcar("login", "ok", "login", claims, app)
        novos = dict(claims)
        novos["app"] = app
        token = emitir(privada_local, novos)
        _marcar("escolher", "ok", "escolher", novos, app)
        return {
            "liga": False,
            "status": 303,
            "corpo": b"",
            "tipo": "text/plain; charset=utf-8",
            "headers": ["Location: /", "Set-Cookie: " + cabecalho_cookie(token)],
            "consultou": True,
        }

    def _app(claims, ip, privada_local, arquivo):
        if claims is None:
            return _seco()
        app = str(claims.get("app") or "")
        abrir = claims.get("abrir") or []
        if not app or app not in abrir:
            _marcar("/" + (app or "raiz"), "BLOQUEIO", "fora de abrir", claims, app)
            return _negar("/" + (app or "raiz"))
        consultar = getattr(fila, "consultar", None)
        if consultar is None:
            _marcar("/" + (app or "raiz"), "BLOQUEIO", "sem porteiro", claims, app)
            return _negar("/" + (app or "raiz"))
        acao = exige_porteiro(app, ip, claims["u"], str(claims.get("origem") or ""), consultar)
        if not acao.get("liga"):
            codigo = int(acao.get("status") or 403)
            _marcar("Porteiro", "BLOQUEIO" if codigo == 403 else "AGUARDANDO", "porteiro", claims, app)
            return acao
        _marcar("Porteiro ok", "ok", "porteiro", claims, app)
        _marcar(app, "LIBERADO", "liberado", claims, app)
        if privada_local is not None and arquivo is not None:
            _deslizar(acao, claims, privada_local, arquivo)
        return acao

    def _do_ticket(headers, pedido, ip_visita, arquivo):
        chave = ler_chave_hmac()
        token = _param((pedido or {}).get("query") or "", "t")
        if not chave or not token or arquivo is None:
            return None, "sem sessao"
        corpo = ler_ticket(chave, token, ip=ip_visita)
        if not isinstance(corpo, dict):
            return None, "sem sessao"
        item = ler_geracao(arquivo, corpo["u"])
        if item is None or not item["ativo"] or item["g"] != corpo["g"]:
            return {"u": corpo["u"]}, "sessao da conta revogada"
        prova = _prova_cookies(headers, "")
        if not isinstance(prova, dict):
            return {"u": corpo["u"]}, "origem nao confere"
        return {
            "u": corpo["u"],
            "ip": ip_visita,
            "g": corpo["g"],
            "app": "",
            "abrir": list(corpo.get("abrir") or []),
            "origem": prova["origem"],
            "dispositivo": prova["dispositivo"],
            "nonce": prova["nonce"],
            "sid": "",
        }, ""

    def _cookie_revogado(headers, ip_visita, arquivo):
        if trilha is None or publica is None or arquivo is None:
            return None
        token = ler_cookie(headers.get("cookie", ""))
        if not token:
            return None
        velho = verificar(token, publica, arquivo, ip_visita)
        if not isinstance(velho, dict) or not trilha.revogada(sid_de(velho)):
            return None
        return velho

    def _abrir_sessao(headers, pedido, ip_visita, privada_local, arquivo):
        claims, motivo = _do_ticket(headers, pedido, ip_visita, arquivo)
        if not isinstance(claims, dict) or motivo or not claims.get("origem"):
            _marcar("/painel/sessao", "BLOQUEIO", motivo or "sem sessao", claims if isinstance(claims, dict) else None)
            return _negar("/painel/sessao")
        if _motivo_prova(headers, claims):
            _marcar("/painel/sessao", "BLOQUEIO", "origem nao confere", claims)
            return _negar("/painel/sessao")
        if privada_local is None:
            _marcar("/painel/sessao", "BLOQUEIO", "sem sessao", claims)
            return _negar("/painel/sessao")
        velho = _cookie_revogado(headers, ip_visita, arquivo)
        if velho is not None:
            _marcar("/painel/sessao", "BLOQUEIO", "sessao revogada", velho)
            return _negar("/painel/sessao")
        claims = dict(claims)
        claims["sid"] = secrets.token_hex(8)
        token = emitir(privada_local, claims)
        _marcar("login", "ok", "login", claims)
        # 303 Location: /painel faz o iframe de altura zero carregar outro
        # Streamlit. O sessionStorage é o do pai e a tela volta para o token.
        corpo = b"<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head><body>ok</body></html>"
        return {
            "liga": False,
            "status": 200,
            "corpo": corpo,
            "tipo": "text/html; charset=utf-8",
            "headers": [
                "Set-Cookie: " + cabecalho_cookie(token),
                "Cache-Control: no-store",
                "X-Content-Type-Options: nosniff",
            ],
            "consultou": True,
        }

    return antes


def _deslizar(acao, claims, privada, geracoes):
    acao["set_cookie"] = cabecalho_cookie(renovar(claims, privada))
    usuario = claims["u"]
    geracao = claims["g"]

    def ainda(nome=usuario, numero=geracao, arquivo=geracoes):
        item = ler_geracao(arquivo, nome)
        return bool(item and item["ativo"] and item["g"] == numero)

    acao["ainda_vale"] = ainda


def _prova_cookies(headers, nonce: str) -> dict | None:
    cookie = (headers or {}).get("cookie", "")
    origem = ler_cookie(cookie, "n8groker_origem")
    valor = nonce or ler_cookie(cookie, "n8groker_desafio")
    if not origem or not valor:
        return None
    try:
        spki = spki_do_texto(ler_cookie(cookie, "n8groker_spki"))
    except Exception:
        return None
    if not conferir(
        spki,
        valor,
        origem,
        ler_cookie(cookie, "n8groker_horario"),
        ler_cookie(cookie, "n8groker_assinatura"),
    ):
        return None
    return {"origem": origem, "dispositivo": impressao(spki), "nonce": valor}


def _motivo_prova(headers, claims) -> str:
    if not isinstance(claims, dict):
        return "sem sessao"
    prova = _prova_cookies(headers, str(claims.get("nonce") or ""))
    if not isinstance(prova, dict):
        return "origem nao confere"
    if prova["origem"] != claims.get("origem") or prova["dispositivo"] != claims.get("dispositivo"):
        return "origem nao confere"
    if prova["nonce"] != claims.get("nonce"):
        return "origem nao confere"
    return ""


def _prova(headers, claims) -> bool:
    return _motivo_prova(headers, claims) == ""


def _hmac_pedido(ip: str) -> str:
    chave = ler_chave_hmac()
    if not chave:
        return ""
    mac = hmac.new(chave, ip.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"x-n8groker-client: {ip}|{mac}"
