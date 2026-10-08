"""Tokens de aprovação fora do .env.

O compose do Scout carrega o `.env` da raiz. Um segredo nesse arquivo entra no
container. A aba Admin e o n8n usam tokens diferentes, os dois em `.n8groker/`.
"""

from __future__ import annotations

import sys
import secrets
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1] / "Scout_OSINT_Docker"
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scout.core.gravar_arquivo import gravar_bytes  # noqa: E402

ARQUIVO_PAINEL = "porteiro-painel.token"
ARQUIVO_N8N = "porteiro-n8n.token"
ARQUIVO_N8N_ENV = "porteiro-n8n.env"


def pasta(root: Path) -> Path:
    return Path(root) / ".n8groker"


def _ler(path: Path) -> str:
    try:
        texto = path.read_text(encoding="utf-8").replace("\ufeff", "").strip()
    except OSError:
        return ""
    if not texto or "\n" in texto or "\r" in texto:
        return ""
    return texto


def _gravar(path: Path, conteudo: str) -> None:
    # porteiro-painel.token é montado sozinho no Scout. Trocar o inode quebra o bind.
    gravar_bytes(path, conteudo.encode("utf-8"))


def garantir(root: Path) -> dict[str, str]:
    """Cria os dois tokens se faltarem. Não rotaciona um valor já gravado."""
    destino = pasta(root)
    saida: dict[str, str] = {}
    for nome in (ARQUIVO_PAINEL, ARQUIVO_N8N):
        path = destino / nome
        atual = _ler(path)
        if len(atual) < 16:
            atual = secrets.token_urlsafe(32)
            _gravar(path, atual + "\n")
        saida[nome] = atual
    linha = "PORTEIRO_N8N_TOKEN=" + saida[ARQUIVO_N8N] + "\n"
    env = destino / ARQUIVO_N8N_ENV
    if _ler_bruto(env) != linha:
        _gravar(env, linha)
    return saida


def _ler_bruto(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").replace("\ufeff", "")
    except OSError:
        return ""


def token_painel(root: Path) -> str:
    return _ler(pasta(root) / ARQUIVO_PAINEL)


def token_n8n(root: Path) -> str:
    return _ler(pasta(root) / ARQUIVO_N8N)
