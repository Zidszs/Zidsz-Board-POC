"""Resolução IP → nome de container via Docker Engine API (Unix socket)."""
import json
import os
import socket
import threading

DEFAULT_SOCK = "/var/run/docker.sock"


class DockerResolver:
    """Cache periódico de IPs Docker, nomes e portas publicadas."""

    def __init__(self, refresh_interval: float = 15.0, sock_path: str = DEFAULT_SOCK):
        self._sock_path = sock_path
        self._refresh_interval = refresh_interval
        self._cache: dict[str, str] = {}
        self._inventory: list[dict] = []
        self._ok = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    @property
    def available(self) -> bool:
        return os.path.exists(self._sock_path)

    @property
    def ok(self) -> bool:
        with self._lock:
            return self._ok

    def start(self):
        if not self.available:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._refresh_once()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def get(self, ip: str) -> str | None:
        if not ip:
            return None
        with self._lock:
            return self._cache.get(ip)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._cache)

    def docker_ips(self) -> set[str]:
        with self._lock:
            return set(self._cache.keys())

    def inventory(self) -> list[dict]:
        with self._lock:
            return [dict(x) for x in self._inventory]

    def container_for_ip(self, ip: str, local_ip: str, local_name: str = "scout-backend") -> str:
        if ip == local_ip:
            return local_name
        return self.get(ip) or ""

    def _loop(self):
        while not self._stop.wait(self._refresh_interval):
            self._refresh_once()

    def _refresh_once(self):
        if not self.available:
            with self._lock:
                self._ok = False
            return
        try:
            data = self._docker_get("/containers/json")
            mapping: dict[str, str] = {}
            inventory: list[dict] = []
            for c in data:
                names = c.get("Names") or []
                name = names[0].lstrip("/") if names else (c.get("Id") or "")[:12]
                if not name:
                    continue
                ips = []
                nets = (c.get("NetworkSettings") or {}).get("Networks") or {}
                for net in nets.values():
                    ip = net.get("IPAddress")
                    if ip:
                        mapping[ip] = name
                        ips.append(ip)
                published = []
                seen_ports = set()
                for p in c.get("Ports") or []:
                    if not p.get("PublicPort"):
                        continue
                    host_port = p.get("PublicPort")
                    if host_port in seen_ports:
                        continue
                    seen_ports.add(host_port)
                    published.append({
                        "host": host_port,
                        "container": p.get("PrivatePort"),
                        "proto": (p.get("Type") or "tcp").upper(),
                    })
                inventory.append({
                    "name": name,
                    "ips": ips,
                    "published_ports": published,
                    "state": "running",
                })
            with self._lock:
                self._cache = mapping
                self._inventory = inventory
                self._ok = True
        except (OSError, ValueError, json.JSONDecodeError, KeyError):
            with self._lock:
                self._ok = False

    def _docker_get(self, path: str) -> list:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(5.0)
            sock.connect(self._sock_path)
            req = f"GET {path} HTTP/1.0\r\nHost: docker\r\n\r\n"
            sock.sendall(req.encode())
            chunks = []
            while True:
                buf = sock.recv(65536)
                if not buf:
                    break
                chunks.append(buf)
        finally:
            sock.close()

        body = b"".join(chunks)
        idx = body.find(b"\r\n\r\n")
        if idx == -1:
            raise ValueError("resposta Docker inválida")
        status_line = body.split(b"\r\n", 1)[0]
        if b"200" not in status_line:
            raise ValueError(f"Docker API erro: {status_line.decode(errors='replace')}")
        return json.loads(body[idx + 4 :].decode())
