"""Registo de redireccionamentos — Docker discovery + manual + toggles."""
import json
import threading
import uuid
from copy import deepcopy
from pathlib import Path

from scout.core.route_config import REDIRECTIONS_FILE, RouteConfig


class RedirectionRegistry:
    def __init__(self, config: RouteConfig | None = None, path: Path = REDIRECTIONS_FILE):
        self.config = config or RouteConfig()
        self._path = path
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._load_or_seed()

    def _load_or_seed(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._entries = data.get("entries", [])
                if self._entries:
                    self._reconcile_after_load()
                    return
            except (OSError, json.JSONDecodeError):
                pass
        with self._lock:
            self._entries = [self.config.seed_porteiro_entry()]
            self._save()

    def _reconcile_after_load(self):
        """Sincroniza porteiro-manual com .env e desactiva rotas conflituosas."""
        changed = False
        with self._lock:
            for e in self._entries:
                if e.get("id") == "porteiro-manual":
                    before = json.dumps(e, sort_keys=True)
                    self.config.reconcile_porteiro_entry(e)
                    if json.dumps(e, sort_keys=True) != before:
                        changed = True
            public = self.config.public_port
            for e in self._entries:
                if e.get("id") == "porteiro-manual":
                    continue
                name = e.get("name", "")
                listen = e.get("listen_port")
                if e.get("enabled") and (
                    name in ("n8n_app", "ngrok_service")
                    or listen == public
                    or (name == "scout-backend" and listen == public)
                ):
                    e["enabled"] = False
                    e["is_new"] = False
                    changed = True
            if changed:
                self._save()

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"entries": self._entries}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _next_listen_port(self) -> int:
        used = {e.get("listen_port") for e in self._entries if e.get("listen_port")}
        port = self.config.listen_port_start
        while port in used:
            port += 1
        return port

    def list_all(self) -> list[dict]:
        with self._lock:
            return [deepcopy(e) for e in self._entries]

    def get_active(self) -> list[dict]:
        with self._lock:
            return [deepcopy(e) for e in self._entries if e.get("enabled")]

    def get_by_id(self, entry_id: str) -> dict | None:
        with self._lock:
            for e in self._entries:
                if e.get("id") == entry_id:
                    return deepcopy(e)
        return None

    def toggle(self, entry_id: str, enabled: bool) -> dict | None:
        with self._lock:
            for e in self._entries:
                if e.get("id") == entry_id:
                    e["enabled"] = enabled
                    e["is_new"] = False
                    self._save()
                    return deepcopy(e)
        return None

    def update(self, entry_id: str, **fields) -> dict | None:
        allowed = {"listen_port", "upstream_host", "upstream_port", "mode", "name", "enabled"}
        with self._lock:
            for e in self._entries:
                if e.get("id") == entry_id:
                    for k, v in fields.items():
                        if k in allowed and v is not None:
                            e[k] = v
                    e["is_new"] = False
                    self._save()
                    return deepcopy(e)
        return None

    def add_manual(
        self,
        name: str,
        upstream_host: str,
        upstream_port: int,
        mode: str = "tcp",
        listen_port: int | None = None,
        enabled: bool = False,
    ) -> dict:
        entry = {
            "id": f"manual-{uuid.uuid4().hex[:8]}",
            "source": "manual",
            "name": name,
            "container": None,
            "docker_host_port": None,
            "listen_port": listen_port or self._next_listen_port(),
            "upstream_host": upstream_host,
            "upstream_port": upstream_port,
            "mode": mode,
            "enabled": enabled,
            "is_new": False,
        }
        with self._lock:
            self._entries.append(entry)
            self._save()
            return deepcopy(entry)

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            for i, e in enumerate(self._entries):
                if e.get("id") == entry_id and e.get("source") == "manual":
                    if entry_id == "porteiro-manual":
                        return False
                    del self._entries[i]
                    self._save()
                    return True
        return False

    def sync_docker(self, inventory: list[dict]) -> list[dict]:
        """Merge inventário Docker — novas portas aparecem com enabled=False."""
        new_entries = []
        with self._lock:
            existing_keys = {
                (e.get("container"), e.get("docker_host_port"))
                for e in self._entries
                if e.get("source") == "docker"
            }
            for c in inventory:
                name = c.get("name", "")
                for p in c.get("published_ports") or []:
                    host_port = p.get("host") or p.get("container")
                    if not host_port:
                        continue
                    key = (name, host_port)
                    if key in existing_keys:
                        continue
                    entry = {
                        "id": f"docker-{name}-{host_port}",
                        "source": "docker",
                        "name": name,
                        "container": name,
                        "docker_host_port": host_port,
                        "listen_port": self._next_listen_port(),
                        "upstream_host": self.config.docker_upstream_host(),
                        "upstream_port": host_port,
                        "mode": self.config.default_mode,
                        "enabled": False,
                        "is_new": True,
                    }
                    self._entries.append(entry)
                    new_entries.append(deepcopy(entry))
                    existing_keys.add(key)
            if new_entries:
                self._save()
        return new_entries

    def published_host_ports(self) -> list[int]:
        ports = []
        with self._lock:
            for e in self._entries:
                hp = e.get("docker_host_port") or e.get("upstream_port")
                if hp and e.get("source") == "docker":
                    ports.append(int(hp))
        return ports

    def allowed_host_ports(self) -> list[int]:
        with self._lock:
            return [
                int(e.get("docker_host_port") or e.get("upstream_port"))
                for e in self._entries
                if e.get("enabled") and (e.get("docker_host_port") or e.get("upstream_port"))
            ]

    def blocked_host_ports(self) -> list[int]:
        with self._lock:
            blocked = []
            for e in self._entries:
                if e.get("enabled"):
                    continue
                hp = e.get("docker_host_port")
                if hp and e.get("source") == "docker":
                    blocked.append(int(hp))
            return blocked

    def summary(self) -> dict:
        with self._lock:
            total = len(self._entries)
            active = sum(1 for e in self._entries if e.get("enabled"))
            new = sum(1 for e in self._entries if e.get("is_new"))
            return {"total": total, "active": active, "new": new}
