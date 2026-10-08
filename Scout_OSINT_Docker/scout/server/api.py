"""API FastAPI + WebSocket — Scout Gate."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from scout.core.redirection_registry import PortaReservadaError
from scout.server.service import get_service

logger = logging.getLogger("scout.api")
_loop = None
_ws_clients: set = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    svc = get_service()
    svc.start()
    yield
    svc.stop()


app = FastAPI(title="Scout Gate", version="5.2", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ToggleBody(BaseModel):
    id: str
    enabled: bool


class ManualBody(BaseModel):
    name: str
    upstream_host: str = "host.docker.internal"
    upstream_port: int
    mode: str = "tcp"
    listen_port: int | None = None
    enabled: bool = False


class UpdateBody(BaseModel):
    listen_port: int | None = None
    upstream_host: str | None = None
    upstream_port: int | None = None
    mode: str | None = None
    name: str | None = None
    enabled: bool | None = None


class AliasBody(BaseModel):
    ip: str
    alias: str = ""


class IpBody(BaseModel):
    ip: str


class SidBody(BaseModel):
    sid: str


class AlertaBody(BaseModel):
    id: str


@app.get("/health")
def health():
    svc = get_service()
    return {"status": "ok", **svc.status()}


@app.get("/redirections")
def list_redirections():
    svc = get_service()
    return {"entries": svc.registry.list_all(), "summary": svc.registry.summary()}


def _resposta_rota(acao):
    try:
        return acao()
    except PortaReservadaError as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/redirections/toggle")
def toggle_redirection(body: ToggleBody):
    def acao():
        entry = get_service().toggle_redirection(body.id, body.enabled)
        if not entry:
            return {"ok": False, "error": "entrada não encontrada"}
        return {"ok": True, "entry": entry}

    return _resposta_rota(acao)


@app.post("/redirections/manual")
def add_manual(body: ManualBody):
    def acao():
        entry = get_service().add_manual(
            name=body.name,
            upstream_host=body.upstream_host,
            upstream_port=body.upstream_port,
            mode=body.mode,
            listen_port=body.listen_port,
            enabled=body.enabled,
        )
        return {"ok": True, "entry": entry}

    return _resposta_rota(acao)


@app.patch("/redirections/{entry_id}")
def update_redirection(entry_id: str, body: UpdateBody):
    def acao():
        fields = body.model_dump(exclude_none=True)
        entry = get_service().update_redirection(entry_id, **fields)
        if not entry:
            return {"ok": False, "error": "entrada não encontrada"}
        return {"ok": True, "entry": entry}

    return _resposta_rota(acao)


@app.delete("/redirections/{entry_id}")
def delete_redirection(entry_id: str):
    ok = get_service().delete_redirection(entry_id)
    return {"ok": ok}


@app.post("/redirections/sync")
def sync_docker():
    new = get_service().sync_docker()
    return {"ok": True, "new": new}


@app.get("/clients")
def list_clients():
    return {"clients": get_service().list_clients()}


@app.delete("/clients/{ip}")
def remove_client(ip: str):
    return get_service().remove_client(ip)


@app.get("/traffic")
def get_traffic():
    svc = get_service()
    return {"traffic": svc.traffic.snapshot(), "alerts": svc.traffic.snapshot_alerts()}


@app.get("/aliases")
def list_aliases():
    return {"aliases": get_service().aliases.list_all()}


@app.post("/aliases")
def set_alias(body: AliasBody):
    return get_service().set_alias(body.ip, body.alias)


@app.get("/blocklist")
def list_blocklist():
    return {"ips": get_service().blocklist.list_all()}


@app.post("/blocklist")
def block_ip(body: IpBody):
    return get_service().block_ip(body.ip)


@app.delete("/blocklist/{ip}")
def unblock_ip(ip: str):
    return get_service().unblock_ip(ip)


@app.post("/firewall/sync")
def firewall_sync():
    return get_service().firewall_sync()


@app.get("/varredura")
def listar_varredura():
    return {"ips": get_service().varredura.listar()}


@app.post("/varredura/desbloquear")
def desbloquear_varredura(body: IpBody):
    get_service().varredura.desbloquear(body.ip, "admin")
    return {"ok": True}


@app.get("/sessoes")
def listar_sessoes():
    return {"sessoes": get_service().trilha.sessoes()}


@app.post("/sessoes/revogar")
def revogar_sessao(body: SidBody):
    get_service().trilha.revogar(body.sid)
    return {"ok": True}


@app.get("/trilha")
def ler_trilha(ip: str = "", sid: str = ""):
    return {"linhas": get_service().trilha.legiveis(ip, sid)}


@app.get("/alertas")
def listar_alertas():
    return {"alertas": get_service().trilha.alertas()}


@app.post("/alertas/visto")
def marcar_alerta_visto(body: AlertaBody):
    get_service().trilha.marcar_visto(body.id)
    return {"ok": True}


def _broadcast(payload):
    if not _loop:
        return
    dead = []
    for ws in list(_ws_clients):
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(payload), _loop)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    svc = get_service()
    _ws_clients.add(ws)

    def on_tick(payload):
        _broadcast(payload)

    svc.subscribe(on_tick)
    try:
        await ws.send_json({"type": "connected", "status": svc.status()})
        while True:
            msg = await ws.receive_text()
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            cmd = data.get("cmd")
            if cmd == "toggle_redirection":
                entry = svc.toggle_redirection(data["id"], data.get("enabled", False))
                await ws.send_json({"type": "ack", "cmd": cmd, "entry": entry})
            elif cmd == "set_alias":
                result = svc.set_alias(data["ip"], data.get("alias", ""))
                await ws.send_json({"type": "ack", "cmd": cmd, "result": result})
            elif cmd == "block_ip":
                result = svc.block_ip(data["ip"])
                await ws.send_json({"type": "ack", "cmd": cmd, "result": result})
            elif cmd == "unblock_ip":
                result = svc.unblock_ip(data["ip"])
                await ws.send_json({"type": "ack", "cmd": cmd, "result": result})
            elif cmd == "remove_client":
                result = svc.remove_client(data["ip"])
                await ws.send_json({"type": "ack", "cmd": cmd, "result": result})
            elif cmd == "sync_docker":
                new = svc.sync_docker()
                await ws.send_json({"type": "ack", "cmd": cmd, "new": new})
            elif cmd == "firewall_sync":
                result = svc.firewall_sync()
                await ws.send_json({"type": "ack", "cmd": cmd, "result": result})
    except WebSocketDisconnect:
        pass
    finally:
        svc.unsubscribe(on_tick)
        _ws_clients.discard(ws)


def main():
    import uvicorn
    port = get_service().config.admin_port
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
