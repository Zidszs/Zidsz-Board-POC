"""Cada app só abre depois da consulta ao Porteiro.

n8n, LiteLLM, Langfuse e qualquer outro id passam pela mesma função.
Não há ramo que devolva host e porta sem essa consulta.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

# De dentro do scout-backend, 127.0.0.1 é o próprio container. O n8n, o
# Langfuse e o LiteLLM são outros containers na rede_comunicacao. O teste
# troca o host com registrar_app. A variável de ambiente, quando existe,
# ganha do padrão.
APPS = {
    "n8n": ("n8n_app", 5678),
    "langfuse": ("langfuse-web", 3000),
    "litellm": ("litellm", 4000),
}

_ENV_APP = {
    "n8n": ("N8N_UPSTREAM_HOST", "N8N_UPSTREAM_PORT"),
    "langfuse": ("LANGFUSE_UPSTREAM_HOST", "LANGFUSE_UPSTREAM_PORT"),
    "litellm": ("LITELLM_UPSTREAM_HOST", "LITELLM_UPSTREAM_PORT"),
}


def destino_de(app: str):
    atual = APPS.get(app)
    par = _ENV_APP.get(app)
    if not atual or not par:
        return atual
    host = os.environ.get(par[0], "").strip()
    if not host:
        return atual
    porta = atual[1]
    bruto = os.environ.get(par[1], "").strip()
    if bruto:
        try:
            porta = int(bruto)
        except ValueError:
            porta = atual[1]
    return host, porta


def registrar_app(app: str, host: str, porta: int) -> None:
    if not app or app == "painel":
        raise ValueError("App recusado.")
    APPS[app] = (host, int(porta))


_CSP_ANALISE = (
    "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


def _html(ip: str, *, navegador_novo: bool = False) -> bytes:
    """Página 202 do túnel. Com Scout na frente, o ngrok cai aqui, não no Porteiro.

    IP ainda pendente fala da aprovação do IP. Navegador novo num IP que já
    está aprovado não diz que o IP está em análise.
    """
    seguro = (
        str(ip)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    if navegador_novo:
        titulo = "Navegador novo neste IP"
        miolo = (
            f"<p>Navegador novo neste IP <b>{seguro}</b>.</p>"
            "<p>Aguarde a aprovacao deste navegador. Esta pagina atualiza sozinha.</p>"
        )
    else:
        titulo = "Acesso em Analise"
        miolo = (
            f"<p>Seu IP (<b>{seguro}</b>) foi enviado para aprovacao do administrador.</p>"
            "<p>Aguarde. Esta pagina atualiza sozinha.</p>"
        )
    pagina = (
        "<!DOCTYPE html><html><head>"
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP_ANALISE}">'
        "</head><body style=\"font-family:sans-serif;text-align:center;"
        "padding:50px;background:#1e1e1e;color:#fff\">"
        f"<h2>{titulo}</h2>"
        f"{miolo}"
        '<p id="origem-aviso" data-recarregar="1">Se o navegador bloquear JavaScript '
        "ou a chave do dispositivo, a origem nao e registrada e o administrador "
        "nao consegue aprovar. Abra este endereco em HTTPS, libere JavaScript e recarregue.</p>"
        "<noscript><p>JavaScript esta desligado. Sem ele a origem nao e registrada "
        "e a aprovacao nao acontece.</p></noscript>"
        '<script src="/origem.js"></script>'
        "</body></html>"
    )
    return pagina.encode("utf-8")


def _parar(status: int, corpo, tipo: str, consultou: bool) -> dict:
    if isinstance(corpo, str):
        corpo = corpo.encode("utf-8")
    return {
        "liga": False,
        "status": status,
        "corpo": corpo,
        "tipo": tipo,
        "consultou": consultou,
    }


def _espera(ip: str, *, navegador_novo: bool = False) -> dict:
    acao = _parar(202, _html(ip, navegador_novo=navegador_novo), "text/html; charset=utf-8", True)
    acao["headers"] = [
        "Content-Security-Policy: " + _CSP_ANALISE,
        "X-Content-Type-Options: nosniff",
        "Referrer-Policy: no-referrer",
        "Cache-Control: no-store",
    ]
    return acao


def _negado() -> dict:
    return _parar(403, "Acesso negado.", "text/plain; charset=utf-8", True)


def exige_porteiro(app: str, ip: str, conta: str, origem: str, consultar) -> dict:
    """Consulta o Porteiro e só então devolve o upstream do app."""
    if not isinstance(app, str) or not app or app == "painel":
        return _parar(403, "Acesso negado.", "text/plain; charset=utf-8", False)
    try:
        registro = consultar(ip, origem, conta, app)
    except Exception:
        return _negado()
    if not isinstance(registro, dict) or registro.get("status") != "aprovado":
        if isinstance(registro, dict) and registro.get("status") == "bloqueado":
            return _negado()
        return _espera(ip)
    if not conta or registro.get("conta_vinculada") != conta:
        return _espera(ip)
    if not _origem_aprovada(registro, origem):
        if _origem_bloqueada(registro, origem):
            return _negado()
        return _espera(ip, navegador_novo=True)
    if registro.get("vinculo") != "ativo":
        return _espera(ip)
    destino = destino_de(app)
    if not destino:
        return _negado()
    host, porta = destino
    return {
        "liga": True,
        "consultou": True,
        "host": host,
        "port": porta,
        "app": app,
    }


def _origem_aprovada(registro: dict, origem: str) -> bool:
    if not origem:
        return False
    origens = registro.get("origens")
    if isinstance(origens, list) and origens:
        for item in origens:
            if isinstance(item, dict) and item.get("origem") == origem and item.get("status") == "aprovado":
                return True
        return False
    return registro.get("origem") == origem


def _origem_bloqueada(registro: dict, origem: str) -> bool:
    if not origem:
        return False
    origens = registro.get("origens")
    if not isinstance(origens, list):
        return False
    for item in origens:
        if isinstance(item, dict) and item.get("origem") == origem and item.get("status") == "bloqueado":
            return True
    return False


def fila_por_ip(visitantes: list, ip: str) -> dict | None:
    for registro in visitantes or []:
        if isinstance(registro, dict) and registro.get("ip") == ip:
            return registro
    return None


class FilaHttp:
    """GET /n8n/fila. Sem token, ou se a chamada falhar, não há registro."""

    def __init__(self, url: str, token: str, timeout: float = 2.0):
        self.url = url
        self.token = token
        self.timeout = timeout

    def consultar(self, ip: str, origem: str, conta: str, app: str) -> dict | None:
        del origem, conta, app
        if not self.token:
            return None
        pedido = urllib.request.Request(
            self.url,
            headers={"X-Admin-Token": self.token, "Host": "127.0.0.1"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as resposta:
                dados = json.loads(resposta.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
            return None
        visitantes = dados.get("visitantes") if isinstance(dados, dict) else None
        return fila_por_ip(visitantes if isinstance(visitantes, list) else [], ip)

    def _raiz(self) -> str:
        base = self.url
        if base.endswith("/fila"):
            base = base[: -len("/fila")]
        return base

    def _admin(self, caminho: str) -> dict | None:
        if not self.token or not caminho.startswith("/"):
            return None
        pedido = urllib.request.Request(
            self._raiz() + caminho,
            headers={"X-Admin-Token": self.token, "Host": "127.0.0.1"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as resposta:
                bruto = resposta.read().decode("utf-8")
                if not bruto:
                    return {}
                dados = json.loads(bruto)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
            return None
        return dados if isinstance(dados, dict) else None

    def tocar(self, ip: str) -> dict | None:
        if not ip:
            return None
        return self._admin("/tocar?" + urllib.parse.urlencode({"ip": ip}))

    def registrar(self, ip: str, origem: str, dispositivo: str) -> dict | None:
        if not ip or not origem or not dispositivo:
            return None
        return self._admin(
            "/registrar-origem?"
            + urllib.parse.urlencode({"ip": ip, "origem": origem, "dispositivo": dispositivo})
        )


def antes_do_porteiro(fila: FilaHttp):
    """Enquanto a sessão não escolhe o app, a fila ainda segura o pipe atual."""

    def antes(pedido, client_ip, entry, proxies=""):
        del entry
        from scout.core.ip_vivo import decidir_ip

        headers = (pedido or {}).get("headers") or {}
        visto = decidir_ip(client_ip, headers.get("x-forwarded-for", ""), proxies)
        if visto["acao"] == "negar" or not visto.get("ip"):
            return _parar(403, "Acesso negado.", "text/plain; charset=utf-8", True)
        registro = fila.consultar(visto["ip"], "", "", "fila")
        if not isinstance(registro, dict):
            return _espera(visto["ip"])
        if registro.get("status") == "bloqueado":
            return _negado()
        if registro.get("status") != "aprovado":
            return _espera(visto["ip"])
        return {"liga": True, "consultou": True, "ip": visto["ip"]}

    return antes


def fila_do_ambiente() -> FilaHttp:
    url = os.environ.get("PORTEIRO_FILA_URL", "http://host.docker.internal:5676/n8n/fila")
    token = os.environ.get("PORTEIRO_PAINEL_TOKEN", "").strip()
    if not token:
        caminho = os.environ.get("PORTEIRO_PAINEL_TOKEN_ARQUIVO", "/run/porteiro-painel.token")
        try:
            token = open(caminho, encoding="utf-8").read().replace("\ufeff", "").strip()
        except OSError:
            token = ""
    return FilaHttp(url, token)
