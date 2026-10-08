"""Importa o cliente do Scout sem puxar a janela que este ramo removeu."""

from __future__ import annotations

import sys
from pathlib import Path

_SCOUT_ROOT = Path(__file__).resolve().parents[1] / "Scout_OSINT_Docker"
if str(_SCOUT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCOUT_ROOT))

from scout.gate import (  # noqa: E402
    DEFAULT_HTTP,
    GUI_PARITY,
    GateError,
    ScoutGate,
    api_base,
    apply_action,
    coerce_managed_upstream,
    describe_snapshot,
    parse_upstream,
    toggle_decision,
)


def make_gate(base: str | None, *, transport=None, timeout: float = 5) -> ScoutGate:
    return ScoutGate(api_base(base), transport=transport, timeout=timeout)


def query_text(kind: str, *, base: str | None = None, transport=None) -> str:
    gate = make_gate(base or DEFAULT_HTTP, transport=transport, timeout=4)
    try:
        snap = gate.snapshot()
    except GateError as exc:
        return exc.message
    return describe_snapshot(kind, snap)
