"""Trilha de autenticação. Não entra na janela de 20 nem no webhook.

O arquivo é `.n8groker/trilha/auth.jsonl`, ao lado de `trilha.jsonl`.
O leitor da trilha de caminho não abre este arquivo. O motivo gravado é
um código da lista fixa. A frase fica na tela, em `proximo_passo`.

Aviso só na tela do console, fora de `alertas.jsonl` e do webhook:
`N8GROKER_AUTH_RAJADA` (padrão 5) `auth.sessao` do mesmo IP em
`N8GROKER_AUTH_RAJADA_SEG` (padrão 60) segundos, e qualquer `auth.sessao`
com motivo `origem` nessa mesma janela. Nenhum dos dois bloqueia acesso.
O `sid` de 16 hex é identificador, não segredo, e entra na linha.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

MOTIVOS = frozenset(
    {
        "ok",
        "vazio",
        "formato",
        "chave",
        "assinatura",
        "prazo",
        "revogado",
        "repetido",
        "conta",
        "tela",
        "sessao",
        "sem_sessao",
        "origem",
        "rotacionado",
        "relogio",
        "registro",
    }
)

PASSOS = frozenset(
    {
        "auth.login",
        "auth.emitir",
        "auth.revogar",
        "auth.rotacionar",
        "auth.sessao",
    }
)

# A tela de entrar mostra só isto. O código não entra na frase.
PROXIMO_PASSO = {
    "ok": "",
    "vazio": "Preencha o campo",
    "formato": "Peça outro token ao dono",
    "chave": "Espere o dono. Falta a chave nesta máquina",
    "assinatura": "Peça outro token ao dono",
    "prazo": "Peça outro token ao dono. Este venceu",
    "revogado": "Peça outro token ao dono",
    "repetido": "Volte ao navegador em que você já entrou",
    "conta": "Espere o dono",
    "tela": "Esta tela não aceita este acesso",
    "sessao": "Volte ao navegador em que você entrou",
    "sem_sessao": "Entre de novo nesta tela",
    "origem": "Espere o dono aprovar esta origem",
    "rotacionado": "Peça outro token ao dono. A chave girou",
    "relogio": "Acerte o relógio desta máquina e peça outro token",
    # O convidado não vê o nome do arquivo. A seção Autenticação do console mostra.
    "registro": "Espere o dono",
}


def proximo_passo(motivo: str) -> str:
    return PROXIMO_PASSO.get(motivo, PROXIMO_PASSO["formato"])


_SUCESSO_REVOGAR = "Sessão revogada. O próximo pedido desse cookie cai."
_FALHA_REVOGAR = "O Scout não respondeu. A sessão não foi revogada."


def caminho(root: Path) -> Path:
    base = (Path(root).resolve() / ".n8groker" / "trilha").resolve()
    path = base / "auth.jsonl"
    if path.parent != base:
        raise ValueError("Arquivo de auth fora da pasta da trilha.")
    return path


def _scout():
    import sys

    raiz = Path(__file__).resolve().parents[1] / "Scout_OSINT_Docker"
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))


def _campo(valor, limite: int) -> str:
    _scout()
    from scout.core.trilha import recortar_segredo

    return recortar_segredo(valor, limite)


def _sid(valor) -> str:
    _scout()
    from scout.core.trilha import sid_de

    return sid_de({"sid": valor})


def anotar_auth(
    root: Path,
    *,
    ip: str,
    conta: str,
    passo: str,
    resultado: str,
    motivo: str,
    arquivo: str = "",
    chave: str = "",
    sid: str = "",
) -> None:
    """Grava um passo. Falha de disco não derruba o login."""
    try:
        if passo not in PASSOS or resultado not in {"ok", "recusado"} or motivo not in MOTIVOS:
            return
        if resultado == "ok":
            motivo = "ok"
        elif motivo == "ok":
            return
        agora = time.time()
        linha = {
            "quando": int(agora),
            "hora": datetime.fromtimestamp(agora, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ip": _campo(ip, 64) or "127.0.0.1",
            "conta": _campo(conta, 64),
            "passo": passo,
            "resultado": resultado,
            "motivo": motivo,
        }
        nome = _campo(arquivo, 64)
        if nome:
            linha["arquivo"] = nome
        if chave == "anterior":
            linha["chave"] = "anterior"
        identificador = _sid(sid)
        if identificador:
            linha["sid"] = identificador
        _scout()
        from scout.core.trilha import gravar_jsonl

        gravar_jsonl(caminho(root), linha)
    except Exception:
        return


def ler_auth(root: Path) -> list[dict]:
    try:
        path = caminho(root)
    except ValueError:
        return []
    _scout()
    from scout.core.trilha import ler_cauda_texto

    bruto = ler_cauda_texto(path)
    saida = []
    for linha in bruto.splitlines():
        if not linha.strip() or "\ufffd" in linha:
            continue
        try:
            item = json.loads(linha)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("passo") not in PASSOS:
            continue
        if item.get("resultado") not in {"ok", "recusado"} or item.get("motivo") not in MOTIVOS:
            continue
        saida.append(item)
    return saida


def base_do_scout() -> str:
    """Gestão do Scout em loopback. Host de fora volta para 127.0.0.1:8765."""
    _scout()
    from scout.gate import DEFAULT_HTTP, api_base

    bruto = os.environ.get("SCOUT_HTTP_URL", "").strip() or os.environ.get("SCOUT_BACKEND_URL", "").strip()
    base = api_base(bruto or DEFAULT_HTTP)
    host = urlparse(base).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return DEFAULT_HTTP
    return base


def revogar_sessao(root: Path, sid: str, *, conta: str, ip: str, transport=None) -> dict:
    """Pede POST /sessoes/revogar ao Scout. auth.revogar só entra depois do ok."""
    _scout()
    from scout.gate import GateError, ScoutGate

    from control_plane.audit import auditar

    identificador = _sid(sid)
    if not identificador:
        return {"ok": False, "mensagem": "Sessão inválida. Nada foi revogado."}
    gate = ScoutGate(base_do_scout(), transport=transport)
    try:
        resposta = gate._request("POST", "/sessoes/revogar", {"sid": identificador})
    except GateError as exc:
        return {"ok": False, "mensagem": exc.message or _FALHA_REVOGAR}
    except Exception:
        return {"ok": False, "mensagem": _FALHA_REVOGAR}
    if not isinstance(resposta, dict) or resposta.get("ok") is not True:
        return {"ok": False, "mensagem": "O Scout não confirmou a revogação. A sessão continua."}
    anotar_auth(
        root,
        ip=ip or "127.0.0.1",
        conta=conta,
        passo="auth.revogar",
        resultado="ok",
        motivo="ok",
        sid=identificador,
    )
    try:
        auditar(root, "admin", str(ip or ""), "revogar_sessao", identificador, "ok")
    except ValueError:
        pass
    return {"ok": True, "mensagem": _SUCESSO_REVOGAR}


def mensagens_da_revogacao(resultado: dict) -> tuple[str, str]:
    """Sucesso só com ok da API. Falha nunca devolve a frase de sessão revogada."""
    if isinstance(resultado, dict) and resultado.get("ok") is True:
        return _SUCESSO_REVOGAR, ""
    texto = str(resultado.get("mensagem") or "") if isinstance(resultado, dict) else ""
    if not texto or _SUCESSO_REVOGAR in texto:
        texto = _FALHA_REVOGAR
    return "", texto


def legiveis_de(eventos: list[dict]) -> list[str]:
    linhas = []
    for item in eventos:
        partes = [
            str(item.get("hora") or ""),
            str(item.get("ip") or ""),
            str(item.get("conta") or ""),
            str(item.get("passo") or ""),
            str(item.get("resultado") or ""),
            str(item.get("motivo") or ""),
        ]
        if item.get("sid"):
            partes.append(str(item["sid"]))
        if item.get("arquivo"):
            partes.append(str(item["arquivo"]))
        if item.get("chave") == "anterior":
            partes.append("chave anterior")
        linhas.append(" · ".join(partes))
    return linhas


def legiveis_auth(root: Path) -> list[str]:
    return legiveis_de(ler_auth(root))


def linhas_auth(root: Path, *, sid: str = "", ip: str = "") -> list[str]:
    """Filtra pelo sid ou pelo IP antes de cortar as 30 linhas da tela."""
    eventos = ler_auth(root)
    if sid:
        eventos = [item for item in eventos if item.get("sid") == sid]
    elif ip:
        eventos = [item for item in eventos if item.get("ip") == ip]
    return legiveis_de(eventos)[-30:]


def avisos_de_tela(eventos: list[dict], agora: int | None = None) -> list[str]:
    """Faixa amarela. Não grava alerta e não chama o webhook."""
    _scout()
    from scout.core.trilha import _inteiro

    marca = int(time.time() if agora is None else agora)
    limite = _inteiro("N8GROKER_AUTH_RAJADA", 5)
    janela = _inteiro("N8GROKER_AUTH_RAJADA_SEG", 60)
    contagem: dict[str, int] = {}
    origem: dict[str, bool] = {}
    for item in eventos or []:
        if not isinstance(item, dict) or item.get("passo") != "auth.sessao":
            continue
        try:
            quando = int(item.get("quando") or 0)
        except (TypeError, ValueError):
            continue
        if quando < marca - janela or quando > marca + 1:
            continue
        endereco = str(item.get("ip") or "")
        if not endereco:
            continue
        contagem[endereco] = contagem.get(endereco, 0) + 1
        if item.get("motivo") == "origem":
            origem[endereco] = True
    saida = []
    for endereco in sorted(set(contagem) | set(origem)):
        if origem.get(endereco):
            saida.append(
                f"Aviso só na tela: o IP {endereco} teve recusa de sessão com motivo origem. "
                "Não bloqueia o acesso e não chama o webhook."
            )
        if contagem.get(endereco, 0) >= limite:
            saida.append(
                f"Aviso só na tela: o IP {endereco} juntou {contagem[endereco]} recusas auth.sessao "
                f"em {janela} s. Não bloqueia o acesso e não chama o webhook."
            )
    return saida
