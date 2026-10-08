"""ID de origem preso à chave pública. Não há MAC."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature


def impressao(spki: bytes) -> str:
    return hashlib.sha256(spki).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(texto: str) -> bytes:
    resto = (-len(texto)) % 4
    return base64.urlsafe_b64decode(texto + ("=" * resto))


def mensagem(nonce: str, origem: str, horario: str) -> bytes:
    return f"{nonce}|{origem}|{horario}".encode("utf-8")


def _para_der(assinatura: bytes) -> bytes:
    if len(assinatura) == 64:
        r = int.from_bytes(assinatura[:32], "big")
        s = int.from_bytes(assinatura[32:], "big")
        return encode_dss_signature(r, s)
    return assinatura


def assinar(privada_pem_ou_obj, nonce: str, origem: str, horario: str) -> str:
    bruto = mensagem(nonce, origem, horario)
    assinatura = privada_pem_ou_obj.sign(bruto, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(assinatura)
    cru = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return _b64(cru)


def conferir(spki: bytes, nonce: str, origem: str, horario: str, assinatura_b64: str) -> bool:
    try:
        publica = serialization.load_der_public_key(spki)
        publica.verify(
            _para_der(_unb64(assinatura_b64)),
            mensagem(nonce, origem, horario),
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except Exception:
        return False


def gerar_chave():
    privada = ec.generate_private_key(ec.SECP256R1())
    spki = privada.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return privada, spki


def spki_do_texto(texto: str) -> bytes:
    return _unb64(texto)


def cookies_de_prova(privada, spki: bytes, origem: str, nonce: str, horario: str) -> str:
    partes = [
        "n8groker_origem=" + origem,
        "n8groker_spki=" + _b64(spki),
        "n8groker_assinatura=" + assinar(privada, nonce, origem, horario),
        "n8groker_horario=" + horario,
    ]
    return "; ".join(partes)


def horario_utc(momento: datetime | None = None) -> str:
    agora = momento or datetime.now(timezone.utc)
    return agora.strftime("%Y-%m-%dT%H:%M:%SZ")


def anotar_origem(registro: dict, origem: str, dispositivo: str) -> dict:
    """A origem nova fica pendente. A já aprovada no mesmo IP continua."""
    origens = registro.setdefault("origens", [])
    if not isinstance(origens, list):
        origens = []
        registro["origens"] = origens
    atual = next((item for item in origens if isinstance(item, dict) and item.get("origem") == origem), None)
    aprovada = next(
        (
            item.get("origem")
            for item in origens
            if isinstance(item, dict) and item.get("status") == "aprovado" and item.get("origem") != origem
        ),
        "",
    )
    if atual is None:
        atual = {"origem": origem, "dispositivo": dispositivo, "status": "pendente"}
        origens.append(atual)
        return {"nova": True, "item": atual, "origem_aprovada": aprovada or ""}
    if atual.get("dispositivo") != dispositivo:
        atual["status"] = "pendente"
        atual["dispositivo"] = dispositivo
        return {"nova": True, "item": atual, "origem_aprovada": aprovada or ""}
    return {"nova": False, "item": atual, "origem_aprovada": aprovada or ""}
