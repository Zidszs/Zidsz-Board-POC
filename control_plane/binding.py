"""Vínculo conta→IP mora no registro do Porteiro, não num campo só do painel."""

from __future__ import annotations

import json
from pathlib import Path


def controle_path(root: Path) -> Path:
    return root / "n8n" / "storage" / "Porteiro" / "controle_acesso.json"


def _ler_controle(root: Path) -> tuple[str, list]:
    """Devolve (passo, visitantes). passo vazio significa que a lista veio."""
    path = controle_path(root)
    try:
        existe = path.exists()
    except OSError:
        return "leitura", []
    if not existe:
        return "ip", []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return "leitura", []
    visitantes = data.get("visitantes") if isinstance(data, dict) else None
    if not isinstance(visitantes, list):
        return "leitura", []
    return "", [item for item in visitantes if isinstance(item, dict)]


def _registro_do_ip(visitantes: list, ip: str) -> dict | None:
    for registro in visitantes:
        if registro.get("ip") == ip:
            return registro
    return None


def origem_do_cookie(cabecalho: str) -> str:
    if not isinstance(cabecalho, str) or not cabecalho:
        return ""
    for parte in cabecalho.split(";"):
        nome, sep, valor = parte.strip().partition("=")
        if sep and nome == "n8groker_origem":
            return valor.strip()
    return ""


def aviso_navegador_novo(root: Path, ip: str, origem: str) -> str:
    """Frase do /painel quando a origem deste navegador ainda está pendente.

    IP aprovado com outra origem pendente não avisa este navegador.
    Sem origem no cookie, não avisa: o origem.js ainda vai registrar.
    """
    if not ip or not origem:
        return ""
    passo, visitantes = _ler_controle(root)
    if passo:
        return ""
    registro = _registro_do_ip(visitantes, ip)
    if registro is None or registro.get("status") != "aprovado":
        return ""
    origens = registro.get("origens")
    if not isinstance(origens, list):
        return ""
    for parte in origens:
        if (
            isinstance(parte, dict)
            and parte.get("origem") == origem
            and parte.get("status") == "pendente"
        ):
            return f"Navegador novo neste IP {ip}. Aguarde a aprovação deste navegador."
    return ""


def aviso_navegador_novo_sem_cookie(root: Path, ip: str) -> str:
    """Origem pendente já gravada neste IP quando o cookie ainda não veio no pedido.

    O navegador já aprovado manda o cookie da origem antiga e não passa aqui.
    """
    if not ip:
        return ""
    passo, visitantes = _ler_controle(root)
    if passo:
        return ""
    registro = _registro_do_ip(visitantes, ip)
    if registro is None or registro.get("status") != "aprovado":
        return ""
    origens = registro.get("origens")
    if not isinstance(origens, list):
        return ""
    for parte in origens:
        if isinstance(parte, dict) and parte.get("status") == "pendente" and parte.get("origem"):
            return aviso_navegador_novo(root, ip, str(parte["origem"]))
    return ""


def aviso_para_painel(root: Path, ip: str, origem_cookie: str) -> str:
    aviso = aviso_navegador_novo(root, ip, origem_cookie)
    if aviso or origem_cookie:
        return aviso
    return aviso_navegador_novo_sem_cookie(root, ip)


def espera_registro_origem(origem_cookie: str, aviso: str, tentativas: int) -> bool:
    """Um rerun curto enquanto o iframe ainda não gravou a origem."""
    if origem_cookie or aviso or tentativas >= 2:
        return False
    return True


def vinculo_ativo(root: Path, ip: str, username: str) -> bool:
    if not ip or not username:
        return False
    passo, visitantes = _ler_controle(root)
    if passo:
        return False
    registro = _registro_do_ip(visitantes, ip)
    if registro is None:
        return False
    if registro.get("status") != "aprovado":
        return False
    if registro.get("conta_vinculada") != username:
        return False
    if registro.get("vinculo") != "ativo":
        return False
    return True


def mensagem_espera(root: Path, ip: str, username: str) -> str:
    """Qual passo falta na tela de espera. O token continua valendo."""
    endereco = ip or "este IP"
    conta = username or "esta conta"
    passo, visitantes = _ler_controle(root)
    if passo == "leitura":
        return "Não deu para ler o registro de acesso. O token continua valendo."
    registro = _registro_do_ip(visitantes, ip) if ip else None
    if registro is None or passo == "ip":
        return f"Falta aprovar o IP {endereco}. O token continua valendo."
    status = registro.get("status")
    if status == "bloqueado":
        return f"O IP {endereco} está bloqueado. O token continua valendo."
    if status != "aprovado":
        return f"Falta aprovar o IP {endereco}. O token continua valendo."
    if registro.get("conta_vinculada") != username or registro.get("vinculo") != "ativo":
        return (
            f"O IP {endereco} já está aprovado. "
            f"Falta vincular a conta {conta}. O token continua valendo."
        )
    return f"O vínculo da conta {conta} com o IP {endereco} está ativo. O token continua valendo."
