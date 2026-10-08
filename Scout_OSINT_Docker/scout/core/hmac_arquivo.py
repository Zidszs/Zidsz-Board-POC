"""Arquivo da chave HMAC que o Scout monta em /run. Pasta não assina cabeçalho."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("scout.hmac")

MOTIVO_AUSENTE = "cabeçalho do Porteiro ausente"


def caminho_hmac() -> Path:
    return Path(os.environ.get("PORTEIRO_HMAC_KEY_ARQUIVO", "/run/porteiro-hmac.key"))


def estado_hmac(caminho: Path | None = None) -> str:
    path = caminho_hmac() if caminho is None else caminho
    if path.is_dir():
        return "diretorio"
    if not path.is_file():
        return "ausente"
    try:
        tamanho = path.stat().st_size
    except OSError:
        return "ausente"
    if tamanho <= 0:
        return "vazio"
    return "ok"


_MENSAGENS = {
    "diretorio": (
        "A chave HMAC do Porteiro é um diretório, não um arquivo. "
        "O cabeçalho x-n8groker-client não será assinado."
    ),
    "ausente": (
        "A chave HMAC do Porteiro não existe. "
        "O cabeçalho x-n8groker-client não será assinado."
    ),
    "vazio": (
        "A chave HMAC do Porteiro está vazia. "
        "O cabeçalho x-n8groker-client não será assinado."
    ),
}


def avisar_hmac(caminho: Path | None = None) -> str:
    texto = _MENSAGENS.get(estado_hmac(caminho), "")
    if texto:
        logger.error(texto)
    return texto


def ler_chave_hmac(caminho: Path | None = None) -> bytes:
    if estado_hmac(caminho) != "ok":
        return b""
    path = caminho_hmac() if caminho is None else caminho
    try:
        return path.read_bytes()
    except OSError:
        return b""
