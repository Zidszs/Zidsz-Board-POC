"""Firewall Linux — DROP portas Docker com redireccionamento OFF."""
import logging
import shutil
import subprocess

logger = logging.getLogger("scout.firewall")

CHAIN = "DOCKER-USER"
COMMENT = "scout-gate"


class FirewallLinux:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled and shutil.which("iptables") is not None

    def sync(self, allowed_ports: list[int], blocked_ports: list[int]) -> dict:
        if not self.enabled:
            return {"ok": False, "reason": "iptables indisponível ou desactivado"}
        applied = {"allowed": [], "blocked": [], "errors": []}
        try:
            self._ensure_chain()
            self._flush_scout_rules()
            for port in sorted(set(allowed_ports)):
                if self._insert_allow(port):
                    applied["allowed"].append(port)
            for port in sorted(set(blocked_ports)):
                if port in allowed_ports:
                    continue
                if self._insert_drop(port):
                    applied["blocked"].append(port)
            return {"ok": True, **applied}
        except Exception as exc:
            logger.exception("firewall sync failed")
            applied["errors"].append(str(exc))
            return {"ok": False, **applied}

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["iptables", *args],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _ensure_chain(self):
        r = self._run(["-L", CHAIN, "-n"])
        if r.returncode != 0:
            self._run(["-N", CHAIN])

    def _flush_scout_rules(self):
        for _ in range(50):
            r = self._run(["-L", CHAIN, "-n", "--line-numbers"])
            if r.returncode != 0:
                break
            lines = r.stdout.splitlines()
            deleted = False
            for line in lines:
                if COMMENT in line:
                    num = line.split()[0]
                    if num.isdigit():
                        self._run(["-D", CHAIN, num])
                        deleted = True
                        break
            if not deleted:
                break

    def _insert_allow(self, port: int) -> bool:
        r = self._run([
            "-I", CHAIN, "1",
            "!", "-s", "127.0.0.0/8",
            "!", "-s", "10.0.0.0/8",
            "!", "-s", "172.16.0.0/12",
            "!", "-s", "192.168.0.0/16",
            "-p", "tcp", "--dport", str(port),
            "-m", "comment", "--comment", f"{COMMENT}-allow-{port}",
            "-j", "ACCEPT",
        ])
        return r.returncode == 0

    def _insert_drop(self, port: int) -> bool:
        r = self._run([
            "-I", CHAIN, "1",
            "!", "-s", "127.0.0.0/8",
            "!", "-s", "10.0.0.0/8",
            "!", "-s", "172.16.0.0/12",
            "!", "-s", "192.168.0.0/16",
            "-p", "tcp", "--dport", str(port),
            "-m", "comment", "--comment", f"{COMMENT}-drop-{port}",
            "-j", "DROP",
        ])
        return r.returncode == 0
