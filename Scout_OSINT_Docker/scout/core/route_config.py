"""Variáveis de ambiente e bootstrap."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("SCOUT_DATA_DIR", "/app/data"))
REDIRECTIONS_FILE = DATA_DIR / "redirections.json"

# 5677 é o Porteiro público. 5676 é a escuta de admin só em loopback.
# A rota oficial porteiro-manual continua com o upstream do ambiente (em geral 5677).
PORTAS_RESERVADAS_PORTEIRO = frozenset({5676, 5677})
# HUD-admin. Não é escuta, destino nem upstream, nem na porteiro-manual.
PORTA_HUD_ADMIN = 8501


def porta_reservada_porteiro(porta) -> bool:
    try:
        numero = int(porta)
    except (TypeError, ValueError):
        return False
    return numero in PORTAS_RESERVADAS_PORTEIRO


def porta_hud_admin(porta) -> bool:
    try:
        numero = int(porta)
    except (TypeError, ValueError):
        return False
    return numero == PORTA_HUD_ADMIN


def porta_sem_hud(porta, reserva: int, atual=None) -> int:
    """Se o ambiente pedir 8501, fica o destino seguro que já havia, ou a reserva."""
    if not porta_hud_admin(porta):
        try:
            return int(porta)
        except (TypeError, ValueError):
            return reserva
    if atual is not None and not porta_hud_admin(atual):
        try:
            return int(atual)
        except (TypeError, ValueError):
            return reserva
    return reserva


def env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


class RouteConfig:
    """Configuração base lida do .env."""

    def __init__(self):
        self.public_port = env_int("SCOUT_PUBLIC_PORT", 4040)
        self.admin_port = env_int("SCOUT_ADMIN_PORT", 8765)
        self.upstream_host = env_str("SCOUT_UPSTREAM_HOST", "host.docker.internal")
        self.upstream_port = env_int("SCOUT_UPSTREAM_PORT", 5000)
        self.default_mode = env_str("SCOUT_DEFAULT_ROUTE_MODE", "tcp") or "tcp"
        self.listen_port_start = env_int("SCOUT_LISTEN_PORT_START", 4040)
        self.ngrok_url = env_str("SCOUT_NGROK_TUNNEL_URL")
        self.enforce_firewall = env_str("SCOUT_ENFORCE_FIREWALL", "1") not in ("0", "false", "False")
        self.data_dir = DATA_DIR

    def seed_porteiro_entry(self) -> dict:
        return {
            "id": "porteiro-manual",
            "source": "manual",
            "name": "porteiro",
            "container": None,
            "docker_host_port": None,
            "listen_port": porta_sem_hud(self.public_port, 4050),
            "upstream_host": self.upstream_host,
            "upstream_port": porta_sem_hud(self.upstream_port, 5677),
            "mode": self.default_mode,
            "enabled": True,
            "is_new": False,
        }

    def reconcile_porteiro_entry(self, entry: dict) -> dict:
        """Alinha entrada porteiro-manual com variáveis .env (preserva mode guardado)."""
        entry["listen_port"] = porta_sem_hud(self.public_port, 4050, entry.get("listen_port"))
        entry["upstream_host"] = self.upstream_host
        entry["upstream_port"] = porta_sem_hud(self.upstream_port, 5677, entry.get("upstream_port"))
        if not (entry.get("mode") or "").strip():
            entry["mode"] = self.default_mode
        entry["enabled"] = True
        entry["is_new"] = False
        return entry

    def docker_upstream_host(self) -> str:
        """Host para upstream de portas publicadas no host Windows (Porteiro, n8n, etc.)."""
        host = self.upstream_host or "127.0.0.1"
        if host in ("127.0.0.1", "localhost"):
            return "host.docker.internal"
        return host
