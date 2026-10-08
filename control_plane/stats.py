"""CPU e memória por container, via docker stats --no-stream.

A UI guarda o resultado por alguns segundos. Uma leitura falha não derruba
o painel: devolve a anterior, ou lista vazia.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable


STATS_TTL_SECONDS = 20


@dataclass(frozen=True)
class ContainerStat:
    name: str
    cpu: str
    memory: str


def stats_argv(docker: str = "docker") -> list[str]:
    if not isinstance(docker, str) or not docker.strip():
        raise ValueError("Executável do Docker inválido.")
    return [docker, "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"]


def parse_docker_stats(text: str) -> list[ContainerStat]:
    rows: list[ContainerStat] = []
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, cpu, memory = (part.strip() for part in parts[:3])
        if not name or name.upper() == "NAME":
            continue
        rows.append(ContainerStat(name=name, cpu=cpu, memory=memory))
    return rows


def _default_runner(argv: list[str], timeout: float) -> str:
    proc = subprocess.run(
        argv,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise OSError("docker stats falhou")
    return proc.stdout or ""


def read_docker_stats(*, docker: str = "docker", timeout: float = 8, runner: Callable | None = None) -> list[ContainerStat]:
    run = runner or _default_runner
    text = run(stats_argv(docker), timeout)
    return parse_docker_stats(text)


def cached_stats(now: float, cache: dict | None, loader: Callable[[], list[ContainerStat]], *, ttl: float = STATS_TTL_SECONDS):
    """Devolve (linhas, cache). Dentro do prazo não chama o loader."""
    if cache and now - float(cache.get("at", 0)) < ttl:
        return list(cache.get("rows") or []), cache
    try:
        rows = list(loader())
    except Exception:
        previous = list(cache.get("rows") or []) if cache else []
        return previous, {"at": now, "rows": previous}
    fresh = {"at": now, "rows": rows}
    return rows, fresh
