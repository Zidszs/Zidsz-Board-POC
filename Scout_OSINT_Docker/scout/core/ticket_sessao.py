"""Ticket curto que o painel assina e o Scout troca pelo cookie de sessão.

O ticket não é o cookie. Ele diz quem entrou e o que pode abrir. A prova
do dispositivo continua nos cookies da origem. Não carrega senha.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

TTL = 120


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(texto: str) -> bytes:
    resto = (-len(texto)) % 4
    return base64.urlsafe_b64decode(texto + ("=" * resto))


def _id_ok(valor: str) -> bool:
    if not valor or len(valor) > 64:
        return False
    return all(c.isalnum() or c in "-_" for c in valor)


def emitir_ticket(
    chave: bytes,
    *,
    usuario: str,
    ip: str,
    geracao: int,
    abrir: list,
    agora: int | None = None,
    ttl: int = TTL,
) -> str:
    if not chave or not usuario or not ip:
        raise ValueError("ticket recusado")
    momento = int(time.time() if agora is None else agora)
    lista = []
    for item in abrir or []:
        texto = str(item)
        if _id_ok(texto) and texto not in lista:
            lista.append(texto)
    corpo = {
        "u": str(usuario),
        "ip": str(ip),
        "g": int(geracao),
        "abrir": lista,
        "exp": momento + int(ttl),
    }
    bruto = _b64(json.dumps(corpo, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    mac = hmac.new(chave, bruto.encode("ascii"), hashlib.sha256).hexdigest()
    return bruto + "." + mac


def ler_ticket(chave: bytes, token: str, *, ip: str, agora: int | None = None) -> dict | None:
    if not chave or not isinstance(token, str) or token.count(".") != 1:
        return None
    if any(ch in token for ch in "\r\n &"):
        return None
    bruto, mac = token.split(".", 1)
    if not bruto or not mac:
        return None
    esperado = hmac.new(chave, bruto.encode("ascii"), hashlib.sha256).hexdigest()
    try:
        if not hmac.compare_digest(esperado, mac):
            return None
    except ValueError:
        return None
    try:
        corpo = json.loads(_unb64(bruto))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(corpo, dict):
        return None
    momento = int(time.time() if agora is None else agora)
    exp = corpo.get("exp")
    if not isinstance(exp, int) or exp < momento:
        return None
    if corpo.get("ip") != ip or not isinstance(corpo.get("u"), str) or not corpo.get("u"):
        return None
    if not isinstance(corpo.get("g"), int):
        return None
    abrir = corpo.get("abrir")
    if not isinstance(abrir, list) or any(not isinstance(item, str) for item in abrir):
        return None
    return corpo
