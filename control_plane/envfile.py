"""Leitura do .env e geração de segredos.

O arquivo versionado é `.env.example` (só marcadores). `ensure_env` cria ou
completa `.env` sem rotacionar segredo que já exista. NGROK_AUTHTOKEN não é
gerado: o token vem do painel do ngrok.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_MARKER_RE = re.compile(r"^__GENERATE_[A-Z0-9_]+__$")
_MASTER_RE = re.compile(r"^sk-[A-Za-z0-9]+$")
# Configuração, não segredo. Vazio significa só o núcleo. Um marcador aqui
# não vira chave e um valor já escrito não é rotacionado.
_SEM_SEGREDO = frozenset({"STACKS_BOOT"})


def _alnum(n: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _hex(nbytes: int) -> str:
    return secrets.token_hex(nbytes)


def n8n_overwrite(master_key: str) -> str:
    """JSON da credencial OpenAI do n8n apontando ao LiteLLM na rede Docker.

    Sem aspas simples: o valor entra numa string YAML com aspas simples.
    """
    if not _MASTER_RE.fullmatch(master_key):
        raise ValueError("LITELLM_MASTER_KEY precisa ser sk- seguido de letras e números.")
    payload = {
        "openAiApi": {
            "apiKey": master_key,
            "url": "http://litellm:4000/v1",
        }
    }
    raw = json.dumps(payload, separators=(",", ":"))
    if "'" in raw:
        raise ValueError("O JSON da credencial do n8n não pode conter aspas simples.")
    return raw


def _generators() -> dict[str, callable]:
    return {
        "N8N_ENCRYPTION_KEY": lambda: _hex(32),
        "NEXTAUTH_SECRET": lambda: _hex(32),
        "SALT": lambda: _hex(16),
        "ENCRYPTION_KEY": lambda: _hex(32),
        "LANGFUSE_DB_PASSWORD": lambda: _alnum(32),
        "CLICKHOUSE_PASSWORD": lambda: _alnum(24),
        "REDIS_AUTH": lambda: _alnum(32),
        "MINIO_ROOT_PASSWORD": lambda: _alnum(24),
        "LANGFUSE_INIT_USER_PASSWORD": lambda: _alnum(24),
        "LANGFUSE_PUBLIC_KEY": lambda: "pk-lf-" + _hex(16),
        "LANGFUSE_SECRET_KEY": lambda: "sk-lf-" + _hex(16),
        "LITELLM_MASTER_KEY": lambda: "sk-" + _hex(24),
        "LITELLM_SALT_KEY": lambda: "sk-" + _hex(24),
        "LITELLM_DB_PASSWORD": lambda: _alnum(32),
        "LITELLM_UI_PASSWORD": lambda: _alnum(24),
    }


def is_marker(value: str | None) -> bool:
    if value is None:
        return False
    return bool(_MARKER_RE.fullmatch(value.strip()))


def parse_env(text: str) -> dict[str, str]:
    """Parser simples, alinhado ao dos scripts PowerShell (comentário em linha inteira)."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        if key:
            values[key] = value
    return values


def read_text(path: Path) -> str:
    """Lê UTF-8 e aceita BOM (PowerShell 5.1 grava utf-8 com BOM em Set-Content)."""
    return path.read_text(encoding="utf-8-sig")


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return parse_env(read_text(path))


def scout_enabled(values: dict[str, str]) -> bool:
    flag = values.get("USE_SCOUT", "1").strip().lower()
    return flag in {"1", "true", "yes", "sim", "on"}


def _porta_publicada(valor: str, reserva: str) -> str:
    texto = (valor or "").strip() or reserva
    if texto == "8501":
        return reserva
    if texto.isdigit() and 1 <= int(texto) <= 65535:
        return texto
    return reserva


def portas_publicadas(values: dict[str, str]) -> dict[str, str]:
    """O que o processo do Compose recebe. 8501 não é escuta nem o painel de borda."""
    return {
        "SCOUT_PUBLIC_PORT": _porta_publicada(values.get("SCOUT_PUBLIC_PORT", ""), "4050"),
        "PAINEL_BORDA_PORT": _porta_publicada(values.get("PAINEL_BORDA_PORT", ""), "8502"),
    }


def ngrok_tunnel_target(values: dict[str, str]) -> str:
    """Mesma regra do iniciar_servicos.ps1: Scout na porta pública, senão o Porteiro."""
    if scout_enabled(values):
        port = values.get("SCOUT_PUBLIC_PORT", "4050").strip() or "4050"
    else:
        port = "5677"
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        raise ValueError(f"Porta do túnel ngrok inválida: {port}")
    # 8501 é o HUD-admin. O alvo do túnel não muda para ela.
    if port == "8501":
        return "4050"
    return port


