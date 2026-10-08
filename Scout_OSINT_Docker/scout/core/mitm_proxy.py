"""Proxy MITM. A porta do portal lê cada pedido HTTP. WebSocket e as outras rotas copiam bytes."""
import asyncio
import logging
import os
import threading
from typing import Callable

logger = logging.getLogger("scout.mitm")


def _cookies_da_acao(acao) -> list:
    if not isinstance(acao, dict):
        return []
    linhas = []
    bruto = acao.get("set_cookie")
    if isinstance(bruto, str) and bruto and "\r" not in bruto and "\n" not in bruto:
        linhas.append(bruto)
    for extra in acao.get("cookies") or []:
        if isinstance(extra, str) and extra and "\r" not in extra and "\n" not in extra and extra not in linhas:
            linhas.append(extra)
    return linhas


def porta_do_portal() -> int:
    try:
        return int(os.environ.get("SCOUT_PUBLIC_PORT", "4050"))
    except (TypeError, ValueError):
        return 4050


def eh_portal(entry: dict) -> bool:
    try:
        return int(entry.get("listen_port")) == porta_do_portal()
    except (TypeError, ValueError):
        return False


def ler_pedido_http(bloco: bytes):
    """Devolve metodo, caminho e headers, ou None se o bloco ainda não é um pedido HTTP."""
    if b"\r\n" not in bloco:
        return None
    texto = bloco.decode("iso-8859-1", errors="replace")
    linhas = texto.split("\r\n")
    partes = linhas[0].split(" ")
    if len(partes) < 2 or not partes[0].isascii():
        return None
    metodo = partes[0].upper()
    if metodo not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT"}:
        return None
    alvo = partes[1]
    caminho = alvo.split("?", 1)[0] or "/"
    consulta = alvo.split("?", 1)[1] if "?" in alvo else ""
    headers = {}
    for linha in linhas[1:]:
        if ":" not in linha:
            continue
        chave, valor = linha.split(":", 1)
        headers[chave.strip().lower()] = valor.strip()
    return {"metodo": metodo, "caminho": caminho, "query": consulta, "headers": headers}


async def ler_cabecalhos(reader, limite: int = 65536):
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        pedaco = await reader.read(4096)
        if not pedaco:
            break
        buf.extend(pedaco)
        if len(buf) > limite:
            break
    marca = buf.find(b"\r\n\r\n")
    if marca < 0:
        return bytes(buf), b"", None, False
    cabeca = bytes(buf[:marca])
    resto = bytes(buf[marca + 4 :])
    return cabeca, resto, ler_pedido_http(cabeca), True


def _eh_websocket(pedido) -> bool:
    headers = (pedido or {}).get("headers") or {}
    return "websocket" in str(headers.get("upgrade") or "").lower()


def _modo_corpo_pedido(pedido) -> tuple[str, int]:
    """len = um corpo com tamanho. outro = o resto da conexão, sem outro pedido."""
    headers = (pedido or {}).get("headers") or {}
    if "chunked" in str(headers.get("transfer-encoding") or "").lower():
        return "outro", 0
    bruto = headers.get("content-length")
    if bruto is not None and str(bruto).strip().isdigit():
        return "len", int(str(bruto).strip())
    metodo = str((pedido or {}).get("metodo") or "GET").upper()
    if metodo in {"GET", "HEAD", "OPTIONS", "DELETE", "TRACE"}:
        return "len", 0
    return "outro", 0


def _destino_da_acao(entry: dict, acao) -> dict:
    if isinstance(acao, dict) and acao.get("host"):
        destino = dict(entry)
        destino["upstream_host"] = acao["host"]
        destino["upstream_port"] = acao["port"]
        return destino
    return entry


def _status_e_headers(bloco: bytes) -> tuple[int, dict]:
    texto = bloco.decode("iso-8859-1", errors="replace")
    linhas = texto.split("\r\n")
    status = 200
    if linhas and linhas[0].startswith("HTTP/"):
        partes = linhas[0].split(" ")
        if len(partes) > 1 and partes[1].isdigit():
            status = int(partes[1])
    headers = {}
    for linha in linhas[1:]:
        if ":" not in linha:
            continue
        chave, valor = linha.split(":", 1)
        headers[chave.strip().lower()] = valor.strip()
    return status, headers


