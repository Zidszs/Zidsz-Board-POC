"""Prefixos negados numa janela. Não mexe em alias nem na blocklist."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone


def _inteiro(nome: str, padrao: int) -> int:
    try:
        valor = int(os.environ.get(nome, str(padrao)))
    except (TypeError, ValueError):
        return padrao
    return valor if valor > 0 else padrao


def _anexar_auditoria(ip: str, prefixos: list[str]) -> None:
    caminho = os.environ.get("N8GROKER_AUDIT_ARQUIVO", "").strip()
    if not caminho or "\n" in caminho or "\r" in caminho:
        return
    alvo = ",".join(prefixos)
    if "\n" in alvo or "\r" in alvo or alvo.startswith("eyJ"):
        return
    linha = {
        "hora": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "usuario": "scout",
        "ip": ip,
        "acao": "varredura",
        "alvo": alvo,
        "resultado": "bloqueado",
    }
    try:
        with open(caminho, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(linha, ensure_ascii=False) + "\n")
    except OSError:
        return


def prefixo_de(caminho: str) -> str:
    texto = caminho or "/"
    if texto == "/":
        return "raiz"
    parte = texto.split("?", 1)[0].strip("/").split("/", 1)[0]
    return parte or "raiz"


class Varredura:
    def __init__(self, agora=None):
        self._agora = agora or time.time
        self.caminhos = _inteiro("SCOUT_SCAN_CAMINHOS", 8)
        self.janela = _inteiro("SCOUT_SCAN_JANELA_SEG", 10)
        self.bloqueio = _inteiro("SCOUT_SCAN_BLOQUEIO_SEG", 900)
        self._eventos: dict[str, list] = {}
        self._ate: dict[str, float] = {}
        self.linhas: list[dict] = []

    def bloqueado(self, ip: str) -> bool:
        limite = self._ate.get(ip)
        if limite is None:
            return False
        if self._agora() >= limite:
            self._ate.pop(ip, None)
            self._eventos.pop(ip, None)
            return False
        return True

    def observar(self, ip: str, caminho: str, *, negado: bool) -> bool:
        if self.bloqueado(ip):
            return True
        if not negado:
            return False
        agora = self._agora()
        pref = prefixo_de(caminho)
        eventos = self._eventos.setdefault(ip, [])
        eventos.append((agora, pref))
        corte = agora - self.janela
        distintos = {item for quando, item in eventos if quando >= corte}
        self._eventos[ip] = [(quando, item) for quando, item in eventos if quando >= corte]
        if len(distintos) >= self.caminhos:
            self._ate[ip] = agora + self.bloqueio
            prefixos = sorted(distintos)
            self.linhas.append(
                {
                    "ip": ip,
                    "acao": "varredura",
                    "prefixos": prefixos,
                    "resultado": "bloqueado",
                }
            )
            _anexar_auditoria(ip, prefixos)
            return True
        return False

    def listar(self) -> list[str]:
        return [ip for ip in list(self._ate) if self.bloqueado(ip)]

    def desbloquear(self, ip: str, admin: str) -> None:
        self._ate.pop(ip, None)
        self._eventos.pop(ip, None)
        self.linhas.append(
            {"ip": ip, "acao": "desbloquear_varredura", "admin": admin, "resultado": "ok"}
        )