def ngrok_tunnel_host(values: dict[str, str]) -> str:
    """Com Scout, o ngrok fala com o container. Sem Scout, com o Porteiro no host."""
    if scout_enabled(values):
        return "scout-backend"
    return "host.docker.internal"


def llm_configured(values: dict[str, str]) -> bool:
    needed = (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LITELLM_MASTER_KEY",
        "LANGFUSE_DB_PASSWORD",
        "ENCRYPTION_KEY",
        "N8N_CREDENTIALS_OVERWRITE_DATA",
    )
    for key in needed:
        value = values.get(key, "").strip()
        if not value or is_marker(value):
            return False
    return True


def _needs_fill(current: str | None, example_value: str | None) -> bool:
    if current is None:
        return example_value is not None and is_marker(example_value)
    return is_marker(current)


def _generate(keys: list[str], current: dict[str, str]) -> dict[str, str]:
    generators = _generators()
    unknown = [key for key in keys if key not in generators and key != "N8N_CREDENTIALS_OVERWRITE_DATA"]
    if unknown:
        raise ValueError("Marcador sem gerador: " + ", ".join(unknown))

    produced: dict[str, str] = {}
    for key in keys:
        if key == "N8N_CREDENTIALS_OVERWRITE_DATA":
            continue
        produced[key] = generators[key]()

    if "N8N_CREDENTIALS_OVERWRITE_DATA" in keys:
        master = produced.get("LITELLM_MASTER_KEY") or current.get("LITELLM_MASTER_KEY", "")
        if not master or is_marker(master):
            master = generators["LITELLM_MASTER_KEY"]()
            produced["LITELLM_MASTER_KEY"] = master
        produced["N8N_CREDENTIALS_OVERWRITE_DATA"] = n8n_overwrite(master)
    return produced


def _apply(lines: list[str], generated: dict[str, str]) -> list[str]:
    applied: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.lstrip()
        match = _KEY_RE.match(line) if not stripped.startswith("#") else None
        if match and match.group(2) in generated:
            indent, key, _value = match.group(1), match.group(2), match.group(3)
            applied.append(f"{indent}{key}={generated[key]}")
            seen.add(key)
        else:
            applied.append(line)
    missing = [key for key in generated if key not in seen]
    if missing and applied and applied[-1].strip():
        applied.append("")
    for key in missing:
        applied.append(f"{key}={generated[key]}")
    return applied


@dataclass(frozen=True)
class EnsureResult:
    action: str
    filled_keys: tuple[str, ...]

    @property
    def message(self) -> str:
        if self.action == "unchanged":
            return "Nada a fazer: .env já existe e não há segredo pendente."
        names = ", ".join(self.filled_keys) if self.filled_keys else "(nenhum)"
        if self.action == "created":
            return f".env criado com segredos aleatórios. Chaves preenchidas: {names}."
        return f".env atualizado sem rotacionar segredo existente. Chaves preenchidas: {names}."


def ensure_env(example: Path, dest: Path) -> EnsureResult:
    """Cria `.env` a partir do exemplo, ou só preenche marcadores/chaves ausentes."""
    if not example.is_file():
        raise FileNotFoundError(f"Modelo não encontrado: {example}")

    example_text = read_text(example)
    example_values = parse_env(example_text)

    if dest.exists():
        base_lines = read_text(dest).splitlines()
        current = parse_env("\n".join(base_lines))
        action = "updated"
    else:
        base_lines = example_text.splitlines()
        current = {}
        action = "created"

    pending: list[str] = []
    for key, example_value in example_values.items():
        if key in _SEM_SEGREDO:
            continue
        if _needs_fill(current.get(key), example_value):
            pending.append(key)
    for key, value in current.items():
        if key in _SEM_SEGREDO:
            continue
        if is_marker(value) and key not in pending:
            pending.append(key)

    if not pending:
        if action == "created":
            dest.write_text(example_text if example_text.endswith("\n") else example_text + "\n", encoding="utf-8")
            _restrict(dest)
        return EnsureResult("unchanged" if action == "updated" else "created", ())

    generated = _generate(pending, current)
    new_lines = _apply(base_lines, generated)
    text = "\n".join(new_lines)
    if not text.endswith("\n"):
        text += "\n"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    _restrict(dest)
    return EnsureResult(action, tuple(generated))


def _restrict(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return