def _modo_corpo_resposta(status: int, headers: dict, metodo: str) -> tuple[str, int]:
    if metodo == "HEAD" or status in (204, 304) or 100 <= status < 200:
        return "vazio", 0
    if "chunked" in str(headers.get("transfer-encoding") or "").lower():
        return "chunked", 0
    bruto = headers.get("content-length")
    if bruto is not None and str(bruto).strip().isdigit():
        return "len", int(str(bruto).strip())
    return "eof", 0


def _pedido_para_upstream(cabeca: bytes, extra: str | None) -> bytes:
    """Uma conexão nova por pedido. Connection: close faz o upstream terminar a resposta."""
    texto = cabeca.decode("iso-8859-1", errors="replace")
    linhas = texto.split("\r\n")
    saida = [linhas[0]]
    for linha in linhas[1:]:
        if linha.lower().startswith("connection:"):
            continue
        saida.append(linha)
    if extra and "\r" not in extra and "\n" not in extra:
        saida.append(extra)
    saida.append("Connection: close")
    return "\r\n".join(saida).encode("iso-8859-1")


class MitmProxy:
    def __init__(
        self,
        on_session: Callable | None = None,
        is_blocked: Callable[[str], bool] | None = None,
    ):
        self._on_session = on_session
        self._is_blocked = is_blocked or (lambda _ip: False)
        self.on_pedido = None
        self.antes_de_ligar = None
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

        if eh_portal(entry):
            await self._handle_portal(reader, writer, entry, client_ip, route_name, upstream, mode)
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

    async def _handle_portal(self, reader, writer, entry, client_ip, route_name, upstream, mode):
        try:
            cabeca, resto, pedido, completo = await asyncio.wait_for(ler_cabecalhos(reader), timeout=5)
        except asyncio.TimeoutError:
            writer.close()
            return
        if not self.antes_de_ligar:
            if pedido and self.on_pedido:
                self.on_pedido(pedido, client_ip, entry)
            await self._repassar(
                reader, writer, entry, cabeca, resto, completo, {},
                client_ip, route_name, upstream, mode,
            )
            return
        if not pedido or not completo:
            writer.close()
            return
        try:
            while pedido:
                if self.on_pedido:
                    self.on_pedido(pedido, client_ip, entry)
                acao = self.antes_de_ligar(pedido, client_ip, entry) or {}
                if not acao.get("liga"):
                    await self._escrever_resposta(writer, acao or {"status": 403, "corpo": b"Acesso negado."})
                    return
                destino = _destino_da_acao(entry, acao)
                modo_corpo, _tamanho = _modo_corpo_pedido(pedido)
                # WebSocket e corpo sem tamanho continuam um fluxo só: a revogação
                # corta o quadro seguinte. Um GET keep-alive não pode ir por aí,
                # senão o iframe /painel/sessao cai no Streamlit e o cookie não nasce.
                if _eh_websocket(pedido) or modo_corpo != "len":
                    await self._repassar(
                        reader, writer, destino, cabeca, resto, True, acao,
                        client_ip, route_name, upstream, mode,
                    )
                    return
                cabeca, resto, pedido, _completo = await self._troca_http(
                    reader, writer, destino, cabeca, resto, pedido, acao,
                    client_ip, entry, route_name, upstream, mode,
                )
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _repassar(self, reader, writer, destino, cabeca, resto, completo, acao, client_ip, route_name, upstream, mode):
        try:
            up_reader, up_writer = await asyncio.open_connection(
                destino["upstream_host"], int(destino["upstream_port"])
            )
        except OSError:
            writer.close()
            return
        if completo:
            prefixo = cabeca + b"\r\n\r\n" + resto
        else:
            prefixo = cabeca
        extra_pedido = acao.get("injetar_pedido") if isinstance(acao, dict) else None
        if extra_pedido and completo and isinstance(extra_pedido, str) and "\r" not in extra_pedido and "\n" not in extra_pedido:
            prefixo = cabeca + b"\r\n" + extra_pedido.encode("ascii") + b"\r\n\r\n" + resto
        bytes_in = len(prefixo)
        if prefixo:
            up_writer.write(prefixo)
            await up_writer.drain()
        ainda = acao.get("ainda_vale") if isinstance(acao, dict) else None
        cookies = _cookies_da_acao(acao)

        async def pipe(src, dst, counter: list, injetar: bool):
            primeiro = injetar
            pendente = b""
            try:
                while True:
                    if ainda and not ainda():
                        break
                    data = await src.read(65536)
                    if not data:
                        break
                    if primeiro:
                        pendente += data
                        marca = pendente.find(b"\r\n\r\n")
                        if marca < 0 and len(pendente) < 65536:
                            continue
                        if marca >= 0 and cookies:
                            cabeca_up = pendente[:marca]
                            resto_up = pendente[marca + 4 :]
                            bloco = b"".join(b"Set-Cookie: " + item.encode("ascii") + b"\r\n" for item in cookies)
                            pendente = cabeca_up + b"\r\n" + bloco + b"\r\n" + resto_up
                        primeiro = False
                        data = pendente
                        pendente = b""
                    counter[0] += len(data)
                    dst.write(data)
                    await dst.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError, UnicodeError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        cin, cout = [0], [0]
        t1 = asyncio.create_task(pipe(reader, up_writer, cin, False))
        t2 = asyncio.create_task(pipe(up_reader, writer, cout, bool(cookies)))
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        for t in (t1, t2):
            t.cancel()
        if self._on_session:
            self._on_session(
                client_ip, destino.get("id"), route_name, upstream,
                bytes_in + cin[0], cout[0], False, mode,
            )
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    async def _troca_http(
        self, reader, writer, destino, cabeca, resto, pedido, acao,
        client_ip, entry, route_name, upstream, mode,
    ):
        """Um pedido e uma resposta. O que sobrar no cliente é o pedido seguinte."""
        try:
            up_reader, up_writer = await asyncio.open_connection(
                destino["upstream_host"], int(destino["upstream_port"])
            )
        except OSError:
            writer.close()
            return b"", b"", None, False
        extra = acao.get("injetar_pedido") if isinstance(acao, dict) else None
        if not isinstance(extra, str):
            extra = None
        prefixo = _pedido_para_upstream(cabeca, extra) + b"\r\n\r\n"
        _modo, tamanho = _modo_corpo_pedido(pedido)
        ainda = acao.get("ainda_vale") if isinstance(acao, dict) else None
        tarefa = asyncio.create_task(ler_cabecalhos(up_reader))
        try:
            up_writer.write(prefixo)
            await up_writer.drain()
            ok, sobra = await self._mandar_corpo(reader, up_writer, resto, tamanho, ainda)
            if not ok:
                return b"", b"", None, False
            try:
                cabeca_up, resto_up, _pedido_up, completo = await asyncio.wait_for(tarefa, timeout=30)
            except asyncio.TimeoutError:
                writer.close()
                return b"", b"", None, False
            tarefa = None
            if not completo:
                writer.close()
                return b"", b"", None, False
            status, headers_up = _status_e_headers(cabeca_up)
            modo_resp, tamanho_resp = _modo_corpo_resposta(status, headers_up, pedido.get("metodo") or "GET")
            cookies = _cookies_da_acao(acao)
            if cookies:
                # cabeca_up não traz o CRLF final. A linha do cookie já termina
                # em CRLF. Mais um CRLF fecha o cabeçalho. Dois a mais empurram
                # o corpo chunked e o navegador vê a resposta como corte.
                linhas_cookie = b"".join(
                    b"Set-Cookie: " + item.encode("ascii") + b"\r\n" for item in cookies
                )
                bloco = cabeca_up + b"\r\n" + linhas_cookie + b"\r\n"
            else:
                bloco = cabeca_up + b"\r\n\r\n"
            writer.write(bloco)
            await writer.drain()
            await self._mandar_resposta(up_reader, writer, resto_up, modo_resp, tamanho_resp)
            if self._on_session:
                self._on_session(
                    client_ip, entry.get("id"), route_name, upstream,
                    len(prefixo) + tamanho, tamanho_resp, False, mode,
                )
        finally:
            if tarefa is not None and not tarefa.done():
                tarefa.cancel()
                try:
                    await tarefa
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                up_writer.close()
            except Exception:
                pass
        return await self._proximo_pedido(reader, sobra)

    async def _mandar_corpo(self, reader, up_writer, resto: bytes, tamanho: int, ainda):
        ja = resto[:tamanho]
        sobra = resto[tamanho:]
        if ja:
            if ainda and not ainda():
                return False, b""
            up_writer.write(ja)
            await up_writer.drain()
        faltam = tamanho - len(ja)
        while faltam > 0:
            if ainda and not ainda():
                return False, b""
            pedaco = await reader.read(min(65536, faltam))
            if not pedaco:
                return False, b""
            if ainda and not ainda():
                return False, b""
            up_writer.write(pedaco)
            await up_writer.drain()
            faltam -= len(pedaco)
        return True, sobra

    async def _mandar_resposta(self, up_reader, writer, resto: bytes, modo: str, tamanho: int):
        if modo == "vazio":
            return
        if modo == "len":
            falta = tamanho
            if resto:
                pedaco = resto[:falta]
                if pedaco:
                    writer.write(pedaco)
                    await writer.drain()
                resto = resto[len(pedaco):]
                falta -= len(pedaco)
            while falta > 0:
                data = await up_reader.read(min(65536, falta))
                if not data:
                    break
                writer.write(data)
                await writer.drain()
                falta -= len(data)
            return
        if modo == "chunked":
            await self._mandar_chunked(up_reader, writer, resto)
            return
        if resto:
            writer.write(resto)
            await writer.drain()
        while True:
            data = await up_reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()

    async def _mandar_chunked(self, up_reader, writer, resto: bytes):
        buf = bytearray(resto)
        while True:
            while b"\r\n" not in buf:
                data = await up_reader.read(4096)
                if not data:
                    if buf:
                        writer.write(buf)
                        await writer.drain()
                    return
                buf.extend(data)
            marca = buf.find(b"\r\n")
            linha = bytes(buf[:marca])
            try:
                tamanho = int(linha.split(b";", 1)[0], 16)
            except ValueError:
                writer.write(buf)
                await writer.drain()
                return
            if tamanho == 0:
                while buf.find(b"\r\n\r\n") < 0:
                    data = await up_reader.read(4096)
                    if not data:
                        break
                    buf.extend(data)
                fim = buf.find(b"\r\n\r\n")
                if fim < 0:
                    writer.write(buf)
                else:
                    writer.write(buf[: fim + 4])
                await writer.drain()
                return
            preciso = marca + 2 + tamanho + 2
            while len(buf) < preciso:
                data = await up_reader.read(4096)
                if not data:
                    writer.write(buf)
                    await writer.drain()
                    return
                buf.extend(data)
            writer.write(buf[:preciso])
            await writer.drain()
            del buf[:preciso]

    async def _proximo_pedido(self, reader, sobra: bytes):
        """O próximo pedido na mesma conexão. Sem bytes novos, solta o keep-alive.

        O navegador abre o iframe depois de ler o HTML. Se essa pausa passar
        do intervalo, ele abre outra conexão — e essa também é lida do zero.
        """
        buf = bytearray(sobra)
        while b"\r\n\r\n" not in buf:
            try:
                if buf:
                    pedaco = await reader.read(4096)
                else:
                    pedaco = await asyncio.wait_for(reader.read(4096), timeout=0.25)
            except asyncio.TimeoutError:
                return b"", b"", None, False
            if not pedaco:
                break
            buf.extend(pedaco)
            if len(buf) > 65536 and b"\r\n\r\n" not in buf:
                break
        marca = buf.find(b"\r\n\r\n")
        if marca < 0:
            return b"", b"", None, False
        cabeca = bytes(buf[:marca])
        resto = bytes(buf[marca + 4 :])
        pedido = ler_pedido_http(cabeca)
        if not pedido:
            return b"", b"", None, False
        return cabeca, resto, pedido, True

    async def _escrever_resposta(self, writer, acao: dict):
        status = int(acao.get("status") or 403)
        frase = {
            202: "Accepted",
            303: "See Other",
            401: "Unauthorized",
            403: "Forbidden",
            503: "Service Unavailable",
        }.get(status, "OK")
        corpo = acao.get("corpo") or b""
        if isinstance(corpo, str):
            corpo = corpo.encode("utf-8")
        tipo = acao.get("tipo") or "text/plain; charset=utf-8"
        linhas = [
            f"HTTP/1.1 {status} {frase}",
            f"Content-Type: {tipo}",
            f"Content-Length: {len(corpo)}",
        ]
        for extra in acao.get("headers") or []:
            if isinstance(extra, str) and extra and "\r" not in extra and "\n" not in extra:
                linhas.append(extra)
        linhas.append("Connection: close")
        linhas.append("")
        linhas.append("")
        cabeca = "\r\n".join(linhas).encode("ascii")
        try:
            writer.write(cabeca + corpo)
            await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass
