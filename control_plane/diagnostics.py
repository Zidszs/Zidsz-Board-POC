"""Histórico de saúde, termômetro e pacote de diagnóstico.

O banco fica em `<raiz>/.n8groker/diagnostics.sqlite`. Essa pasta é gitignored:
não commitar o sqlite, o zip nem o cache.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Mapping, Sequence

from control_plane.operations import ALLOWED_COMPOSE, ALLOWED_CONTAINERS
from control_plane.support_chat import redact_text

RETENTION_DAYS = 7
MIN_BASELINE = 5
ROLLING = 40
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SAFE_FILE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_STATUSES = frozenset({"online", "degraded", "offline"})
_MARKER_KINDS = frozenset({"start", "restart"})
_TIME_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    service_id TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms REAL,
    time_to_healthy_ms REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_service ON samples(service_id, id);
CREATE TABLE IF NOT EXISTS markers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    service_id TEXT NOT NULL,
    kind TEXT NOT NULL
);
"""


class DiagnosticsError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Sample:
    recorded_at: str
    service_id: str
    status: str
    latency_ms: float | None
    time_to_healthy_ms: float | None


@dataclass(frozen=True)
class ThermoFlag:
    service_id: str
    metric: str
    latest: float
    median: float
    p90: float


def data_dir(root: Path) -> Path:
    path = (root / ".n8groker").resolve()
    if not path.is_relative_to(root.resolve()):
        raise DiagnosticsError("Pasta de dados fora do projeto.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path(root: Path) -> Path:
    return data_dir(root) / "diagnostics.sqlite"


def format_time(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(_TIME_FMT)


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, _TIME_FMT).replace(tzinfo=timezone.utc)


def percentile(values: Sequence[float], pct: float) -> float:
    ordered = sorted(float(item) for item in values)
    if not ordered:
        raise DiagnosticsError("Não há valores para a linha de base.")
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def median(values: Sequence[float]) -> float:
    return percentile(values, 50)


def rolling_baseline(values: Sequence[float], *, minimum: int = MIN_BASELINE) -> tuple[float, float] | None:
    """Mediana e p90 dos pontos anteriores ao último. Precisa de `minimum` anteriores."""
    if len(values) < minimum + 1:
        return None
    prior = list(values[:-1])[-ROLLING:]
    if len(prior) < minimum:
        return None
    return median(prior), percentile(prior, 90)


def is_slower_than_baseline(latest: float, prior: Sequence[float], *, minimum: int = MIN_BASELINE) -> bool:
    base = rolling_baseline([*prior, latest], minimum=minimum)
    if base is None:
        return False
    med, p90 = base
    return latest > p90 and latest > med


def flags_for_series(samples: Sequence[Sample], *, minimum: int = MIN_BASELINE) -> list[ThermoFlag]:
    if not samples:
        return []
    service_id = samples[-1].service_id
    found: list[ThermoFlag] = []
    latencies = [sample.latency_ms for sample in samples if sample.latency_ms is not None]
    latency_base = rolling_baseline(latencies, minimum=minimum)
    if latency_base is not None and is_slower_than_baseline(latencies[-1], latencies[:-1], minimum=minimum):
        med, p90 = latency_base
        found.append(ThermoFlag(service_id, "latencia", latencies[-1], med, p90))
    recoveries = [sample.time_to_healthy_ms for sample in samples if sample.time_to_healthy_ms is not None]
    recovery_base = rolling_baseline(recoveries, minimum=minimum)
    if recovery_base is not None and is_slower_than_baseline(recoveries[-1], recoveries[:-1], minimum=minimum):
        med, p90 = recovery_base
        found.append(ThermoFlag(service_id, "tempo_ate_saudavel", recoveries[-1], med, p90))
    return found


def _connect(root: Path) -> sqlite3.Connection:
    path = database_path(root)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _require_id(service_id: str) -> str:
    if not isinstance(service_id, str) or not _SAFE_ID.fullmatch(service_id):
        raise DiagnosticsError("Serviço recusado no histórico.")
    return service_id


def prune(conn: sqlite3.Connection, now: datetime, *, days: int = RETENTION_DAYS) -> None:
    cutoff = format_time(now - timedelta(days=days))
    conn.execute("DELETE FROM samples WHERE recorded_at < ?", (cutoff,))
    conn.execute("DELETE FROM markers WHERE recorded_at < ?", (cutoff,))


def note_marker(root: Path, service_id: str, kind: str, *, now: datetime | None = None) -> None:
    service_id = _require_id(service_id)
    if kind not in _MARKER_KINDS:
        raise DiagnosticsError("Marcador recusado.")
    moment = now or datetime.now(timezone.utc)
    with _connect(root) as conn:
        conn.execute(
            "INSERT INTO markers (recorded_at, service_id, kind) VALUES (?, ?, ?)",
            (format_time(moment), service_id, kind),
        )
        prune(conn, moment)


def _time_to_healthy_ms(conn: sqlite3.Connection, service_id: str, status: str, now: datetime) -> float | None:
    if status != "online":
        return None
    last = conn.execute(
        "SELECT status FROM samples WHERE service_id = ? ORDER BY id DESC LIMIT 1",
        (service_id,),
    ).fetchone()
    last_online = conn.execute(
        "SELECT recorded_at FROM samples WHERE service_id = ? AND status = 'online' ORDER BY id DESC LIMIT 1",
        (service_id,),
    ).fetchone()
    marker = conn.execute(
        "SELECT recorded_at FROM markers WHERE service_id = ? ORDER BY id DESC LIMIT 1",
        (service_id,),
    ).fetchone()
    anchors: list[str] = []
    if last is None or last["status"] != "online":
        if last_online is not None:
            first_down = conn.execute(
                "SELECT recorded_at FROM samples WHERE service_id = ? AND recorded_at > ? ORDER BY id ASC LIMIT 1",
                (service_id, last_online["recorded_at"]),
            ).fetchone()
            if first_down is not None:
                anchors.append(first_down["recorded_at"])
        elif last is not None:
            first = conn.execute(
                "SELECT recorded_at FROM samples WHERE service_id = ? ORDER BY id ASC LIMIT 1",
                (service_id,),
            ).fetchone()
            if first is not None:
                anchors.append(first["recorded_at"])
    if marker is not None:
        last_online_at = last_online["recorded_at"] if last_online is not None else ""
        if marker["recorded_at"] > last_online_at:
            anchors.append(marker["recorded_at"])
    if not anchors:
        return None
    anchor = max(anchors)
    elapsed = (now - parse_time(anchor)).total_seconds() * 1000
    if elapsed < 0:
        return None
    return elapsed


def record_results(
    root: Path,
    rows: Sequence[tuple[str, str, float | None]],
    *,
    now: datetime | None = None,
) -> list[float | None]:
    """Grava status, latência e o tempo até ficar saudável. Devolve esse tempo por linha."""
    moment = now or datetime.now(timezone.utc)
    recorded: list[float | None] = []
    with _connect(root) as conn:
        for service_id, status, latency_ms in rows:
            service_id = _require_id(service_id)
            if status not in _STATUSES:
                raise DiagnosticsError("Status recusado no histórico.")
            if latency_ms is not None and (isinstance(latency_ms, bool) or not isinstance(latency_ms, (int, float))):
                raise DiagnosticsError("Latência recusada no histórico.")
            if latency_ms is not None and latency_ms < 0:
                latency_ms = None
            time_to_healthy = _time_to_healthy_ms(conn, service_id, status, moment)
            conn.execute(
                """
                INSERT INTO samples (recorded_at, service_id, status, latency_ms, time_to_healthy_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (format_time(moment), service_id, status, latency_ms, time_to_healthy),
            )
            recorded.append(time_to_healthy)
        prune(conn, moment)
    return recorded


def load_series(root: Path, *, limit: int = 200) -> dict[str, list[Sample]]:
    if not database_path(root).is_file():
        return {}
    with _connect(root) as conn:
        found = conn.execute(
            """
            SELECT recorded_at, service_id, status, latency_ms, time_to_healthy_ms
            FROM samples
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    grouped: dict[str, list[Sample]] = {}
    for row in reversed(found):
        sample = Sample(
            recorded_at=row["recorded_at"],
            service_id=row["service_id"],
            status=row["status"],
            latency_ms=row["latency_ms"],
            time_to_healthy_ms=row["time_to_healthy_ms"],
        )
        grouped.setdefault(sample.service_id, []).append(sample)
    return grouped


def thermometer(series: Mapping[str, Sequence[Sample]], *, minimum: int = MIN_BASELINE) -> list[ThermoFlag]:
    flags: list[ThermoFlag] = []
    for service_id in sorted(series):
        flags.extend(flags_for_series(series[service_id], minimum=minimum))
    return flags


def _clean(text: object, env: Mapping[str, str]) -> str:
    return redact_text("" if text is None else str(text), env)


def assemble_diagnostic_zip(
    *,
    status_rows: Sequence[Mapping[str, object]],
    history_rows: Sequence[Mapping[str, object]],
    logs: Mapping[str, str],
    compose_ps: str,
    docker_version: str,
    ollama_version: str,
    env: Mapping[str, str],
) -> bytes:
    """Monta o zip já com a mesma redação do chat. Não inclui .env."""

    def dump(rows: Sequence[Mapping[str, object]], fields: tuple[str, ...]) -> str:
        payload = []
        for row in rows:
            item = {}
            for field in fields:
                value = row.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    item[field] = value
                else:
                    item[field] = _clean(value, env)
            payload.append(item)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    status_text = dump(status_rows, ("service_id", "status", "detail", "latency_ms", "recorded_at"))
    history_text = dump(
        history_rows,
        ("recorded_at", "service_id", "status", "latency_ms", "time_to_healthy_ms"),
    )
    versions = _clean(f"docker:\n{docker_version}\n\nollama:\n{ollama_version}\n", env)[:8000]
    compose_text = _clean(compose_ps, env)[:20000]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("status.json", status_text)
        archive.writestr("historico.json", history_text)
        archive.writestr("compose-ps.txt", compose_text)
        archive.writestr("versoes.txt", versions)
        for name in sorted(logs):
            if not _SAFE_FILE.fullmatch(name):
                continue
            archive.writestr(f"logs/{name}.log", _clean(logs[name], env)[-8000:])
    return buffer.getvalue()


def _default_command(argv: list[str]) -> str:
    if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
        raise DiagnosticsError("Comando de diagnóstico recusado.")
    try:
        proc = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "(comando indisponível)"
    parts = [part for part in ((proc.stdout or "").rstrip(), (proc.stderr or "").rstrip()) if part]
    text = "\n".join(parts)
    if proc.returncode != 0 and not text:
        return f"(código {proc.returncode})"
    return text or "(sem saída)"


def gather_diagnostic_zip(
    root: Path,
    status_rows: Sequence[Mapping[str, object]],
    env: Mapping[str, str],
    *,
    command=None,
    docker: str = "docker",
) -> bytes:
    from control_plane.actor import concluir, exigir

    exigir("diagnostico")
    run = command or _default_command
    logs: dict[str, str] = {}
    for name in sorted(ALLOWED_CONTAINERS):
        logs[name] = run([docker, "logs", "--tail", "80", name])
    chunks: list[str] = []
    for relative in sorted(ALLOWED_COMPOSE):
        chunks.append(f"## {relative}\n{run([docker, 'compose', '-f', str(root / relative), 'ps'])}")
    history = []
    for samples in load_series(root, limit=500).values():
        for sample in samples:
            history.append(
                {
                    "recorded_at": sample.recorded_at,
                    "service_id": sample.service_id,
                    "status": sample.status,
                    "latency_ms": sample.latency_ms,
                    "time_to_healthy_ms": sample.time_to_healthy_ms,
                }
            )
    pacote = assemble_diagnostic_zip(
        status_rows=status_rows,
        history_rows=history,
        logs=logs,
        compose_ps="\n\n".join(chunks),
        docker_version=run([docker, "version"]),
        ollama_version=run(["ollama", "--version"]),
        env=env,
    )
    concluir("diagnostico", "zip")
    return pacote
