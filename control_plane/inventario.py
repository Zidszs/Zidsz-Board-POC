"""Inventário da aba Admin. Lê o disco. Não chama o Keeper e não lê privada."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from control_plane.auth import AuthError

FRASE_SESSAO = (
    "O cookie do túnel deixa de validar e o convidado cai no pedido seguinte. "
    "O admin da máquina não usa esse cookie. Pare o Scout, apague o arquivo se ele estiver vazio ou corrompido, "
    "suba de novo com iniciar_servicos.ps1 para o boot criar outro, e só então suba o Scout, "
    "para a montagem ver o arquivo. Quem tinha cookie entra de novo em /painel."
)
FRASE_HMAC = (
    "O HUD-user não abre: falta o cabeçalho assinado. O n8n em 127.0.0.1:5678 não cai. "
    "O boot cria o arquivo se ele estiver ausente ou vazio. Reinicie o Porteiro e o HUD-user "
    "para os dois lerem a mesma chave. Se trocar o arquivo com o Scout no ar, pare o Scout antes."
)
FRASE_TOKENS = (
    "A aba Admin recebe 403 na fila se o token do painel não bater. "
    "O workflow de aprovação recebe 403 se o token do n8n não bater. "
    "O visitante no túnel não cai por isso. Não apague à toa. "
    "Se apagou, o boot gera outro e regrava porteiro-n8n.env. "
    "Recrie o container do n8n para ele ver o env novo. A aba Admin lê o arquivo novo na hora."
)


def _existe(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _kid_curto(publica: bytes) -> str:
    return hashlib.sha256(publica).hexdigest()[:16]


def _sim(valor: bool) -> str:
    return "sim" if valor else "não"


def ler_inventario(root: Path) -> dict:
    from control_plane.admin_token import ler_geracao, pub_path
    from control_plane.user_token import listar, pub_path as usuario_pub

    base = Path(root)
    admin_pub = _existe(pub_path(base))
    caminho_usuario = usuario_pub(base)
    usuario_ok = _existe(caminho_usuario)
    kid = ""
    if usuario_ok:
        try:
            publica = caminho_usuario.read_bytes()
        except OSError:
            publica = b""
        if len(publica) == 32:
            kid = _kid_curto(publica)
        else:
            usuario_ok = False
    geracao = ler_geracao(base)
    jti: list[dict] = []
    jti_erro = False
    try:
        for item in listar(base):
            exp = int(item.get("exp") or 0)
            jti.append(
                {
                    "jti": str(item.get("jti") or ""),
                    "conta": str(item.get("conta") or ""),
                    "validade": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(exp)),
                    "revogado": bool(item.get("revogado")),
                }
            )
    except (AuthError, OSError, ValueError, TypeError):
        jti_erro = True
        jti = []
    return {
        "admin_pub": admin_pub,
        "usuario_pub": usuario_ok,
        "kid": kid,
        "admin_geracao": geracao,
        "jti": jti,
        "jti_erro": jti_erro,
        "maquina": _existe(base / ".n8groker" / "maquina.key"),
        "sessao": _existe(base / ".n8groker" / "sessao.key"),
        "hmac": _existe(base / ".n8groker" / "porteiro-hmac.key"),
        "porteiro_painel": _existe(base / ".n8groker" / "porteiro-painel.token"),
        "porteiro_n8n": _existe(base / ".n8groker" / "porteiro-n8n.token"),
    }


def texto_inventario(root: Path) -> str:
    dados = ler_inventario(root)
    if dados["admin_geracao"] is None:
        geracao = "ilegível"
    else:
        geracao = str(dados["admin_geracao"])
    linhas = [
        f"- admin.pub: {_sim(dados['admin_pub'])}",
        f"- usuario.pub: {_sim(dados['usuario_pub'])}",
        f"- kid da chave de usuário: {dados['kid'] or '—'}",
        f"- admin-geracao: {geracao}",
        f"- maquina.key: {_sim(dados['maquina'])}",
    ]
    if dados["jti_erro"]:
        linhas.append("- jti no prazo: o registro não pôde ser lido")
    elif not dados["jti"]:
        linhas.append("- jti no prazo: nenhum")
    else:
        for item in dados["jti"]:
            linhas.append(
                f"- jti {item['jti']} · conta {item['conta']} · "
                f"válido até {item['validade']} · revogado {_sim(item['revogado'])}"
            )
    linhas.append(f"- sessao.key: {_sim(dados['sessao'])}. {FRASE_SESSAO}")
    linhas.append(f"- porteiro-hmac.key: {_sim(dados['hmac'])}. {FRASE_HMAC}")
    linhas.append(f"- porteiro-painel.token: {_sim(dados['porteiro_painel'])}. {FRASE_TOKENS}")
    linhas.append(f"- porteiro-n8n.token: {_sim(dados['porteiro_n8n'])}. {FRASE_TOKENS}")
    return "\n".join(linhas)
