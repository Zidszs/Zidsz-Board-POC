"""Cliente WebSocket deixado pelo protocolo da API. A aba Scout não o importa."""
import json
import threading

try:
    import websocket
except ImportError:
    websocket = None


class ScoutRemoteClient:
    def __init__(self, url="ws://127.0.0.1:8765/ws", on_tick=None, on_event=None):
        self.url = url
        self.on_tick = on_tick
        self.on_event = on_event
        self._ws = None
        self._thread = None
        self.connected = False
        self._running = False

    def start(self):
        if websocket is None:
            raise RuntimeError("Instale websocket-client: pip install websocket-client")
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def send_cmd(self, cmd_dict):
        if self._ws and self.connected:
            try:
                self._ws.send(json.dumps(cmd_dict))
            except Exception:
                pass

    def toggle_redirection(self, entry_id, enabled):
        self.send_cmd({"cmd": "toggle_redirection", "id": entry_id, "enabled": enabled})

    def set_alias(self, ip, alias):
        self.send_cmd({"cmd": "set_alias", "ip": ip, "alias": alias})

    def block_ip(self, ip):
        self.send_cmd({"cmd": "block_ip", "ip": ip})

    def unblock_ip(self, ip):
        self.send_cmd({"cmd": "unblock_ip", "ip": ip})

    def remove_client(self, ip):
        self.send_cmd({"cmd": "remove_client", "ip": ip})

    def sync_docker(self):
        self.send_cmd({"cmd": "sync_docker"})

    def firewall_sync(self):
        self.send_cmd({"cmd": "firewall_sync"})

    def _run(self):
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    self.url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception:
                pass
            if self._running:
                threading.Event().wait(3)

    def _on_open(self, _ws):
        self.connected = True
        if self.on_event:
            self.on_event({"type": "connected"})

    def _on_close(self, _ws, *_):
        self.connected = False
        if self.on_event:
            self.on_event({"type": "disconnected"})

    def _on_error(self, _ws, err):
        if self.on_event:
            self.on_event({"type": "error", "msg": str(err)})

    def _on_message(self, _ws, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if data.get("type") == "tick" and self.on_tick:
            self.on_tick(data)
        elif self.on_event:
            self.on_event(data)


def check_backend_http(base="http://127.0.0.1:8765"):
    try:
        import urllib.request
        with urllib.request.urlopen(f"{base}/health", timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None
