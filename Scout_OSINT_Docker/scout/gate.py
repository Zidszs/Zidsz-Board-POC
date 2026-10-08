"""Cliente da gestão do Scout, sem janela.

A GUI Tkinter chamava o backend na porta 8765: parte por WebSocket
(`/ws`) e parte por HTTP. Este módulo fala só HTTP, nos mesmos caminhos
que a API já expõe para aqueles comandos.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

MODES = ("tcp", "http", "https")
PORTAS_PORTEIRO = frozenset({5676, 5677})
PORTA_HUD_ADMIN = 8501
DEFAULT_HTTP = "http://127.0.0.1:8765"
_ENTRY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
_IP = re.compile(r"^[A-Za-z0-9:.]{2,64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,60}$")

# Controle da GUI antiga -> equivalente no painel. `ported` falso fica explicado.
GUI_PARITY = (
    {
        "gui": "Cabeçalho Backend ONLINE/OFFLINE e resumo Activos/Total/Novos",
        "painel": "Estado e resumo no topo da aba Scout",
        "api": "GET /health",
        "ported": True,
    },
    {
        "gui": "Sync Docker",
        "painel": "Sincronizar Docker",
        "api": "POST /redirections/sync",
        "ported": True,
    },
    {
        "gui": "+ Porta manual (nome e porta upstream; host host.docker.internal; começa desligada)",
        "painel": "Incluir porta manual",
        "api": "POST /redirections/manual",
        "ported": True,
    },
    {
        "gui": "Sync Firewall",
        "painel": "Sincronizar firewall",
        "api": "POST /firewall/sync",
        "ported": True,
    },
    {
        "gui": "Tabela de rotas: modo tcp/http/https, nome, origem, porta Scout, upstream, ativo, NOVO/ACTIVO/INACTIVO",
        "painel": "Cartão de cada rota com os mesmos campos",
        "api": "GET /redirections",
        "ported": True,
    },
    {
        "gui": "Combobox de modo quando a rota docker ou manual está ativa",
        "painel": "Gravar modo",
        "api": "PATCH /redirections/{id} {mode}",
        "ported": True,
    },
    {
        "gui": "Checkbox Activo (toggle)",
        "painel": "Ativar ou Desativar, com confirmação",
        "api": "POST /redirections/toggle",
        "ported": True,
    },
    {
        "gui": "Aviso antes de ativar n8n_app (pula o Porteiro)",
        "painel": "O mesmo texto na confirmação; sem Confirmar a API não é chamada",
        "api": "POST /redirections/toggle",
        "ported": True,
    },
    {
        "gui": "Recusa ativar ngrok_service",
        "painel": "Recusa igual, sem chamar a API",
        "api": "",
        "ported": True,
    },
    {
        "gui": "Edição de rota manual: nome, porta Scout, upstream host:porta",
        "painel": "Salvar rota manual",
        "api": "PATCH /redirections/{id}",
        "ported": True,
    },
    {
        "gui": "Aviso e troca de 127.0.0.1:5677/5678 por host.docker.internal",
        "painel": "A mesma troca antes de gravar",
        "api": "PATCH /redirections/{id}",
        "ported": True,
    },
    {
        "gui": "Menu Apagar em rota manual",
        "painel": "Apagar rota manual",
        "api": "DELETE /redirections/{id}",
        "ported": True,
    },
    {
        "gui": "Aba Tráfego (hora, cliente, rota, modo, upstream, bytes, bloqueado) e alertas",
        "painel": "Tabelas de tráfego e de alertas",
        "api": "GET /traffic",
        "ported": True,
    },
    {
        "gui": "Menu do tráfego: Alias e Bloquear IP",
        "painel": "Alias e Bloquear na linha",
        "api": "POST /aliases e POST /blocklist",
        "ported": True,
    },
    {
        "gui": "Aba Clientes: IP, alias, última interação, modo, rota, sessões, bytes, bloqueado",
        "painel": "Tabela de clientes",
        "api": "GET /clients",
        "ported": True,
    },
    {
        "gui": "Menu do cliente: Alias, Bloquear IP, Remover da lista",
        "painel": "Os três botões na linha",
        "api": "POST /aliases, POST /blocklist, DELETE /clients/{ip}",
        "ported": True,
    },
    {
        "gui": "O menu da janela não tinha Desbloquear; a API já aceitava tirar o IP da blocklist",
        "painel": "Desbloquear na linha, quando o IP já está bloqueado",
        "api": "DELETE /blocklist/{ip}",
        "ported": True,
    },
    {
        "gui": "Aba Config: URL do backend, Religar, Health Check, texto de ngrok/porta/firewall/docker",
        "painel": "Endereço HTTP, Health Check e o mesmo texto de estado",
        "api": "GET /health",
        "ported": True,
    },
    {
        "gui": "Log curto no rodapé",
        "painel": "Registro das ações desta sessão do navegador",
        "api": "",
        "ported": True,
    },
    {
        "gui": "Tick WebSocket a cada segundo",
        "painel": "Atualizar Scout lê os mesmos dados por HTTP. Não há socket contínuo.",
        "api": "GET /health, /redirections, /traffic, /clients",
        "ported": False,
        "motivo": "O Streamlit não segura o WebSocket da GUI. Os dados são os do tick, sob demanda.",
    },
    {
        "gui": "Bipe do Windows quando chega alerta",
        "painel": "Não há som. O alerta aparece na lista.",
        "api": "GET /traffic",
        "ported": False,
        "motivo": "winsound era da janela Tk. O navegador não recebe esse bip.",
    },
    {
        "gui": "Fechar a janela e perguntar se encerra ngrok, n8n, Scout e Porteiro",
        "painel": "Não há janela para fechar. Parar infraestrutura no painel pede confirmação.",
        "api": "",
        "ported": False,
        "motivo": "A janela Tk foi removida neste ramo. O desligamento da stack continua no botão já existente.",
    },
)


class GateError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def offline_message(base: str) -> str:
    return (
        f"O Scout não respondeu em {base}. "
        "Confira se o container scout-backend está no ar. "
        "A borda pública é a porta 4050; a gestão responde na 8765."
    )


def api_base(url: str | None) -> str:
    raw = (url or "").strip()
    if not raw:
        return DEFAULT_HTTP
    if raw.startswith("ws://"):
        raw = "http://" + raw[len("ws://") :]
    elif raw.startswith("wss://"):
        raw = "https://" + raw[len("wss://") :]
    raw = raw.rstrip("/")
    if raw.endswith("/ws"):
        raw = raw[: -len("/ws")]
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(char in raw for char in "\n\r "):
        return DEFAULT_HTTP
    return raw


def parse_upstream(text: str) -> tuple[str, int]:
    raw = (text or "").strip()
    if not raw:
        raise GateError("Upstream vazio. Use host:porta.")
    if ":" not in raw:
        raise GateError("Upstream precisa ser host:porta.")
    host, _, port_s = raw.rpartition(":")
    host = host.strip()
    if not host or any(char in host for char in " /\n\r"):
        raise GateError("Host de upstream inválido.")
    try:
        port = int(port_s.strip())
    except ValueError as exc:
        raise GateError("Porta de upstream inválida.") from exc
    if port < 1 or port > 65535:
        raise GateError("Porta de upstream inválida. Use 1–65535.")
    return host, port


def coerce_managed_upstream(host: str, port: int, *, managed: bool) -> tuple[str, int, str | None]:
    if managed and host in {"127.0.0.1", "localhost"} and port in {5677, 5678}:
        return (
            "host.docker.internal",
            port,
            "Dentro do container Scout, 127.0.0.1 não alcança o Porteiro nem o n8n no host. "
            f"A gravação usa host.docker.internal:{port}.",
        )
    return host, port, None


def toggle_decision(entry_id: str, enabled: bool, *, managed: bool) -> str:
    """allow, confirm_n8n ou reject_ngrok. Igual aos avisos da GUI."""
    if enabled and managed:
        if "n8n_app" in entry_id:
            return "confirm_n8n"
        if entry_id != "porteiro-manual" and "ngrok_service" in entry_id:
            return "reject_ngrok"
    return "allow"


def _entry_id(entry_id: str) -> str:
    if not isinstance(entry_id, str) or not _ENTRY_ID.fullmatch(entry_id):
        raise GateError("Rota recusada.")
    return entry_id


def _ip(ip: str) -> str:
    if not isinstance(ip, str) or not _IP.fullmatch(ip.strip()) or ".." in ip:
        raise GateError("IP recusado.")
    return ip.strip()


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _recusar_porta_porteiro(porta: int) -> None:
    if porta == PORTA_HUD_ADMIN:
        raise GateError(
            "A porta 8501 é o HUD-admin e não pode ser escuta nem destino de uma rota do Scout."
        )
    if porta in PORTAS_PORTEIRO:
        raise GateError(
            f"A porta {porta} é do Porteiro e não pode ser escuta nem destino de uma rota do Scout."
        )


def _motivo_http(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if isinstance(data, dict) and data.get("ok") is False:
        return refusal_text(data)
    return ""


def refusal_text(data: dict) -> str:
    """Quando ok é falso, o motivo pode vir em reason, error ou detail."""
    if isinstance(data, dict):
        for key in ("reason", "error", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return f"O Scout recusou a operação: {value.strip()}"
    return "O Scout recusou a operação."


class ScoutGate:
    def __init__(self, base: str, *, transport: Callable | None = None, timeout: float = 5):
        self.base = api_base(base).rstrip("/")
        self.transport = transport
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None):
        url = self.base + path
        if self.transport is not None:
            try:
                data = self.transport(method, url, payload, self.timeout)
            except GateError:
                raise
            except Exception as exc:
                raise GateError(offline_message(self.base)) from exc
            if isinstance(data, dict) and data.get("ok") is False:
                raise GateError(refusal_text(data))
            return data
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "N8GrokerControlPlane/1.0"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # HTTPError é URLError. Sem este ramo, o corpo com ok=false vira "não respondeu".
            raw = exc.read()
            texto = _motivo_http(raw)
            if texto:
                raise GateError(texto) from exc
            raise GateError(offline_message(self.base)) from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise GateError(offline_message(self.base)) from exc
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise GateError("O Scout respondeu com um texto que não é JSON.") from exc
        if isinstance(data, dict) and data.get("ok") is False:
            raise GateError(refusal_text(data))
        return data

    def health(self) -> dict:
        data = self._request("GET", "/health")
        return data if isinstance(data, dict) else {}

    def redirections(self) -> dict:
        data = self._request("GET", "/redirections")
        return data if isinstance(data, dict) else {}

    def traffic(self) -> dict:
        data = self._request("GET", "/traffic")
        return data if isinstance(data, dict) else {}

    def clients(self) -> dict:
        data = self._request("GET", "/clients")
        return data if isinstance(data, dict) else {}

    def snapshot(self) -> dict:
        health = self.health()
        routes = self.redirections()
        traffic = self.traffic()
        clients = self.clients()
        return {
            "health": health,
            "entries": list(routes.get("entries") or []),
            "summary": routes.get("summary") or health.get("summary") or {},
            "traffic": list(traffic.get("traffic") or []),
            "alerts": list(traffic.get("alerts") or []),
            "clients": list(clients.get("clients") or []),
        }

    def toggle(self, entry_id: str, enabled: bool) -> dict:
        return self._request("POST", "/redirections/toggle", {"id": _entry_id(entry_id), "enabled": bool(enabled)})

    def patch(self, entry_id: str, fields: dict) -> dict:
        allowed = {"listen_port", "upstream_host", "upstream_port", "mode", "name", "enabled"}
        payload = {key: value for key, value in fields.items() if key in allowed}
        if not payload:
            raise GateError("Nada para gravar nessa rota.")
        if "mode" in payload and payload["mode"] not in MODES:
            raise GateError("Modo recusado. Use tcp, http ou https.")
        if "name" in payload and not _NAME.fullmatch(str(payload["name"]).strip()):
            raise GateError("Nome de rota recusado.")
        if "listen_port" in payload:
            port = payload["listen_port"]
            if isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535:
                raise GateError("Porta Scout inválida. Use 1–65535.")
            _recusar_porta_porteiro(port)
        if "upstream_port" in payload:
            port = payload["upstream_port"]
            if isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535:
                raise GateError("Porta upstream inválida. Use 1–65535.")
            _recusar_porta_porteiro(port)
        return self._request("PATCH", "/redirections/" + _quote(_entry_id(entry_id)), payload)

    def delete(self, entry_id: str) -> dict:
        return self._request("DELETE", "/redirections/" + _quote(_entry_id(entry_id)))

    def add_manual(
        self,
        name: str,
        upstream_port: int,
        *,
        upstream_host: str = "host.docker.internal",
        enabled: bool = False,
        mode: str = "tcp",
    ) -> dict:
        if not _NAME.fullmatch(name.strip()):
            raise GateError("Nome de rota recusado.")
        if isinstance(upstream_port, bool) or not isinstance(upstream_port, int) or upstream_port < 1 or upstream_port > 65535:
            raise GateError("Porta upstream inválida. Use 1–65535.")
        _recusar_porta_porteiro(upstream_port)
        if mode not in MODES:
            raise GateError("Modo recusado. Use tcp, http ou https.")
        host = upstream_host.strip()
        if not host or any(char in host for char in " /\n\r"):
            raise GateError("Host de upstream inválido.")
        return self._request(
            "POST",
            "/redirections/manual",
            {
                "name": name.strip(),
                "upstream_port": upstream_port,
                "upstream_host": host,
                "enabled": bool(enabled),
                "mode": mode,
            },
        )

    def sync_docker(self) -> dict:
        return self._request("POST", "/redirections/sync", {})

    def firewall_sync(self) -> dict:
        return self._request("POST", "/firewall/sync", {})

    def set_alias(self, ip: str, alias: str) -> dict:
        text = alias.strip()
        if len(text) > 80 or any(char in text for char in "\n\r"):
            raise GateError("Alias recusado.")
        return self._request("POST", "/aliases", {"ip": _ip(ip), "alias": text})

    def block_ip(self, ip: str) -> dict:
        return self._request("POST", "/blocklist", {"ip": _ip(ip)})

    def unblock_ip(self, ip: str) -> dict:
        return self._request("DELETE", "/blocklist/" + _quote(_ip(ip)))

    def remove_client(self, ip: str) -> dict:
        return self._request("DELETE", "/clients/" + _quote(_ip(ip)))


def apply_action(gate: ScoutGate, action: dict) -> str:
    kind = action.get("kind")
    if kind == "toggle":
        gate.toggle(str(action["id"]), bool(action["enabled"]))
        return "Rota atualizada."
    if kind == "patch":
        gate.patch(str(action["id"]), dict(action.get("fields") or {}))
        return "Rota manual gravada."
    if kind == "delete":
        gate.delete(str(action["id"]))
        return "Rota manual apagada."
    if kind == "manual":
        gate.add_manual(str(action["name"]), int(action["upstream_port"]), upstream_host=str(action.get("upstream_host") or "host.docker.internal"))
        return "Porta manual incluída, desligada."
    if kind == "sync_docker":
        gate.sync_docker()
        return "Sincronização com o Docker pedida."
    if kind == "firewall":
        gate.firewall_sync()
        return "Firewall sincronizado."
    if kind == "alias":
        gate.set_alias(str(action["ip"]), str(action.get("alias") or ""))
        return "Alias gravado."
    if kind == "block":
        gate.block_ip(str(action["ip"]))
        return "IP bloqueado na borda."
    if kind == "unblock":
        gate.unblock_ip(str(action["ip"]))
        return "IP retirado do bloqueio."
    if kind == "remove_client":
        gate.remove_client(str(action["ip"]))
        return "IP removido da lista de clientes."
    raise GateError("Ação do Scout recusada.")


def describe_snapshot(kind: str, snap: dict) -> str:
    if kind == "scout_resumo":
        health = snap.get("health") or {}
        summary = health.get("summary") or snap.get("summary") or {}
        return (
            f"Scout em {health.get('public_port', '4050')}, admin {health.get('admin_port', 8765)}. "
            f"Ativos {summary.get('active', 0)}, total {summary.get('total', 0)}. "
            f"Firewall {'ativo' if health.get('enforce_firewall') else 'inativo'}. "
            f"Docker {'ok' if health.get('docker_ok') else 'indisponível'}."
        )
    if kind == "scout_rotas":
        lines = []
        for entry in (snap.get("entries") or [])[:40]:
            state = "ativa" if entry.get("enabled") else "inativa"
            lines.append(
                f"{entry.get('name') or entry.get('id')} ({entry.get('mode', 'tcp')}, {state}) "
                f"porta {entry.get('listen_port')} -> {entry.get('upstream_host')}:{entry.get('upstream_port')}"
            )
        return "Rotas do Scout:\n" + ("\n".join(lines) if lines else "nenhuma rota.")
    if kind == "scout_trafego":
        lines = []
        for row in (snap.get("traffic") or [])[:20]:
            lines.append(
                f"{row.get('time', '')} {row.get('client_ip', '')} {row.get('route_name', '')} "
                f"{row.get('bytes_total', 0)} bytes"
            )
        alerts = snap.get("alerts") or []
        extra = f"\nAlertas: {len(alerts)}." if alerts else ""
        return "Tráfego recente:\n" + ("\n".join(lines) if lines else "sem sessões.") + extra
    if kind == "scout_clientes":
        lines = []
        for row in (snap.get("clients") or [])[:40]:
            lines.append(f"{row.get('ip')} alias {row.get('alias') or '—'} bloqueado {'sim' if row.get('blocked') else 'não'}")
        return "Clientes vistos na borda:\n" + ("\n".join(lines) if lines else "nenhum.")
    raise GateError("Consulta do Scout recusada.")
