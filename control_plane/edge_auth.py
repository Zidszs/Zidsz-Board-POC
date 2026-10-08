"""HMAC do IP que o Porteiro grava no caminho do painel. A chave fica em `.n8groker/`."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path


def sign_client(ip: str, key: bytes) -> str:
    mac = hmac.new(key, ip.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{ip}|{mac}"


def verify_client(value: str, key: bytes) -> str:
    if not isinstance(value, str) or not key or "|" not in value:
        return ""
    ip, mac = value.rsplit("|", 1)
    if not ip or not mac:
        return ""
    expected = hmac.new(key, ip.encode("utf-8"), hashlib.sha256).hexdigest()
    try:
        if not hmac.compare_digest(mac, expected):
            return ""
    except ValueError:
        return ""
    return ip


def key_path(root: Path) -> Path:
    return (root / ".n8groker" / "porteiro-hmac.key").resolve()


def descrever_chave(root: Path) -> str:
    path = key_path(root)
    if path.is_dir():
        return (
            "A chave HMAC do Porteiro é um diretório, não um arquivo. "
            "O painel de borda não abre sem o cabeçalho assinado."
        )
    if not path.is_file():
        return (
            "A chave HMAC do Porteiro não existe. "
            "O painel de borda não abre sem o cabeçalho assinado."
        )
    try:
        tamanho = path.stat().st_size
    except OSError:
        return (
            "A chave HMAC do Porteiro não pôde ser lida. "
            "O painel de borda não abre sem o cabeçalho assinado."
        )
    if tamanho <= 0:
        return (
            "A chave HMAC do Porteiro está vazia. "
            "O painel de borda não abre sem o cabeçalho assinado."
        )
    return ""


def load_key(root: Path) -> bytes:
    path = key_path(root)
    if path.is_dir():
        return b""
    try:
        data = path.read_bytes()
    except OSError:
        return b""
    return data


def client_ip_from_header(root: Path, header: str) -> str:
    key = load_key(root)
    if not key:
        return ""
    return verify_client(header, key)
