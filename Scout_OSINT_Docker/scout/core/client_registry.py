"""Registo persistente de IPs vistos na borda Scout."""
import json
import threading
import time
from pathlib import Path

from scout.core.route_config import DATA_DIR

CLIENTS_FILE = DATA_DIR / "known_clients.json"


class ClientRegistry:
    def __init__(self, path: Path = CLIENTS_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._clients: dict[str, dict] = {}
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._clients = data.get("clients", data) if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"clients": self._clients}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def touch(
        self,
        ip: str,
        route_name: str,
        upstream: str,
        mode: str,
        bytes_in: int,
        bytes_out: int,
        blocked: bool = False,
    ):
        if not ip or ip == "unknown":
            return
        now = time.time()
        bt = bytes_in + bytes_out
        with self._lock:
            rec = self._clients.get(ip)
            if not rec:
                rec = {
                    "ip": ip,
                    "first_seen_ts": now,
                    "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "session_count": 0,
                    "bytes_total": 0,
                    "blocked_count": 0,
                    "modes_seen": [],
                }
                self._clients[ip] = rec
            rec["last_seen_ts"] = now
            rec["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
            rec["last_route"] = route_name
            rec["last_upstream"] = upstream
            rec["last_mode"] = mode or "tcp"
            rec["session_count"] = rec.get("session_count", 0) + 1
            rec["bytes_total"] = rec.get("bytes_total", 0) + bt
            rec["last_blocked"] = blocked
            if blocked:
                rec["blocked_count"] = rec.get("blocked_count", 0) + 1
            modes = set(rec.get("modes_seen") or [])
            if mode:
                modes.add(mode)
            rec["modes_seen"] = sorted(modes)
            self._save()

    def remove(self, ip: str) -> bool:
        with self._lock:
            if ip not in self._clients:
                return False
            del self._clients[ip]
            self._save()
            return True

    def list_all(self) -> list[dict]:
        with self._lock:
            rows = [dict(v, ip=ip) for ip, v in self._clients.items()]
        rows.sort(key=lambda r: r.get("last_seen_ts", 0), reverse=True)
        return rows
