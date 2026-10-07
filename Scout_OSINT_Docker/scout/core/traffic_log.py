"""Log de sessões MITM."""
import threading
import time
from collections import deque


class TrafficLog:
    def __init__(self, maxlen: int = 500):
        self._lock = threading.Lock()
        self._sessions: deque = deque(maxlen=maxlen)
        self._alerts: deque = deque(maxlen=100)

    def record(
        self,
        client_ip: str,
        redirection_id: str,
        route_name: str,
        upstream: str,
        bytes_in: int,
        bytes_out: int,
        blocked: bool = False,
        mode: str = "tcp",
    ):
        with self._lock:
            self._sessions.appendleft({
                "client_ip": client_ip,
                "redirection_id": redirection_id,
                "route_name": route_name,
                "upstream": upstream,
                "bytes_in": bytes_in,
                "bytes_out": bytes_out,
                "bytes_total": bytes_in + bytes_out,
                "blocked": blocked,
                "mode": mode,
                "ts": time.time(),
                "time": time.strftime("%H:%M:%S"),
            })

    def alert(self, msg: str):
        with self._lock:
            entry = {"ts": time.time(), "time": time.strftime("%H:%M:%S"), "msg": msg}
            self._alerts.appendleft(entry)
            return entry

    def snapshot(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._sessions)[:limit]

    def snapshot_alerts(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return list(self._alerts)[:limit]

    def clear(self):
        with self._lock:
            self._sessions.clear()
