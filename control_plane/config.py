"""URLs, containers e caminhos reais do repositório.

Os defaults vêm dos compose files e do `iniciar_servicos.ps1`.
Portainer não existe neste repo: o atalho fica marcado como placeholder.

Sobrescritas opcionais, sem comando de shell:
- arquivo `control_plane.local.json` na raiz (veja o exemplo)
- variável `CP_CONFIG_FILE` apontando para outro JSON
- `N8GROKER_ROOT`, `CP_HEALTH_TIMEOUT`, `CP_SLOW_MS`, `CP_SCOUT_BUILD`
- `CP_LANGFUSE_URL`, `CP_LITELLM_URL`, `CP_N8N_URL`, `CP_PORTAINER_URL`
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class Shortcut:
    id: str
    title: str
    url: str
    blurb: str
    placeholder: bool = False


@dataclass(frozen=True)
class ServiceCopy:
    """Uma linha do que é, e uma ou duas frases do para que serve nesta stack."""

    summary: str
    detail: str


@dataclass(frozen=True)
class ServiceSpec:
    id: str
    title: str
    health_url: str
    link_url: str
    expected_statuses: tuple[int, ...] = (200,)
    container_name: str | None = None
    placeholder: bool = False
    critical: bool = False
    group: str = "n8groker"
    # False: card informativo. Sem porta no host, a checagem HTTP ficaria sempre vermelha.
    monitored: bool = True


# Texto do painel, ao lado do registro. A chave é o id do serviço, do atalho ou do próprio painel.
SERVICE_DESCRIPTIONS: dict[str, ServiceCopy] = {
    "n8n": ServiceCopy(
        "Gerenciamento de fluxos de processos",
        "Motor de automação dos workflows. A aprovação por e-mail e os webhooks rodam aqui, e a credencial OpenAI do editor aponta para o LiteLLM.",
    ),
    "porteiro": ServiceCopy(
        "Controle de quem entra no n8n",
        "Proxy Node.js na porta 5677. Um IP novo fica na fila até o workflow aprovar ou bloquear; IP aprovado segue para o n8n. O endereço usado é o do socket, não um header solto.",
    ),
    "ngrok": ServiceCopy(
        "Túnel da internet até esta máquina",
        "Abre um HTTPS público sem configurar o roteador. Com o Scout ligado, o túnel aponta para a porta 4050; sem Scout, aponta para o Porteiro. A URL aparece na porta 4040 e o script grava ela no .env.",
    ),
    "scout": ServiceCopy(
        "Borda que registra o tráfego e escolhe a rota",
        "Backend na porta 8765, container scout-backend. Recebe o que o ngrok entrega na porta 4050 e só encaminha ao Porteiro nas rotas ligadas. Também guarda alias, blocklist e o log das sessões.",
    ),
    "scout-gui": ServiceCopy(
        "Gestão do Scout nesta aba",
        "A tecla G do HUD abre http://localhost:8501/?aba=scout. Rotas, tráfego, clientes e firewall ficam nessa aba, com confirmação. Neste ramo não há janela Tk separada.",
    ),
    "langfuse": ServiceCopy(
        "Tela do que os modelos responderam",
        "Interface na porta 3000, container langfuse-web. No primeiro boot cria a organização, o projeto e as chaves que o LiteLLM usa para enviar traces. O login é o e-mail e a senha do .env.",
    ),
    "langfuse-worker": ServiceCopy(
        "Processa a fila de eventos do Langfuse",
        "Container langfuse-worker, sem tela própria. Lê o que ficou no Redis e grava em Postgres, ClickHouse e MinIO. A porta 3030 no localhost só responde à checagem de saúde.",
    ),
    "litellm": ServiceCopy(
        "Porta única para chamar os modelos",
        "Proxy compatível com a API da OpenAI, na porta 4000. O n8n chama http://litellm:4000/v1 dentro da rede Docker. Nenhum modelo vem pronto: o cadastro é na UI e fica no banco.",
    ),
    "minio": ServiceCopy(
        "Arquivos e mídia do Langfuse",
        "Container langfuse-minio. Guarda upload e exportação. No host só o localhost na porta 9090 responde; o navegador manda a mídia direto para esse endereço.",
    ),
    "langfuse-postgres": ServiceCopy(
        "Cadastro de usuários e projetos do Langfuse",
        "Postgres interno, sem porta no host. Não é o banco do LiteLLM nem o SQLite do n8n. O painel não reinicia este container.",
    ),
    "langfuse-clickhouse": ServiceCopy(
        "Consulta dos traces e das métricas",
        "ClickHouse interno, sem porta no host. É onde o Langfuse lê o volume de observações. Na primeira subida é a peça que mais usa memória.",
    ),
    "langfuse-redis": ServiceCopy(
        "Fila do que o Langfuse ainda vai processar",
        "Redis interno, sem porta no host. O worker consome esta fila. A política noeviction evita apagar item para liberar memória.",
    ),
    "litellm-db": ServiceCopy(
        "Banco dos modelos e usuários do LiteLLM",
        "Postgres separado, sem porta no host. A UI grava aqui os modelos e as chaves do proxy. Este volume não é o do Postgres do Langfuse.",
    ),
    "portainer": ServiceCopy(
        "Atalho para um painel de containers, se existir",
        "Este repositório não sobe o Portainer. O botão só abre a porta 9000. Se não houver nada escutando lá, a página não abre.",
    ),
    "control-plane": ServiceCopy(
        "Este painel de status e de operação",
        "Mostra se cada peça responde. O núcleo (Porteiro, Scout, ngrok e os painéis) sobe pelo HUD ou pelo botão Iniciar núcleo; n8n e Langfuse/LiteLLM ligam e desligam por stack. O console não desliga o núcleo. Quem grava a URL do ngrok e quem fecha a porta 8501 junto com o resto é o iniciar_servicos.ps1, na tecla Q.",
    ),
}


@dataclass(frozen=True)
class Settings:
    root: Path
    shortcuts: tuple[Shortcut, ...]
    services: tuple[ServiceSpec, ...]
    health_timeout_seconds: float = 2.0
    slow_threshold_ms: float = 1000.0
    compose_timeout_seconds: float = 180.0
    restart_timeout_seconds: float = 60.0
    scout_build: bool = False
    warnings: tuple[str, ...] = ()


def default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _http_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and "\n" not in value and "\r" not in value


def _positive_float(raw: str | None, default: float) -> float:
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def _truthy(raw: str | None, default: bool) -> bool:
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "sim", "on"}


def default_shortcuts() -> tuple[Shortcut, ...]:
    return (
        Shortcut(
            "langfuse",
            "Langfuse",
            "http://localhost:3000",
            "Traces e UI. Container langfuse-web na porta 3000.",
        ),
        Shortcut(
            "litellm",
            "LiteLLM",
            "http://localhost:4000/ui",
            "Gateway OpenAI-compatível. Modelos entram por esta UI.",
        ),
        Shortcut(
            "n8n",
            "n8n",
            "http://localhost:5678",
            "Workflows. Container n8n_app. Credencial OpenAI aponta ao LiteLLM.",
        ),
        Shortcut(
            "portainer",
            "Portainer",
            "http://localhost:9000",
            "Não há Portainer neste repositório. Ajuste a URL se você subir um.",
            placeholder=True,
        ),
    )


def default_services() -> tuple[ServiceSpec, ...]:
    # Rotas conferidas no código / na doc oficial das imagens pinadas.
    # Porteiro: /favicon.ico responde 204 antes do proxy e da fila (porteiro.js).
    return (
        ServiceSpec(
            id="n8n",
            title="n8n",
            health_url="http://127.0.0.1:5678/healthz",
            link_url="http://localhost:5678",
            container_name="n8n_app",
            critical=True,
            group="n8groker",
        ),
        ServiceSpec(
            id="porteiro",
            title="Porteiro",
            health_url="http://127.0.0.1:5677/favicon.ico",
            link_url="http://localhost:5677",
            expected_statuses=(204,),
            container_name=None,
            critical=True,
            group="n8groker",
        ),
        ServiceSpec(
            id="ngrok",
            title="ngrok",
            health_url="http://127.0.0.1:4040/api/tunnels",
            link_url="http://localhost:4040",
            container_name="ngrok_service",
            critical=True,
            group="n8groker",
        ),
        ServiceSpec(
            id="scout",
            title="Scout",
            health_url="http://127.0.0.1:8765/health",
            link_url="http://127.0.0.1:8765/health",
            container_name="scout-backend",
            critical=True,
            group="n8groker",
        ),
        ServiceSpec(
            id="langfuse",
            title="Langfuse",
            health_url="http://127.0.0.1:3000/api/public/health?failIfDatabaseUnavailable=true",
            link_url="http://localhost:3000",
            container_name="langfuse-web",
            critical=True,
            group="llm",
        ),
        ServiceSpec(
            id="langfuse-worker",
            title="Langfuse worker",
            health_url="http://127.0.0.1:3030/api/health",
            link_url="http://localhost:3000",
            container_name="langfuse-worker",
            critical=True,
            group="llm",
        ),
        ServiceSpec(
            id="litellm",
            title="LiteLLM",
            health_url="http://127.0.0.1:4000/health/readiness",
            link_url="http://localhost:4000/ui",
            container_name="litellm",
            critical=True,
            group="llm",
        ),
        ServiceSpec(
            id="minio",
            title="MinIO",
            health_url="http://127.0.0.1:9090/minio/health/live",
            link_url="http://127.0.0.1:9090/minio/health/live",
            container_name="langfuse-minio",
            critical=False,
            group="llm",
        ),
        ServiceSpec(
            id="scout-gui",
            title="Aba Scout",
            health_url="http://127.0.0.1:9/scout-gui",
            link_url="http://localhost:8501/?aba=scout",
            container_name=None,
            critical=False,
            group="n8groker",
            monitored=False,
        ),
        ServiceSpec(
            id="langfuse-postgres",
            title="Postgres do Langfuse",
            health_url="http://127.0.0.1:9/langfuse-postgres",
            link_url="http://127.0.0.1:9/langfuse-postgres",
            container_name="langfuse-postgres",
            critical=False,
            group="dados",
            monitored=False,
        ),
        ServiceSpec(
            id="langfuse-clickhouse",
            title="ClickHouse",
            health_url="http://127.0.0.1:9/langfuse-clickhouse",
            link_url="http://127.0.0.1:9/langfuse-clickhouse",
            container_name="langfuse-clickhouse",
            critical=False,
            group="dados",
            monitored=False,
        ),
        ServiceSpec(
            id="langfuse-redis",
            title="Redis",
            health_url="http://127.0.0.1:9/langfuse-redis",
            link_url="http://127.0.0.1:9/langfuse-redis",
            container_name="langfuse-redis",
            critical=False,
            group="dados",
            monitored=False,
        ),
        ServiceSpec(
            id="litellm-db",
            title="Postgres do LiteLLM",
            health_url="http://127.0.0.1:9/litellm-db",
            link_url="http://127.0.0.1:9/litellm-db",
            container_name="litellm-db",
            critical=False,
            group="dados",
            monitored=False,
        ),
    )


def _load_json(path: Path, warnings: list[str]) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        warnings.append(f"Não consegui ler {path.name}. Seguindo com os padrões do repositório.")
        return {}
    if not isinstance(data, dict):
        warnings.append(f"{path.name} precisa ser um objeto JSON. Ignorado.")
        return {}
    return data


def load_settings(
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    env = os.environ if environ is None else environ
    base = Path(env["N8GROKER_ROOT"]).expanduser() if env.get("N8GROKER_ROOT") else (root or default_root())
    project = base.resolve()
    warnings: list[str] = []

    config_path = Path(env["CP_CONFIG_FILE"]).expanduser() if env.get("CP_CONFIG_FILE") else project / "control_plane.local.json"
    overlay: dict = {}
    if env.get("CP_CONFIG_FILE") or config_path.is_file():
        if config_path.is_file():
            overlay = _load_json(config_path, warnings)
        elif env.get("CP_CONFIG_FILE"):
            warnings.append("CP_CONFIG_FILE não aponta para um arquivo. Ignorado.")

    shortcuts = list(default_shortcuts())
    services = list(default_services())
    url_overrides = overlay.get("shortcuts") if isinstance(overlay.get("shortcuts"), dict) else {}
    service_overrides = overlay.get("services") if isinstance(overlay.get("services"), dict) else {}

    env_shortcut = {
        "langfuse": "CP_LANGFUSE_URL",
        "litellm": "CP_LITELLM_URL",
        "n8n": "CP_N8N_URL",
        "portainer": "CP_PORTAINER_URL",
    }
    env_health = {
        "langfuse": "CP_LANGFUSE_HEALTH",
        "litellm": "CP_LITELLM_HEALTH",
        "n8n": "CP_N8N_HEALTH",
    }

    new_shortcuts: list[Shortcut] = []
    for item in shortcuts:
        url = item.url
        file_url = url_overrides.get(item.id)
        if isinstance(file_url, str) and _http_url(file_url):
            url = file_url.strip()
        elif isinstance(file_url, str):
            warnings.append(f"URL de atalho ignorada para {item.id}.")
        env_name = env_shortcut.get(item.id)
        if env_name and env.get(env_name):
            candidate = env[env_name].strip()
            if _http_url(candidate):
                url = candidate
            else:
                warnings.append(f"{env_name} não é uma URL http(s). Mantive o padrão.")
        new_shortcuts.append(replace(item, url=url))

    new_services: list[ServiceSpec] = []
    for spec in services:
        health = spec.health_url
        link = spec.link_url
        block = service_overrides.get(spec.id)
        if isinstance(block, dict):
            if isinstance(block.get("health_url"), str):
                candidate = block["health_url"].strip()
                if _http_url(candidate):
                    health = candidate
                else:
                    warnings.append(f"Health URL ignorada para {spec.id}.")
            if isinstance(block.get("link_url"), str) and _http_url(block["link_url"]):
                link = block["link_url"].strip()
        env_name = env_health.get(spec.id)
        if env_name and env.get(env_name):
            candidate = env[env_name].strip()
            if _http_url(candidate):
                health = candidate
            else:
                warnings.append(f"{env_name} não é uma URL http(s). Mantive o padrão.")
        new_services.append(replace(spec, health_url=health, link_url=link))

    return Settings(
        root=project,
        shortcuts=tuple(new_shortcuts),
        services=tuple(new_services),
        health_timeout_seconds=_positive_float(env.get("CP_HEALTH_TIMEOUT"), _positive_float(str(overlay.get("health_timeout_seconds") or ""), 2.0)),
        slow_threshold_ms=_positive_float(env.get("CP_SLOW_MS"), _positive_float(str(overlay.get("slow_threshold_ms") or ""), 1000.0)),
        scout_build=_truthy(env.get("CP_SCOUT_BUILD"), bool(overlay.get("scout_build", False))),
        warnings=tuple(warnings),
    )
