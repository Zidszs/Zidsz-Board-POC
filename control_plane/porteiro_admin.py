"""Fila do Porteiro pela escuta local. Não chama o webhook do n8n."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:5676"
_ACOES = {
    "aprovar": "/n8n/aprovar",
    "bloquear": "/n8n/bloquear",
    "vincular": "/n8n/vincular",
    "solicitar": "/n8n/solicitar",
}


class FilaError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _url(caminho: str, params: dict) -> str:
    consulta = urllib.parse.urlencode({chave: valor for chave, valor in params.items() if valor != ""})
    if not consulta:
        return BASE + caminho
    return BASE + caminho + "?" + consulta


def _chamar(url: str, transport, token: str) -> tuple[int, str]:
    headers = {"Accept": "application/json, text/plain"}
    if token:
        headers["X-Admin-Token"] = token
    if transport is not None:
        status, body = transport("GET", url, headers)
        return int(status), str(body)
    request = urllib.request.Request(url, method="GET", headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        corpo = exc.read().decode("utf-8", "replace")
        return int(exc.code), corpo
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        raise FilaError("O Porteiro local não respondeu em 127.0.0.1:5676.") from exc


def _token(token: str | None) -> str:
    if token is None:
        return ""
    return token


def listar_fila(transport=None, token: str | None = None) -> list:
    status, body = _chamar(_url("/n8n/fila", {}), transport, _token(token))
    if status != 200:
        raise FilaError("Não foi possível ler a fila do Porteiro.")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FilaError("A fila não veio em JSON.") from exc
    visitantes = data.get("visitantes") if isinstance(data, dict) else None
    if not isinstance(visitantes, list):
        raise FilaError("A fila não veio em JSON.")
    return [item for item in visitantes if isinstance(item, dict)]


def agir(
    acao: str,
    ip: str,
    conta: str = "",
    transport=None,
    token: str | None = None,
    origem: str = "",
) -> dict:
    caminho = _ACOES.get(acao)
    if caminho is None or not ip:
        raise FilaError("Ação recusada.")
    params = {"ip": ip}
    if acao in {"vincular", "solicitar"}:
        params["conta"] = conta
    if origem:
        params["origem"] = origem
    status, body = _chamar(_url(caminho, params), transport, _token(token))
    n8n_status = None
    if body.startswith("{"):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            n8n_status = payload.get("n8n_status")
    return {"ok": 200 <= status < 300, "status": status, "texto": body, "n8n_status": n8n_status}


def mensagem_recusa(texto: str) -> str:
    """Quando a fila recusa, o motivo pode vir em JSON. A tela não mostra o objeto cru."""
    bruto = str(texto or "").strip()
    if bruto.startswith("{"):
        try:
            data = json.loads(bruto)
        except json.JSONDecodeError:
            return bruto or "O Porteiro recusou."
        if isinstance(data, dict):
            for chave in ("reason", "error", "detail", "texto"):
                valor = data.get(chave)
                if isinstance(valor, str) and valor.strip():
                    return valor.strip()
            return "O Porteiro recusou."
    return bruto or "O Porteiro recusou."


def aplicar(
    session: dict,
    acao: str,
    ip: str,
    conta: str = "",
    transport=None,
    token: str | None = None,
    origem: str = "",
) -> dict:
    """Em sucesso, descarta a fila em memória para a próxima leitura buscar de novo."""
    resultado = agir(acao, ip, conta, transport=transport, token=token, origem=origem)
    if resultado["ok"]:
        session.pop("porteiro_fila", None)
    return resultado


def _origens(item: dict) -> tuple[list[str], list[str]]:
    pendentes: list[str] = []
    aprovadas: list[str] = []
    origens = item.get("origens") if isinstance(item, dict) else None
    if not isinstance(origens, list):
        return pendentes, aprovadas
    for parte in origens:
        if not isinstance(parte, dict):
            continue
        origem = str(parte.get("origem") or "")
        if not origem:
            continue
        if parte.get("status") == "pendente":
            pendentes.append(origem)
        elif parte.get("status") == "aprovado":
            aprovadas.append(origem)
    return pendentes, aprovadas


def plano_da_fila(item: dict) -> dict:
    """O que a aba Admin pode fazer com este IP.

    Aprovar o IP só quando ele está pendente e já há origem.
    Navegador novo num IP aprovado tem botão próprio.
    Liberar usa uma origem: aprovar e vincular no mesmo gesto.
    """
    status = str((item or {}).get("status") or "")
    pendentes, aprovadas = _origens(item or {})
    # Origem pendente neste IP entra em Liberar. A antiga fica só em Vincular.
    if pendentes:
        liberar = pendentes[0]
    elif aprovadas:
        liberar = aprovadas[0]
    else:
        liberar = ""
    return {
        "aprovar": pendentes[0] if status == "pendente" and pendentes else "",
        "navegadores": pendentes if status == "aprovado" else [],
        "vincular": aprovadas[0] if aprovadas else "",
        "liberar": liberar,
    }


def liberar(
    session: dict,
    ip: str,
    conta: str,
    origem: str,
    transport=None,
    token: str | None = None,
) -> dict:
    """Aprova a origem e grava o vínculo. Sem origem, não chama o Porteiro."""
    if not origem:
        return {
            "ok": False,
            "status": 400,
            "texto": "Sem origem registrada neste IP.",
            "n8n_status": None,
            "passo": "origem",
        }
    aprovado = aplicar(session, "aprovar", ip, conta, transport=transport, token=token, origem=origem)
    if not aprovado["ok"]:
        aprovado["passo"] = "aprovar"
        return aprovado
    ligado = aplicar(session, "vincular", ip, conta, transport=transport, token=token, origem=origem)
    ligado["passo"] = "vincular"
    return ligado
