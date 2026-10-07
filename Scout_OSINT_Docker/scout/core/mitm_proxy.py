"""Proxy MITM TCP — listeners dinâmicos por redireccionamento activo."""
import asyncio
import logging
import threading
from typing import Callable

logger = logging.getLogger("scout.mitm")


class MitmProxy:
    def __init__(
        self,
        on_session: Callable | None = None,
        is_blocked: Callable[[str], bool] | None = None,
    ):
        self._on_session = on_session
        self._is_blocked = is_blocked or (lambda _ip: False)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._servers: dict[str, asyncio.Server] = {}
        self._routes: dict[str, dict] = {}
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        for _ in range(50):
            if self._loop and self._loop.is_running():
                break
            threading.Event().wait(0.05)

    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._stop_all(), self._loop)

    def sync_routes(self, entries: list[dict]):
        if not self._loop or not self._loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._sync_routes_async(entries), self._loop)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())
        self._loop.close()

    async def _main(self):
        while self._running:
            await asyncio.sleep(0.5)

    async def _stop_all(self):
        for sid in list(self._servers.keys()):
            await self._stop_server(sid)

    async def _sync_routes_async(self, entries: list[dict]):
        active = {e["id"]: e for e in entries if e.get("enabled")}
        for sid in list(self._servers.keys()):
            if sid not in active:
                await self._stop_server(sid)
        for sid, entry in active.items():
            if sid not in self._servers:
                await self._start_server(entry)
            else:
                old = self._routes.get(sid)
                if old != entry:
                    await self._stop_server(sid)
                    await self._start_server(entry)

    async def _start_server(self, entry: dict):
        listen_port = int(entry["listen_port"])
        host = "0.0.0.0"

        async def handler(reader, writer):
            await self._handle_client(reader, writer, entry)

        try:
            server = await asyncio.start_server(handler, host, listen_port)
            self._servers[entry["id"]] = server
            self._routes[entry["id"]] = dict(entry)
            logger.info("MITM listening %s on :%s -> %s:%s", entry.get("name"), listen_port,
                        entry.get("upstream_host"), entry.get("upstream_port"))
        except OSError as exc:
            logger.error("Failed to bind :%s — %s", listen_port, exc)

    async def _stop_server(self, entry_id: str):
        server = self._servers.pop(entry_id, None)
        self._routes.pop(entry_id, None)
        if server:
            server.close()
            await server.wait_closed()

    async def _handle_client(self, reader, writer, entry: dict):
        peer = writer.get_extra_info("peername")
        client_ip = peer[0] if peer else "unknown"
        upstream = f"{entry['upstream_host']}:{entry['upstream_port']}"
        route_name = entry.get("name", entry.get("id", ""))
        mode = entry.get("mode", "tcp")

        if self._is_blocked(client_ip):
            if self._on_session:
                self._on_session(client_ip, entry["id"], route_name, upstream, 0, 0, True, mode)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return

        bytes_in = bytes_out = 0
        try:
            up_reader, up_writer = await asyncio.open_connection(
                entry["upstream_host"], int(entry["upstream_port"])
            )
        except OSError:
            writer.close()
            return

        async def pipe(src, dst, counter: list):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    counter[0] += len(data)
                    dst.write(data)
                    await dst.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        cin, cout = [0], [0]
        t1 = asyncio.create_task(pipe(reader, up_writer, cin))
        t2 = asyncio.create_task(pipe(up_reader, writer, cout))
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        for t in (t1, t2):
            t.cancel()
        bytes_in, bytes_out = cin[0], cout[0]

        if self._on_session:
            self._on_session(client_ip, entry["id"], route_name, upstream, bytes_in, bytes_out, False, mode)

        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
