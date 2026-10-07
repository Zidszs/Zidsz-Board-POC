"""IPs bloqueados na borda Scout."""
import json
import threading
from pathlib import Path

from scout.core.route_config import DATA_DIR

BLOCKLIST_FILE = DATA_DIR / "blocklist.json"


class BlocklistStore:
    def __init__(self, path: Path = BLOCKLIST_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._ips: set[str] = set()
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._ips = set(data.get("ips", []))
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"ips": sorted(self._ips)}, indent=2),
            encoding="utf-8",
        )

    def is_blocked(self, ip: str) -> bool:
        with self._lock:
            return ip in self._ips

    def add(self, ip: str) -> dict:
        with self._lock:
            self._ips.add(ip)
            self._save()
            return {"ip": ip, "blocked": True}

    def remove(self, ip: str) -> dict:
        with self._lock:
            self._ips.discard(ip)
            self._save()
            return {"ip": ip, "blocked": False}

    def list_all(self) -> list[str]:
        with self._lock:
            return sorted(self._ips)
