"""Backup e restauração com comandos fechados.

Postgres sai por pg_dump (sem senha na linha de comando: dentro do container
o socket local aceita o usuário). O n8n é o SQLite do bind mount. ClickHouse
e MinIO saem como arquivo do volume nomeado, com tar na imagem de Postgres
já pinada. A restauração só monta o plano com confirmação explícita e põe
o stop dos serviços que usam o dado antes da gravação.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_STAMP = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
_ARCHIVE_IMAGE = "docker.io/postgres:17.11"

_POSTGRES = {
    "langfuse-postgres": {
        "title": "Postgres do Langfuse",
        "container": "langfuse-postgres",
        "user_env": "LANGFUSE_DB_USER",
        "db_env": "LANGFUSE_DB_NAME",
        "default_user": "langfuse",
        "default_db": "langfuse",
        "stop": ("langfuse-web", "langfuse-worker"),
    },
    "litellm-db": {
        "title": "Postgres do LiteLLM",
        "container": "litellm-db",
        "user_env": "LITELLM_DB_USER",
        "db_env": "LITELLM_DB_NAME",
        "default_user": "litellm",
        "default_db": "litellm",
        "stop": ("litellm",),
    },
}

_VOLUMES = {
    "clickhouse": {
        "title": "ClickHouse do Langfuse",
        "volume": "n8groker-llm_langfuse_clickhouse_data",
        "stop": ("langfuse-web", "langfuse-worker", "langfuse-clickhouse"),
        "suffix": ".tar.gz",
    },
    "minio": {
        "title": "MinIO do Langfuse",
        "volume": "n8groker-llm_langfuse_minio_data",
        "stop": ("langfuse-web", "langfuse-worker", "langfuse-minio"),
        "suffix": ".tar.gz",
    },
}

_N8N_ID = "n8n"
_N8N_STOP = ("n8n_app",)
_N8N_REL = Path("n8n") / "n8n" / "data" / "database.sqlite"


class BackupError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PlannedCommand:
    kind: str
    title: str
    argv: tuple[str, ...] = ()
    src: str | None = None
    dest: str | None = None
    capture_path: str | None = None
    stdin_path: str | None = None


def backup_catalog() -> tuple[tuple[str, str], ...]:
    return (
        ("langfuse-postgres", "Postgres do Langfuse (pg_dump)"),
        ("litellm-db", "Postgres do LiteLLM (pg_dump)"),
        (_N8N_ID, "SQLite do n8n"),
        ("clickhouse", "Volume do ClickHouse"),
        ("minio", "Volume do MinIO"),
    )


def stamp_now(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backups_dir(root: Path) -> Path:
    path = (root / ".n8groker" / "backups").resolve()
    base = (root.resolve() / ".n8groker").resolve()
    if not path.is_relative_to(base):
        raise BackupError("Pasta de backup fora do projeto.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _docker(docker: str) -> str:
    if not isinstance(docker, str) or not docker.strip() or any(char in docker for char in "\n\r\x00"):
        raise BackupError("Executável do Docker inválido.")
    return docker


def _stamp(stamp: str) -> str:
    if not isinstance(stamp, str) or not _STAMP.fullmatch(stamp):
        raise BackupError("Carimbo de backup recusado.")
    return stamp


def _ident(env: Mapping[str, str], key: str, default: str) -> str:
    raw = str(env.get(key, "") or "").strip() or default
    if not _IDENT.fullmatch(raw):
        raise BackupError("Usuário ou banco recusado. Use só letras, números e _.")
    return raw


def _known(target_id: str) -> str:
    ids = {item[0] for item in backup_catalog()}
    if target_id not in ids:
        raise BackupError("Alvo de backup recusado.")
    return target_id


def n8n_sqlite_path(root: Path) -> Path:
    path = (root / _N8N_REL).resolve()
    parent = (root / "n8n" / "n8n" / "data").resolve()
    if path.parent != parent or not path.is_relative_to(root.resolve()):
        raise BackupError("Caminho do n8n recusado.")
    return path


def _backup_name(target_id: str, stamp: str) -> str:
    if target_id in _POSTGRES:
        return f"{target_id}-{stamp}.dump"
    if target_id == _N8N_ID:
        return f"{target_id}-{stamp}.sqlite"
    return f"{target_id}-{stamp}.tar.gz"


def _name_ok(target_id: str, name: str) -> bool:
    if target_id in _POSTGRES:
        pattern = rf"^{re.escape(target_id)}-[0-9]{{8}}T[0-9]{{6}}Z\.dump$"
    elif target_id == _N8N_ID:
        pattern = r"^n8n-[0-9]{8}T[0-9]{6}Z\.sqlite$"
    elif target_id in _VOLUMES:
        pattern = rf"^{re.escape(target_id)}-[0-9]{{8}}T[0-9]{{6}}Z\.tar\.gz$"
    else:
        return False
    return re.fullmatch(pattern, name) is not None


def list_backups(root: Path, target_id: str) -> list[str]:
    target_id = _known(target_id)
    folder = backups_dir(root)
    names = [path.name for path in folder.iterdir() if path.is_file() and _name_ok(target_id, path.name)]
    return sorted(names)


def resolve_backup_file(root: Path, target_id: str, name: str) -> Path:
    target_id = _known(target_id)
    if not _name_ok(target_id, name):
        raise BackupError("Arquivo de backup recusado.")
    folder = backups_dir(root)
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise BackupError("Arquivo de backup não encontrado.")
    return path


def _stop_start(docker: str, names: tuple[str, ...], verb: str, title: str) -> PlannedCommand:
    if verb not in {"stop", "start"} or not names:
        raise BackupError("Comando de backup recusado.")
    return PlannedCommand(kind="exec", title=title, argv=(docker, verb, *names))


def _mount(source: str, target: str, *, readonly: bool = False) -> str:
    if not source or any(char in source for char in ",\n\r"):
        raise BackupError("Caminho de backup recusado.")
    spec = f"type=bind,source={source},target={target}"
    if readonly:
        spec += ",readonly"
    return spec


def _volume_mount(volume: str, target: str, *, readonly: bool) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,80}", volume):
        raise BackupError("Volume recusado.")
    spec = f"type=volume,source={volume},target={target}"
    if readonly:
        spec += ",readonly"
    return spec


def _pg_argv(docker: str, spec: dict, env: Mapping[str, str], tool: str) -> tuple[str, ...]:
    user = _ident(env, spec["user_env"], spec["default_user"])
    database = _ident(env, spec["db_env"], spec["default_db"])
    if tool == "pg_dump":
        tail = ("pg_dump", "-U", user, "--dbname", database, "--no-owner", "--format=custom")
    elif tool == "pg_restore":
        tail = (
            "pg_restore",
            "-U",
            user,
            "--dbname",
            database,
            "--clean",
            "--if-exists",
            "--no-owner",
        )
    else:
        raise BackupError("Comando de backup recusado.")
    return (docker, "exec", "-i", spec["container"], *tail)


def build_backup_plan(
    target_id: str,
    root: Path,
    env: Mapping[str, str],
    *,
    docker: str = "docker",
    stamp: str | None = None,
) -> list[PlannedCommand]:
    target_id = _known(target_id)
    docker = _docker(docker)
    stamp = _stamp(stamp or stamp_now())
    folder = backups_dir(root)
    filename = _backup_name(target_id, stamp)
    dest = folder / filename
    if target_id in _POSTGRES:
        spec = _POSTGRES[target_id]
        return [
            PlannedCommand(
                kind="exec",
                title=spec["title"],
                argv=_pg_argv(docker, spec, env, "pg_dump"),
                capture_path=str(dest),
            )
        ]
    if target_id == _N8N_ID:
        src = n8n_sqlite_path(root)
        if not src.is_file():
            raise BackupError("Não encontrei o SQLite do n8n em n8n/n8n/data/database.sqlite.")
        return [PlannedCommand(kind="copy", title="SQLite do n8n", src=str(src), dest=str(dest))]
    spec = _VOLUMES[target_id]
    argv = (
        docker,
        "run",
        "--rm",
        "--entrypoint",
        "tar",
        "--mount",
        _volume_mount(spec["volume"], "/source", readonly=True),
        "--mount",
        _mount(str(folder), "/backup"),
        _ARCHIVE_IMAGE,
        "-czf",
        f"/backup/{filename}",
        "-C",
        "/source",
        ".",
    )
    return [PlannedCommand(kind="exec", title=spec["title"], argv=argv)]


def build_restore_plan(
    target_id: str,
    root: Path,
    env: Mapping[str, str],
    *,
    source_name: str,
    confirmed: bool,
    docker: str = "docker",
) -> list[PlannedCommand]:
    if confirmed is not True:
        raise BackupError("Restauração cancelada: confirme a ação antes de restaurar.")
    target_id = _known(target_id)
    docker = _docker(docker)
    source = resolve_backup_file(root, target_id, source_name)
    if target_id in _POSTGRES:
        spec = _POSTGRES[target_id]
        return [
            _stop_start(docker, spec["stop"], "stop", f"Parar serviços do {spec['title']}"),
            PlannedCommand(
                kind="exec",
                title=spec["title"],
                argv=_pg_argv(docker, spec, env, "pg_restore"),
                stdin_path=str(source),
            ),
            _stop_start(docker, spec["stop"], "start", f"Subir serviços do {spec['title']}"),
        ]
    if target_id == _N8N_ID:
        dest = n8n_sqlite_path(root)
        return [
            _stop_start(docker, _N8N_STOP, "stop", "Parar o n8n"),
            PlannedCommand(kind="copy", title="Restaurar SQLite do n8n", src=str(source), dest=str(dest)),
            _stop_start(docker, _N8N_STOP, "start", "Subir o n8n"),
        ]
    spec = _VOLUMES[target_id]
    folder = backups_dir(root)
    argv = (
        docker,
        "run",
        "--rm",
        "--entrypoint",
        "tar",
        "--mount",
        _volume_mount(spec["volume"], "/source", readonly=False),
        "--mount",
        _mount(str(folder), "/backup", readonly=True),
        _ARCHIVE_IMAGE,
        "-xzf",
        f"/backup/{source.name}",
        "-C",
        "/source",
    )
    return [
        _stop_start(docker, spec["stop"], "stop", f"Parar serviços do {spec['title']}"),
        PlannedCommand(kind="exec", title=spec["title"], argv=argv),
        _stop_start(docker, spec["stop"], "start", f"Subir serviços do {spec['title']}"),
    ]


def _run_argv(argv: list[str], *, stdin: bytes | None = None) -> bytes:
    if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
        raise BackupError("Comando de backup recusado.")
    try:
        proc = subprocess.run(
            argv,
            shell=False,
            input=stdin,
            capture_output=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupError("A operação de backup excedeu o tempo limite.") from exc
    except FileNotFoundError:
        raise BackupError("Docker não está no PATH. Abra o Docker Desktop e tente de novo.") from None
    except OSError as exc:
        raise BackupError(f"Falha ao executar o Docker: {exc.strerror or 'erro do sistema'}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")
        detail = " ".join(detail.split())
        if len(detail) > 300:
            detail = detail[:300] + "…"
        raise BackupError(f"A operação falhou (código {proc.returncode}). {detail}".strip())
    return proc.stdout or b""


def execute_plan(commands: list[PlannedCommand], *, runner=None) -> None:
    from control_plane.actor import concluir, exigir

    exigir("backup")
    run = runner or _run_argv
    for cmd in commands:
        if cmd.kind == "copy":
            if not cmd.src or not cmd.dest:
                raise BackupError("Cópia de backup incompleta.")
            src = Path(cmd.src)
            dest = Path(cmd.dest)
            if not src.is_file():
                raise BackupError("Arquivo de origem não encontrado.")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            continue
        if cmd.kind != "exec":
            raise BackupError("Comando de backup recusado.")
        stdin = Path(cmd.stdin_path).read_bytes() if cmd.stdin_path else None
        data = run(list(cmd.argv), stdin=stdin)
        if cmd.capture_path:
            if not data:
                raise BackupError("O backup não devolveu dados.")
            dest = Path(cmd.capture_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    concluir("backup", "plano")
