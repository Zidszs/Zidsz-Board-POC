"""O painel de borda pede o cookie de sessão ao Scout. Não grava o cookie sozinho."""

from __future__ import annotations

import sys
from pathlib import Path

from control_plane.auth import AuthError
from control_plane.edge_auth import load_key

_RAIZ = Path(__file__).resolve().parents[1] / "Scout_OSINT_Docker"
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scout.core.sessao_cookie import ler_geracao  # noqa: E402
from scout.core.ticket_sessao import emitir_ticket  # noqa: E402


def ticket_de_login(root: Path, username: str, abrir: list, ip: str) -> str:
    from control_plane.sessao_host import caminho_geracao, garantir

    if not username or not ip:
        raise AuthError("Sessão recusada.")
    garantir(root)
    item = ler_geracao(caminho_geracao(root), username)
    if item is None or not item["ativo"]:
        raise AuthError("Sessão recusada.")
    chave = load_key(root)
    if not chave:
        raise AuthError("Sessão recusada.")
    return emitir_ticket(
        chave,
        usuario=username,
        ip=ip,
        geracao=int(item["g"]),
        abrir=list(abrir or []),
    )


def _prova_da_origem(cabecalho: str, corpo: dict) -> bool:
    from scout.core.origem import conferir, impressao, spki_do_texto
    from scout.core.sessao_cookie import ler_cookie

    origem = ler_cookie(cabecalho, "n8groker_origem")
    nonce = str(corpo.get("nonce") or "") or ler_cookie(cabecalho, "n8groker_desafio")
    if not origem or not nonce or origem != str(corpo.get("origem") or ""):
        return False
    try:
        spki = spki_do_texto(ler_cookie(cabecalho, "n8groker_spki"))
    except Exception:
        return False
    if not conferir(
        spki,
        nonce,
        origem,
        ler_cookie(cabecalho, "n8groker_horario"),
        ler_cookie(cabecalho, "n8groker_assinatura"),
    ):
        return False
    if impressao(spki) != str(corpo.get("dispositivo") or ""):
        return False
    if str(corpo.get("nonce") or "") and nonce != str(corpo.get("nonce")):
        return False
    return True


def retomar_do_cookie(root: Path, cabecalho: str, ip: str) -> dict | None:
    """O recarregar abre outra sessão do Streamlit. O cookie HttpOnly já autenticou.

    Não devolve o JWT do usuário, não o grava e não escreve trilha. A assinatura,
    o prazo, a geração, o IP e a prova da origem têm de bater. Um sid já revogado
    também recusa: a tela pede o token de novo e a conta continua ativa. O cookie
    em si continua HttpOnly, Secure e SameSite=Lax.
    """
    from control_plane.auth import load_users
    from control_plane.binding import vinculo_ativo
    from control_plane.sessao_host import caminho_geracao, garantir
    from scout.core.sessao_cookie import ler_cookie, verificar
    from scout.core.trilha import Trilha, pasta_de, sid_de

    if not ip or not cabecalho:
        return None
    token = ler_cookie(cabecalho, "n8groker_sessao")
    if not token:
        return None
    try:
        publica = (Path(root) / ".n8groker" / "sessao.pub").read_bytes()
    except OSError:
        return None
    if len(publica) != 32:
        return None
    garantir(root)
    corpo = verificar(token, publica, caminho_geracao(root), ip)
    if not isinstance(corpo, dict) or not _prova_da_origem(cabecalho, corpo):
        return None
    nome = str(corpo.get("u") or "")
    usuario = None
    for item in load_users(root):
        if isinstance(item, dict) and item.get("username") == nome:
            usuario = item
            break
    if usuario is None or usuario.get("status") != "ativo":
        return None
    if Trilha(pasta_de(root)).revogada(sid_de(corpo)):
        return None
    return {
        "username": nome,
        "admin": False,
        "must_change": False,
        "aguardando": not vinculo_ativo(root, ip, nome),
        "ver": [],
        "operar": [],
        "abrir": [str(item) for item in (corpo.get("abrir") or []) if isinstance(item, str)],
    }
