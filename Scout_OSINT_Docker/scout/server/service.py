"""Motor Scout Gate — MITM, redireccionamentos, firewall, tráfego."""
import logging
import threading
import time

from scout.core.blocklist import BlocklistStore
from scout.core.client_registry import ClientRegistry
from scout.core.docker_resolver import DockerResolver
from scout.core.firewall_linux import FirewallLinux
from scout.core.ip_aliases import IpAliasStore
from scout.core.mitm_proxy import MitmProxy
from scout.core.redirection_registry import RedirectionRegistry
from scout.core.route_config import RouteConfig
from scout.core.traffic_log import TrafficLog

logger = logging.getLogger("scout.service")


class ScoutGateService:
    def __init__(self):
        self.config = RouteConfig()
        self.registry = RedirectionRegistry(self.config)
        self.docker = DockerResolver(refresh_interval=15.0)
        self.aliases = IpAliasStore()
        self.clients = ClientRegistry()
        self.blocklist = BlocklistStore()
        self.traffic = TrafficLog()
        self.firewall = FirewallLinux(enabled=self.config.enforce_firewall)
        self.proxy = MitmProxy(
            on_session=self._on_session,
            is_blocked=self._is_blocked,
        )
        self._subscribers: list = []
        self._sub_lock = threading.Lock()
        self._tick_thread = None
        self._sync_thread = None
        self._running = False

    def start(self):
        if self._running:
            return {"ok": True, "message": "já activo"}
        self._running = True
        self.docker.start()
        self.proxy.start()
        self._sync_all()
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._tick_thread.start()
        self._sync_thread.start()
        return {"ok": True}

    def stop(self):
        self._running = False
        self.proxy.stop()
        return {"ok": True}

    def subscribe(self, cb):
        with self._sub_lock:
            self._subscribers.append(cb)

    def unsubscribe(self, cb):
        with self._sub_lock:
            if cb in self._subscribers:
                self._subscribers.remove(cb)

    def _notify(self, payload):
        with self._sub_lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(payload)
            except Exception:
                pass

    def _is_blocked(self, client_ip: str) -> bool:
        if self.aliases.is_trusted(client_ip):
            return False
        return self.blocklist.is_blocked(client_ip)

    def _on_session(self, client_ip, rid, route_name, upstream, bin_, bout, blocked, mode):
        self.traffic.record(client_ip, rid, route_name, upstream, bin_, bout, blocked, mode)
        self.clients.touch(
            client_ip, route_name, upstream, mode, bin_, bout, blocked
        )
        if self.aliases.is_trusted(client_ip):
            return
        if blocked:
            self.traffic.alert(f"Bloqueado: {client_ip} → {route_name}")
        elif bin_ + bout > 50000:
            self.traffic.alert(f"Tráfego elevado: {client_ip} → {route_name}")

    def _sync_all(self):
        inventory = self.docker.inventory()
        new = self.registry.sync_docker(inventory)
        for _ in new:
            self.traffic.alert(f"Novo container/porta detectado — toggle OFF por defeito")
        active = self.registry.get_active()
        self.proxy.sync_routes(active)
        if self.config.enforce_firewall:
            self.firewall.sync(
                self.registry.allowed_host_ports(),
                self.registry.blocked_host_ports(),
            )

    def _sync_loop(self):
        while self._running:
            time.sleep(15)
            try:
                self._sync_all()
            except Exception:
                logger.exception("sync loop error")

    def _tick_loop(self):
        while self._running:
            time.sleep(1.0)
            payload = self._build_tick()
            self._notify(payload)

    def _build_tick(self) -> dict:
        entries = self.registry.list_all()
        for e in entries:
            e["alias_hint"] = ""
        traffic = self.traffic.snapshot(80)
        for t in traffic:
            t["client_label"] = self.aliases.display(t["client_ip"])
        clients = self.list_clients()
        return {
            "type": "tick",
            "stats": self.status(),
            "redirections": entries,
            "traffic": traffic,
            "clients": clients,
            "alerts": self.traffic.snapshot_alerts(15),
            "aliases": self.aliases.list_all(),
            "blocklist": self.blocklist.list_all(),
        }

    def list_clients(self) -> list[dict]:
        blocked = set(self.blocklist.list_all())
        rows = []
        for c in self.clients.list_all():
            ip = c.get("ip", "")
            alias = self.aliases.get(ip)
            rows.append({
                **c,
                "alias": alias,
                "client_label": self.aliases.display(ip),
                "trusted": self.aliases.is_trusted(ip),
                "blocked": ip in blocked,
            })
        return rows

    def status(self) -> dict:
        inv = self.docker.inventory()
        return {
            "running": self._running,
            "ngrok_url": self.config.ngrok_url,
            "public_port": self.config.public_port,
            "admin_port": self.config.admin_port,
            "enforce_firewall": self.config.enforce_firewall,
            "docker_ok": self.docker.ok,
            "containers": len(inv),
            "summary": self.registry.summary(),
        }

    def toggle_redirection(self, entry_id: str, enabled: bool) -> dict | None:
        entry = self.registry.toggle(entry_id, enabled)
        if entry:
            self._sync_all()
        return entry

    def update_redirection(self, entry_id: str, **fields) -> dict | None:
        entry = self.registry.update(entry_id, **fields)
        if entry:
            self._sync_all()
        return entry

    def add_manual(self, **kwargs) -> dict:
        entry = self.registry.add_manual(**kwargs)
        self._sync_all()
        return entry

    def delete_redirection(self, entry_id: str) -> bool:
        ok = self.registry.delete(entry_id)
        if ok:
            self._sync_all()
        return ok

    def sync_docker(self) -> list[dict]:
        self.docker._refresh_once()
        new = self.registry.sync_docker(self.docker.inventory())
        self._sync_all()
        return new

    def set_alias(self, ip: str, alias: str) -> dict:
        result = self.aliases.set(ip, alias)
        if alias and alias.strip():
            self.blocklist.remove(ip)
        return result

    def block_ip(self, ip: str) -> dict:
        return self.blocklist.add(ip)

    def unblock_ip(self, ip: str) -> dict:
        return self.blocklist.remove(ip)

    def remove_client(self, ip: str) -> dict:
        ok = self.clients.remove(ip)
        return {"ip": ip, "removed": ok}

    def firewall_sync(self) -> dict:
        return self.firewall.sync(
            self.registry.allowed_host_ports(),
            self.registry.blocked_host_ports(),
        )


_service: ScoutGateService | None = None


def get_service() -> ScoutGateService:
    global _service
    if _service is None:
        _service = ScoutGateService()
    return _service
