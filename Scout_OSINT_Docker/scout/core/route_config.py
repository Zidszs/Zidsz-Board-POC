"""Variáveis de ambiente e bootstrap."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("SCOUT_DATA_DIR", "/app/data"))
REDIRECTIONS_FILE = DATA_DIR / "redirections.json"


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
        self.upstream_host = env_str("SCOUT_UPSTREAM_HOST", "127.0.0.1")
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
            "listen_port": self.public_port,
            "upstream_host": self.upstream_host,
            "upstream_port": self.upstream_port,
            "mode": self.default_mode,
            "enabled": True,
            "is_new": False,
        }

    def reconcile_porteiro_entry(self, entry: dict) -> dict:
        """Alinha entrada porteiro-manual com variáveis .env (preserva mode guardado)."""
        seed = self.seed_porteiro_entry()
        entry["listen_port"] = seed["listen_port"]
        entry["upstream_host"] = seed["upstream_host"]
        entry["upstream_port"] = seed["upstream_port"]
        if not (entry.get("mode") or "").strip():
            entry["mode"] = seed["mode"]
        entry["enabled"] = True
        entry["is_new"] = False
        return entry

    def docker_upstream_host(self) -> str:
        """Host para upstream de portas publicadas no host Windows (Porteiro, n8n, etc.)."""
        host = self.upstream_host or "127.0.0.1"
        if host in ("127.0.0.1", "localhost"):
            return "host.docker.internal"
        return host
