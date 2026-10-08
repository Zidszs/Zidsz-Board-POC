"""Auditoria append-only em `.n8groker/audit.jsonl`. Sem JWT e sem token."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def auditar(root: Path, usuario: str, ip: str, acao: str, alvo: str, resultado: str) -> None:
    campos = (usuario, ip, acao, alvo, resultado)
    if not all(isinstance(campo, str) for campo in campos):
        raise ValueError("Linha de auditoria recusada.")
    for campo in campos:
        if "\n" in campo or "\r" in campo or campo.startswith("eyJ"):
            raise ValueError("Linha de auditoria recusada.")
    path = (root / ".n8groker" / "audit.jsonl").resolve()
    base = (root.resolve() / ".n8groker").resolve()
    if path.parent != base:
        raise ValueError("Auditoria fora da pasta local.")
    path.parent.mkdir(parents=True, exist_ok=True)
    linha = {
        "hora": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "usuario": usuario,
        "ip": ip,
        "acao": acao,
        "alvo": alvo,
        "resultado": resultado,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(linha, ensure_ascii=False) + "\n")
