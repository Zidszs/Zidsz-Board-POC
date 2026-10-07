"""Aliases IP para visualização."""
import json
import threading
from pathlib import Path

from scout.core.route_config import DATA_DIR

ALIASES_FILE = DATA_DIR / "ip_aliases.json"


class IpAliasStore:
    def __init__(self, path: Path = ALIASES_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._aliases: dict[str, str] = {}
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._aliases = data.get("aliases", data) if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"aliases": self._aliases}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, ip: str) -> str:
        with self._lock:
            return self._aliases.get(ip, "")

    def is_trusted(self, ip: str) -> bool:
        """IP com alias manual — confiável, sem alertas nem bloqueio na borda."""
        with self._lock:
            return bool(self._aliases.get(ip, "").strip())

    def set(self, ip: str, alias: str) -> dict:
        with self._lock:
            if alias:
                self._aliases[ip] = alias
            elif ip in self._aliases:
                del self._aliases[ip]
            self._save()
            return {"ip": ip, "alias": alias}

    def list_all(self) -> list[dict]:
        with self._lock:
            return [{"ip": ip, "alias": a} for ip, a in sorted(self._aliases.items())]

    def display(self, ip: str) -> str:
        alias = self.get(ip)
        return f"{alias} ({ip})" if alias else ip
