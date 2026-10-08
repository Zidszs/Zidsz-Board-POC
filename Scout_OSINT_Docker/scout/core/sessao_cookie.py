"""Cookie de sessão Ed25519. A chave fica em `.n8groker/`, fora do `.env`.

A renovação é deslizante: cada uso aceito empurra o prazo. A geração no
arquivo derruba o cookie na hora, sem esperar o vencimento.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from scout.core.gravar_arquivo import gravar_bytes

NOME = "n8groker_sessao"
TTL_PADRAO = 900


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(texto: str) -> bytes:
    resto = (-len(texto)) % 4
    return base64.urlsafe_b64decode(texto + ("=" * resto))


def _pasta(root: Path) -> Path:
    path = (Path(root) / ".n8groker").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def caminho_chave(root: Path) -> Path:
    return _pasta(root) / "sessao.key"


def caminho_pub(root: Path) -> Path:
    return _pasta(root) / "sessao.pub"


def caminho_geracao(root: Path) -> Path:
    return _pasta(root) / "sessoes-geracao.json"


def _gravar(path: Path, data: bytes) -> None:
    # sessoes-geracao.json e sessao.key são um arquivo por montagem.
    # os.replace troca o inode e o Docker Desktop no Windows recusa (WinError 5).
    gravar_bytes(path, data)


def garantir(root: Path) -> None:
    chave = caminho_chave(root)
    if not chave.is_file():
        privada = Ed25519PrivateKey.generate()
        _gravar(chave, privada.private_bytes_raw())
        _gravar(caminho_pub(root), privada.public_key().public_bytes_raw())
    geracao = caminho_geracao(root)
    if not geracao.is_file():
        _gravar(geracao, b"{}\n")


def _ler_geracoes(path: Path) -> dict:
    try:
        dados = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def ler_geracao(path: Path, usuario: str) -> dict | None:
    item = _ler_geracoes(path).get(usuario)
    if not isinstance(item, dict):
        return None
    if not isinstance(item.get("g"), int) or not isinstance(item.get("ativo"), bool):
        return None
    return item


def iniciar_usuario(path: Path, usuario: str) -> None:
    dados = _ler_geracoes(path)
    if usuario not in dados:
        dados[usuario] = {"g": 1, "ativo": True}
        _gravar(path, (json.dumps(dados, ensure_ascii=False) + "\n").encode("utf-8"))


def subir_geracao(path: Path, usuario: str, *, ativo: bool = True) -> int:
    dados = _ler_geracoes(path)
    atual = dados.get(usuario) if isinstance(dados.get(usuario), dict) else {}
    numero = int(atual.get("g") or 0) + 1
    dados[usuario] = {"g": numero, "ativo": bool(ativo)}
    _gravar(path, (json.dumps(dados, ensure_ascii=False) + "\n").encode("utf-8"))
    return numero


def ttl_segundos() -> int:
    try:
        valor = int(os.environ.get("N8GROKER_SESSAO_SEG", str(TTL_PADRAO)))
    except (TypeError, ValueError):
        return TTL_PADRAO
    return valor if valor > 0 else TTL_PADRAO


def _sid(valor) -> str:
    texto = str(valor or "")
    if len(texto) == 16 and all(caractere in "0123456789abcdef" for caractere in texto):
        return texto
    return secrets.token_hex(8)


def emitir(privada: bytes, claims: dict, agora: int | None = None) -> str:
    momento = int(time.time() if agora is None else agora)
    corpo = {
        "u": str(claims["u"]),
        "ip": str(claims["ip"]),
        "iat": momento,
        "exp": momento + ttl_segundos(),
        "g": int(claims["g"]),
        "app": str(claims.get("app") or ""),
        "abrir": [str(item) for item in (claims.get("abrir") or [])],
        "origem": str(claims.get("origem") or ""),
        "dispositivo": str(claims.get("dispositivo") or ""),
        "nonce": str(claims.get("nonce") or ""),
        "sid": _sid(claims.get("sid")),
    }
    cab = _b64(json.dumps({"alg": "EdDSA", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    meio = _b64(json.dumps(corpo, separators=(",", ":")).encode("utf-8"))
    assinatura = Ed25519PrivateKey.from_private_bytes(privada).sign(f"{cab}.{meio}".encode("ascii"))
    return f"{cab}.{meio}.{_b64(assinatura)}"


def _claims(token: str, publica: bytes, agora: int) -> dict | None:
    partes = token.split(".")
    if len(partes) != 3 or any(("\n" in parte or "\r" in parte) for parte in partes):
        return None
    try:
        Ed25519PublicKey.from_public_bytes(publica).verify(_unb64(partes[2]), f"{partes[0]}.{partes[1]}".encode("ascii"))
        corpo = json.loads(_unb64(partes[1]))
    except (InvalidSignature, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(corpo, dict):
        return None
    exp = corpo.get("exp")
    if not isinstance(exp, int) or exp < agora:
        return None
    if not isinstance(corpo.get("u"), str) or not isinstance(corpo.get("ip"), str):
        return None
    if not isinstance(corpo.get("g"), int):
        return None
    return corpo


def ler_cookie(cabecalho: str, nome: str = NOME) -> str:
    if not cabecalho:
        return ""
    for parte in str(cabecalho).split(";"):
        if "=" not in parte:
            continue
        chave, valor = parte.split("=", 1)
        if chave.strip() == nome:
            return valor.strip()
    return ""


def cabecalho_cookie(token: str) -> str:
    if not token or any(ch in token for ch in "\r\n;"):
        raise ValueError("Cookie recusado.")
    return f"{NOME}={token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age={ttl_segundos()}"


def verificar(token: str, publica: bytes, geracoes: Path, ip: str, agora: int | None = None) -> dict | None:
    momento = int(time.time() if agora is None else agora)
    corpo = _claims(token, publica, momento)
    if corpo is None or corpo.get("ip") != ip:
        return None
    item = ler_geracao(geracoes, corpo["u"])
    if item is None or not item["ativo"] or item["g"] != corpo["g"]:
        return None
    return corpo


def renovar(corpo: dict, privada: bytes, agora: int | None = None) -> str:
    return emitir(privada, corpo, agora)
