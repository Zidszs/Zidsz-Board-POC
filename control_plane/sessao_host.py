"""Atalho do host para a chave de sessão que o Scout também lê."""

from __future__ import annotations

import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1] / "Scout_OSINT_Docker"
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scout.core.sessao_cookie import (  # noqa: E402
    caminho_geracao,
    garantir,
    iniciar_usuario,
    subir_geracao,
)


def preparar(root: Path) -> None:
    garantir(root)


def ao_criar(root: Path, usuario: str) -> None:
    garantir(root)
    iniciar_usuario(caminho_geracao(root), usuario)


def ao_mudar(root: Path, usuario: str, *, ativo: bool = True) -> None:
    garantir(root)
    subir_geracao(caminho_geracao(root), usuario, ativo=ativo)
