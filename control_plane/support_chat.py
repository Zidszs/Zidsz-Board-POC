"""Chat de suporte via Ollama local.

O modelo só pede ações. Quem muda estado é o clique em Confirmar.
Start, stop e restart reutilizam o subprocess já allowlisted em operations.
Não há shell=True e o Control Plane não entra na lista.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlparse

from control_plane.config import SERVICE_DESCRIPTIONS, ServiceSpec
from control_plane.health import HealthResult
from control_plane.operations import (
    ALLOWED_CONTAINERS,
    OperationError,
    container_logs,
    run_allowed_container,
    start_porteiro,
    stop_managed_porteiro,
)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
MAX_LOG_LINES = 200
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,80}$")
_SENSITIVE_KEY = re.compile(r"(KEY|SECRET|TOKEN|PASS)", re.IGNORECASE)
_MUTATING = frozenset({"iniciar_servico", "parar_servico", "reiniciar_servico"})
_SCOUT_READONLY = frozenset({"scout_resumo", "scout_rotas", "scout_trafego", "scout_clientes"})
_READONLY = frozenset({"status_servico", "logs_servico"}) | _SCOUT_READONLY
_KNOWN_TOOLS = _MUTATING | _READONLY
_FORBIDDEN_IDS = frozenset({"control-plane"})

Transport = Callable[..., object]


class OllamaError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NeedsConfirmation(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ChatConfig:
    base_url: str
    model: str


@dataclass(frozen=True)
class ToolDecision:
    kind: str  # rejected | readonly | confirm
    message: str
    tool: str = ""
    service_id: str = ""
    tail: int = 80

    @property
    def mutating(self) -> bool:
        return self.kind == "confirm"


def normalize_base_url(raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        return DEFAULT_OLLAMA_URL
    value = str(raw).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or "\n" in value or "\r" in value:
        return DEFAULT_OLLAMA_URL
    return value


def normalize_model(raw: str | None) -> str:
    if raw is None:
        return DEFAULT_MODEL
    value = str(raw).strip()
    if _MODEL_RE.fullmatch(value):
        return value
    return DEFAULT_MODEL


def chat_config_from_env(values: Mapping[str, str]) -> ChatConfig:
    return ChatConfig(
        base_url=normalize_base_url(values.get("OLLAMA_BASE_URL")),
        model=normalize_model(values.get("SUPPORT_CHAT_MODEL")),
    )


def redact_text(text: str, env: Mapping[str, str]) -> str:
    secrets = []
    for key, value in env.items():
        if not _SENSITIVE_KEY.search(str(key)):
            continue
        secret = str(value).strip()
        if len(secret) < 6:
            continue
        secrets.append(secret)
    redacted = text
    for secret in sorted(set(secrets), key=len, reverse=True):
        redacted = redacted.replace(secret, "«redigido»")
    return redacted


def _service_map(services: tuple[ServiceSpec, ...] | list[ServiceSpec]) -> dict[str, ServiceSpec]:
    return {spec.id: spec for spec in services}


def decide_tool(name: object, arguments: object, services: tuple[ServiceSpec, ...] | list[ServiceSpec]) -> ToolDecision:
    """Não executa nada. Rejeita ferramenta, serviço ou comando fora da lista."""
    if not isinstance(name, str) or name not in _KNOWN_TOOLS:
        return ToolDecision(
            "rejected",
            "Ferramenta recusada. O chat só consulta status, logs e o Scout em leitura, ou pede start, stop ou restart da lista.",
        )
    if not isinstance(arguments, dict):
        return ToolDecision("rejected", "Argumentos recusados.")
    if name in _SCOUT_READONLY:
        if arguments:
            return ToolDecision("rejected", "Argumento recusado. A consulta do Scout não recebe comando livre.")
        return _com_permissao(ToolDecision("readonly", "Consulta do Scout permitida.", tool=name, service_id="scout"))
    allowed_keys = {"service_id", "tail"} if name == "logs_servico" else {"service_id"}
    if set(arguments) - allowed_keys:
        return ToolDecision("rejected", "Argumento recusado. Não executo comando livre.")
    service_id = arguments.get("service_id")
    if not isinstance(service_id, str) or not service_id.strip():
        return ToolDecision("rejected", "Serviço ausente na chamada.")
    service_id = service_id.strip()
    if service_id in _FORBIDDEN_IDS:
        return ToolDecision("rejected", "O chat não para nem reinicia o Control Plane.")
    spec = _service_map(services).get(service_id)
    if spec is None:
        return ToolDecision("rejected", f"«{service_id}» não é um serviço deste painel. Nada foi executado.")
    tail = 80
    if name == "logs_servico":
        raw_tail = arguments.get("tail", 80)
        if isinstance(raw_tail, bool) or not isinstance(raw_tail, int) or raw_tail < 1 or raw_tail > MAX_LOG_LINES:
            return ToolDecision("rejected", "A quantidade de linhas precisa ser um inteiro de 1 a 200.")
        tail = raw_tail
        if not spec.container_name or spec.container_name not in ALLOWED_CONTAINERS:
            return ToolDecision("rejected", f"{spec.title} não tem logs de container nesta lista.")
    if name in _MUTATING:
        if service_id == "porteiro" and name == "reiniciar_servico":
            return ToolDecision("rejected", "O chat não reinicia o Porteiro. Dá para pedir start ou stop do processo que o próprio painel subiu.")
        if service_id != "porteiro" and (not spec.container_name or spec.container_name not in ALLOWED_CONTAINERS):
            return ToolDecision(
                "rejected",
                f"{spec.title} não entra em start, stop ou restart pelo chat. Use os botões da seção Operações para ligar ou desligar a stack desse app.",
            )
        verbs = {"iniciar_servico": "iniciar", "parar_servico": "parar", "reiniciar_servico": "reiniciar"}
        return _com_permissao(
            ToolDecision(
                "confirm",
                f"O modelo pediu para {verbs[name]} {spec.title}. Nada foi executado ainda.",
                tool=name,
                service_id=service_id,
                tail=tail,
            )
        )
    return _com_permissao(ToolDecision("readonly", "Consulta permitida.", tool=name, service_id=service_id, tail=tail))


def _com_permissao(decision: ToolDecision) -> ToolDecision:
    from control_plane.actor import allows, panel_enforced

    if not panel_enforced() or decision.kind == "rejected":
        return decision
    if decision.tool in _SCOUT_READONLY:
        if allows("ver", "scout"):
            return decision
        return ToolDecision("rejected", "Sem permissão para ver o Scout.")
    acao = "ver" if decision.tool == "status_servico" else "operar"
    if allows(acao, decision.service_id):
        return decision
    return ToolDecision("rejected", "Sem permissão para esta ação.")


def perform(
    decision: ToolDecision,
    *,
    confirmed: bool,
    services: tuple[ServiceSpec, ...] | list[ServiceSpec],
    health_by_id: Mapping[str, HealthResult],
    env: Mapping[str, str],
    root,
) -> str:
    if decision.kind == "rejected":
        return decision.message
    if decision.kind == "confirm" and not confirmed:
        raise NeedsConfirmation(decision.message)
    if decision.kind not in {"readonly", "confirm"}:
        return "Ação recusada."
    if decision.tool in _SCOUT_READONLY:
        from control_plane.actor import allows, panel_enforced
        from control_plane.scout_client import query_text

        if panel_enforced() and not allows("ver", "scout"):
            return "Sem permissão para ver o Scout."
        base = env.get("SCOUT_BACKEND_URL") or env.get("SCOUT_HTTP_URL") or ""
        return redact_text(query_text(decision.tool, base=base or None), env)
    spec = _service_map(services).get(decision.service_id)
    if spec is None or decision.service_id in _FORBIDDEN_IDS:
        return "Serviço recusado. Nada foi executado."
    try:
        if decision.tool == "status_servico":
            from control_plane.actor import allows, panel_enforced

            if panel_enforced() and not allows("ver", spec.id):
                return "Sem permissão para esta ação."
            return _status_text(spec, health_by_id.get(spec.id))
        if decision.tool == "logs_servico":
            result = container_logs(spec.container_name or "", tail=decision.tail, service_id=spec.id)
            return redact_text(result.detail, env)
        if decision.tool == "reiniciar_servico":
            result = run_allowed_container("restart", spec.container_name or "", service_id=spec.id)
            return result.detail
        if decision.tool == "iniciar_servico":
            if spec.id == "porteiro":
                result = start_porteiro(root, dict(env))
                return result.detail
            result = run_allowed_container("start", spec.container_name or "", service_id=spec.id)
            return result.detail
        if decision.tool == "parar_servico":
            if spec.id == "porteiro":
                result = stop_managed_porteiro(root)
                return result.detail
            result = run_allowed_container("stop", spec.container_name or "", service_id=spec.id)
            return result.detail
    except OperationError as exc:
        return exc.message
    except Exception as exc:
        from control_plane.actor import AccessDenied

        if isinstance(exc, AccessDenied):
            return exc.message
        raise
    return "Ação recusada."


def _status_text(spec: ServiceSpec, result: HealthResult | None) -> str:
    copy = SERVICE_DESCRIPTIONS.get(spec.id)
    summary = copy.summary if copy else spec.title
    if not spec.monitored:
        return f"{spec.title}: sem checagem HTTP (não publica porta neste painel). {summary}"
    if result is None:
        return f"{spec.title}: ainda sem checagem. {summary}"
    return f"{spec.title}: {result.detail} {summary}"


def build_system_prompt(services: tuple[ServiceSpec, ...] | list[ServiceSpec], health_by_id: Mapping[str, HealthResult]) -> str:
    lines = [
        "Você é o suporte do N8Groker, em português claro.",
        "Explique para que serve cada aplicação e como usá-la. Não invente comando de shell.",
        "Para consultar, use as ferramentas de status, logs e as de leitura do Scout (resumo, rotas, tráfego, clientes). Para iniciar, parar ou reiniciar, use a ferramenta e espere o usuário confirmar no painel. Você não executa essa mudança sozinho.",
        "Rota, alias, bloqueio, firewall e porta manual do Scout só existem na aba Scout, com confirmação. Não há ferramenta para isso.",
        "Não peça para parar o Control Plane. Este chat não desliga a si mesmo.",
        "Você é um modelo de 7B: se não tiver certeza, diga isso em vez de inventar.",
        "",
        "Peças desta stack:",
    ]
    for spec in services:
        copy = SERVICE_DESCRIPTIONS.get(spec.id)
        summary = copy.summary if copy else ""
        detail = copy.detail if copy else ""
        status = _status_text(spec, health_by_id.get(spec.id))
        lines.append(
            f"- {spec.title} (id {spec.id}): {summary} {detail} "
            f"Link {spec.link_url}. Checagem {spec.health_url}. Agora: {status}"
        )
    painel = SERVICE_DESCRIPTIONS.get("control-plane")
    if painel:
        lines.append(f"- Control Plane (este painel, fora das ferramentas de parar): {painel.summary} {painel.detail}")
    return "\n".join(lines)


def tool_schemas(services: tuple[ServiceSpec, ...] | list[ServiceSpec]) -> list[dict]:
    from control_plane.actor import allows, panel_enforced

    ids = [spec.id for spec in services if spec.id not in _FORBIDDEN_IDS]
    if panel_enforced():
        ver = [spec_id for spec_id in ids if allows("ver", spec_id)]
        operar = [spec_id for spec_id in ids if allows("operar", spec_id)]
        esquemas = []
        if ver:
            prop = {"type": "string", "enum": ver, "description": "Id do serviço registrado no painel."}
            esquemas.append(_schema("status_servico", "Estado atual de um serviço registrado.", {"service_id": prop}, ["service_id"]))
        if operar:
            prop = {"type": "string", "enum": operar, "description": "Id do serviço registrado no painel."}
            esquemas.append(
                _schema(
                    "logs_servico",
                    "Últimas linhas de log do container allowlisted desse serviço.",
                    {"service_id": prop, "tail": {"type": "integer", "description": "Linhas, de 1 a 200."}},
                    ["service_id"],
                )
            )
            esquemas.append(_schema("iniciar_servico", "Pede para iniciar um serviço allowlisted. Não executa sem confirmação humana.", {"service_id": prop}, ["service_id"]))
            esquemas.append(_schema("parar_servico", "Pede para parar um serviço allowlisted. Não executa sem confirmação humana.", {"service_id": prop}, ["service_id"]))
            esquemas.append(_schema("reiniciar_servico", "Pede para reiniciar um container allowlisted. Não executa sem confirmação humana.", {"service_id": prop}, ["service_id"]))
        if allows("ver", "scout"):
            esquemas.extend(
                [
                    _schema("scout_resumo", "Leitura do estado do Scout (porta, firewall, docker). Não altera rotas.", {}, []),
                    _schema("scout_rotas", "Leitura das rotas do Scout. Não liga nem apaga rota.", {}, []),
                    _schema("scout_trafego", "Leitura do tráfego recente do Scout. Não bloqueia IP.", {}, []),
                    _schema("scout_clientes", "Leitura dos clientes vistos na borda. Não remove nem altera alias.", {}, []),
                ]
            )
        return esquemas
    service_prop = {"type": "string", "enum": ids, "description": "Id do serviço registrado no painel."}
    return [
        _schema("status_servico", "Estado atual de um serviço registrado.", {"service_id": service_prop}, ["service_id"]),
        _schema(
            "logs_servico",
            "Últimas linhas de log do container allowlisted desse serviço.",
            {"service_id": service_prop, "tail": {"type": "integer", "description": "Linhas, de 1 a 200."}},
            ["service_id"],
        ),
        _schema("iniciar_servico", "Pede para iniciar um serviço allowlisted. Não executa sem confirmação humana.", {"service_id": service_prop}, ["service_id"]),
        _schema("parar_servico", "Pede para parar um serviço allowlisted. Não executa sem confirmação humana.", {"service_id": service_prop}, ["service_id"]),
        _schema("reiniciar_servico", "Pede para reiniciar um container allowlisted. Não executa sem confirmação humana.", {"service_id": service_prop}, ["service_id"]),
        _schema("scout_resumo", "Leitura do estado do Scout (porta, firewall, docker). Não altera rotas.", {}, []),
        _schema("scout_rotas", "Leitura das rotas do Scout. Não liga nem apaga rota.", {}, []),
        _schema("scout_trafego", "Leitura do tráfego recente do Scout. Não bloqueia IP.", {}, []),
        _schema("scout_clientes", "Leitura dos clientes vistos na borda. Não remove nem altera alias.", {}, []),
    ]


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(method: str, url: str, payload: dict | None, timeout: float, transport: Transport | None, stream: bool = False):
    try:
        if transport is not None:
            return transport(method, url, payload, timeout, stream)
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "N8GrokerSupportChat/1.0"},
        )
        return _opener().open(req, timeout=timeout)
    except OllamaError:
        raise
    except (TimeoutError, urllib.error.URLError) as exc:
        raise OllamaError(_down_message(url)) from exc


def _down_message(url: str) -> str:
    base = url.split("/api/")[0]
    return (
        f"O Ollama não respondeu em {base}. "
        "Se ele deveria estar nesta máquina, rode o iniciar_servicos.ps1 com USE_OLLAMA_LOCAL=1 ou, no terminal: ollama serve. "
        "O chat não derruba o restante do painel."
    )


def _read_body(response) -> dict:
    if isinstance(response, dict):
        return response
    raw = response.read() if hasattr(response, "read") else response
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaError("O Ollama respondeu com um texto que não é JSON. Nada foi executado.") from exc
    if not isinstance(data, dict):
        raise OllamaError("O Ollama respondeu fora do formato esperado. Nada foi executado.")
    return data


def _model_present(tags: dict, model: str) -> bool:
    models = tags.get("models")
    if not isinstance(models, list):
        return False
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "")
        if name == model or name.startswith(model + ":"):
            return True
    return False


def check_ollama(config: ChatConfig, *, timeout: float = 3, transport: Transport | None = None) -> None:
    try:
        response = _request("GET", config.base_url + "/api/tags", None, timeout, transport)
        tags = _read_body(response)
    except OllamaError:
        raise
    except Exception as exc:
        raise OllamaError(_down_message(config.base_url + "/api/tags")) from exc
    if not _model_present(tags, config.model):
        raise OllamaError(
            f"O Ollama está no ar, mas o modelo {config.model} não está baixado. "
            f"Rode no terminal: ollama pull {config.model}. "
            "O painel só baixa se você confirmar o botão. Sem esse modelo o chat não abre."
        )


def pull_model(model: str, *, timeout: float = 600) -> str:
    if not _MODEL_RE.fullmatch(model):
        raise OllamaError("Nome de modelo recusado. Nada foi baixado.")
    exe = shutil.which("ollama")
    if not exe:
        raise OllamaError("O comando ollama não está no PATH. Instale o Ollama e rode de novo.")
    try:
        proc = subprocess.run(
            [exe, "pull", model],
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OllamaError("O download do modelo excedeu o tempo limite.") from exc
    except OSError as exc:
        raise OllamaError(f"Não consegui chamar o Ollama: {exc.strerror or 'erro do sistema'}") from exc
    tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if len(tail) > 500:
        tail = tail[-500:]
    if proc.returncode != 0:
        raise OllamaError(f"ollama pull {model} falhou. {tail}".strip())
    return f"Modelo {model} baixado."


def ollama_chat(
    config: ChatConfig,
    messages: list[dict],
    tools: list[dict],
    *,
    timeout: float = 120,
    transport: Transport | None = None,
    on_delta: Callable[[str], None] | None = None,
) -> dict:
    payload = {"model": config.model, "messages": messages, "tools": tools, "stream": on_delta is not None}
    try:
        response = _request("POST", config.base_url + "/api/chat", payload, timeout, transport, stream=payload["stream"])
    except OllamaError:
        raise
    except Exception as exc:
        raise OllamaError(_down_message(config.base_url + "/api/chat")) from exc
    if on_delta is None or isinstance(response, dict):
        data = _read_body(response)
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        return message
    content_parts: list[str] = []
    tool_calls = None
    try:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, bytes) else str(raw_line)
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            message = chunk.get("message") if isinstance(chunk, dict) else None
            if not isinstance(message, dict):
                continue
            piece = message.get("content") or ""
            if piece:
                content_parts.append(piece)
                on_delta(piece)
            if message.get("tool_calls"):
                tool_calls = message["tool_calls"]
    except json.JSONDecodeError as exc:
        raise OllamaError("A resposta do Ollama veio incompleta. Nada foi executado.") from exc
    message = {"role": "assistant", "content": "".join(content_parts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def apply_model_message(
    message: Mapping,
    *,
    services: tuple[ServiceSpec, ...] | list[ServiceSpec],
    health_by_id: Mapping[str, HealthResult],
    env: Mapping[str, str],
    root,
) -> tuple[str, ToolDecision | None]:
    """Roda só ferramentas de leitura. Start/stop/restart viram pedido de confirmação."""
    notes: list[str] = []
    content = message.get("content") if isinstance(message, Mapping) else ""
    if isinstance(content, str) and content.strip():
        notes.append(content.strip())
    pending: ToolDecision | None = None
    for name, arguments in iter_tool_calls(message):
        decision = decide_tool(name, arguments, services)
        if decision.kind == "confirm":
            pending = decision
            notes.append(decision.message)
            break
        if decision.kind == "rejected":
            notes.append(decision.message)
            continue
        notes.append(perform(decision, confirmed=True, services=services, health_by_id=health_by_id, env=env, root=root))
    visible = "\n\n".join(notes).strip() or "Não consegui montar uma resposta."
    return visible, pending


def iter_tool_calls(message: Mapping) -> list[tuple[str, dict]]:
    calls = message.get("tool_calls") if isinstance(message, Mapping) else None
    found: list[tuple[str, dict]] = []
    if not isinstance(calls, list):
        return found
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else call
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        arguments = fn.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        if not isinstance(arguments, dict):
            arguments = {}
        found.append((name if isinstance(name, str) else "", arguments))
    return found
