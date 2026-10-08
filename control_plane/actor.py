"""Quem está na sessão, e o que a função que executa pode fazer.

Sem PANEL_MODE=console ou edge, a checagem não corre. Os testes que chamam
operações direto continuam iguais. Com o modo ligado, ator ausente nega.
"""

from __future__ import annotations

import contextvars
import os
from pathlib import Path

_ATOR = contextvars.ContextVar("n8groker_ator", default=None)
_ACOES_ADMIN = frozenset({"stack", "admin", "diagnostico", "backup"})
_ACOES_APP = frozenset({"ver", "operar", "abrir"})


class AccessDenied(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def panel_mode() -> str:
    return os.environ.get("PANEL_MODE", "").strip()


def panel_enforced() -> bool:
    return panel_mode() in {"console", "edge"}


def set_actor(actor: dict | None) -> None:
    _ATOR.set(actor)


def get_actor():
    return _ATOR.get()


def allows(acao: str, service_id: str | None = None) -> bool:
    if not panel_enforced():
        return True
    actor = get_actor()
    if not isinstance(actor, dict):
        return False
    if actor.get("must_change") or actor.get("aguardando"):
        return False
    if actor.get("admin"):
        return True
    if acao in _ACOES_ADMIN:
        return False
    if acao not in _ACOES_APP or not service_id:
        return False
    lista = actor.get(acao) or []
    return service_id in lista


def exigir(acao: str, service_id: str | None = None) -> None:
    if allows(acao, service_id):
        return
    _linha(acao, service_id or "", "negado")
    raise AccessDenied("Sem permissão para esta ação.")


def concluir(acao: str, alvo: str) -> None:
    if not panel_enforced():
        return
    _linha(acao, alvo, "ok")


def _linha(acao: str, alvo: str, resultado: str) -> None:
    if not panel_enforced():
        return
    actor = get_actor()
    if not isinstance(actor, dict) or not actor.get("root"):
        return
    from control_plane.audit import auditar

    usuario = "admin" if actor.get("admin") else str(actor.get("username") or "")
    try:
        auditar(Path(str(actor["root"])), usuario, str(actor.get("ip") or ""), acao, alvo, resultado)
    except ValueError:
        return
