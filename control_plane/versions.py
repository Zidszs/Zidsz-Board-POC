"""Aviso de tag mais nova. Nunca altera compose nem faz pull.

A consulta fica em `.n8groker/version-cache.json` por 12 horas. Sem rede,
o painel segue com o cache antigo ou sem aviso.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

CACHE_TTL = timedelta(hours=12)
_SEMVER = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?$")
_IMAGE_LINE = re.compile(r"(?m)^\s*image:\s*[\"']?(\S+?)[\"']?\s*$")
_COMPOSE = (
    "llm/docker-compose.yml",
    "n8n/docker-compose.yml",
    "ngrok/docker-compose.yml",
)


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class ImageRef:
    raw: str
    registry: str
    name: str
    tag: str | None
    digest: str | None


@dataclass(frozen=True)
class VersionNotice:
    image: str
    current: str
    newer: str
    message: str


@dataclass(frozen=True)
class VersionReport:
    ok: bool
    notices: tuple[VersionNotice, ...]
    notes: tuple[str, ...]


def version_tuple(tag: str) -> tuple[int, ...] | None:
    if not isinstance(tag, str):
        return None
    match = _SEMVER.fullmatch(tag.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def is_newer(candidate: str, current: str) -> bool:
    left = version_tuple(candidate)
    right = version_tuple(current)
    if left is None or right is None:
        return False
    return left > right


def select_newer_tag(current: str, tags: Sequence[str]) -> str | None:
    newer = [tag for tag in tags if isinstance(tag, str) and is_newer(tag, current)]
    if not newer:
        return None
    return max(newer, key=lambda tag: version_tuple(tag) or ())


def parse_image(ref: str) -> ImageRef:
    raw = ref.strip()
    digest = None
    body = raw
    if "@" in raw:
        body, digest = raw.rsplit("@", 1)
    name = body
    tag = None
    if ":" in body:
        left, right = body.rsplit(":", 1)
        if "/" not in right:
            name, tag = left, right
    registry, repo = _split_registry(name)
    return ImageRef(raw=raw, registry=registry, name=repo, tag=tag, digest=digest)


def _split_registry(name: str) -> tuple[str, str]:
    first, _sep, rest = name.partition("/")
    if "." in first or first == "localhost":
        registry = first
        repo = rest
    else:
        registry = "docker.io"
        repo = name
    if registry in {"docker.io", "index.docker.io"} and repo and "/" not in repo:
        repo = f"library/{repo}"
    return registry, repo


def extract_images(compose_text: str) -> list[str]:
    return _IMAGE_LINE.findall(compose_text)


def pins_from_root(root: Path) -> list[ImageRef]:
    found: list[ImageRef] = []
    for relative in _COMPOSE:
        text = (root / relative).read_text(encoding="utf-8")
        found.extend(parse_image(raw) for raw in extract_images(text))
    return found


def _comparable(pin: ImageRef) -> bool:
    return pin.digest is None and version_tuple(pin.tag or "") is not None


def _compute(pins: Sequence[ImageRef], fetch_tags: Callable[[ImageRef], list[str]]) -> VersionReport:
    notices: list[VersionNotice] = []
    notes: list[str] = []
    checked = 0
    failures = 0
    for pin in pins:
        if pin.digest:
            notes.append(f"{pin.raw} está pinada por digest. O painel não compara tag e não atualiza sozinho.")
            continue
        if not _comparable(pin):
            shown = pin.tag or "sem tag"
            notes.append(f"{pin.raw} usa a tag {shown}. Não há versão numérica pinada para comparar. O painel não troca a tag.")
            continue
        checked += 1
        try:
            tags = fetch_tags(pin)
        except Exception:
            failures += 1
            continue
        newer = select_newer_tag(pin.tag or "", tags)
        if newer is None:
            continue
        notices.append(
            VersionNotice(
                image=pin.raw,
                current=pin.tag or "",
                newer=newer,
                message=(
                    f"{pin.name} está em {pin.tag} e o registro tem {newer}. "
                    "O painel não atualiza sozinho."
                ),
            )
        )
    if checked and failures == checked:
        raise RegistryError("consulta indisponível")
    return VersionReport(ok=True, notices=tuple(notices), notes=tuple(notes))


def cache_path(root: Path) -> Path:
    return root / ".n8groker" / "version-cache.json"


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _write_cache(path: Path, now: datetime, report: VersionReport) -> None:
    payload = {
        "checked_at": _stamp(now),
        "ok": report.ok,
        "notices": [
            {"image": item.image, "current": item.current, "newer": item.newer, "message": item.message}
            for item in report.notices
        ],
        "notes": list(report.notes),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_cached_report(root: Path) -> tuple[datetime, VersionReport] | None:
    return _read_cache_file(cache_path(root))


def _read_cache_file(path: Path) -> tuple[datetime, VersionReport] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    checked = _parse_stamp(str(payload.get("checked_at", "")))
    if checked is None:
        return None
    notices = []
    for item in payload.get("notices") or []:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "")
        notices.append(
            VersionNotice(
                image=str(item.get("image") or ""),
                current=str(item.get("current") or ""),
                newer=str(item.get("newer") or ""),
                message=message,
            )
        )
    notes = tuple(str(item) for item in (payload.get("notes") or []) if isinstance(item, str))
    report = VersionReport(ok=bool(payload.get("ok")), notices=tuple(notices), notes=notes)
    return checked, report


def check_versions(
    pins: Sequence[ImageRef],
    *,
    fetch_tags: Callable[[ImageRef], list[str]],
    now: datetime,
    cache_file: Path,
    ttl: timedelta = CACHE_TTL,
) -> VersionReport:
    cached = _read_cache_file(cache_file)
    if cached is not None and now - cached[0] < ttl:
        return cached[1]
    try:
        report = _compute(pins, fetch_tags)
    except RegistryError:
        if cached is not None:
            return cached[1]
        return VersionReport(ok=False, notices=(), notes=())
    if report.ok:
        try:
            _write_cache(cache_file, now, report)
        except OSError:
            pass
    return report


def load_version_report(
    root: Path,
    *,
    fetch_tags: Callable[[ImageRef], list[str]] | None = None,
    now: datetime | None = None,
) -> VersionReport:
    moment = now or datetime.now(timezone.utc)
    fetcher = fetch_tags or fetch_registry_tags
    try:
        pins = pins_from_root(root)
    except OSError:
        return VersionReport(ok=False, notices=(), notes=())
    try:
        return check_versions(pins, fetch_tags=fetcher, now=moment, cache_file=cache_path(root))
    except Exception:
        return VersionReport(ok=False, notices=(), notes=())


def ensure_version_check(root: Path) -> None:
    """Dispara a consulta fora da renderização. Falha de rede fica em silêncio."""

    def work() -> None:
        try:
            load_version_report(root)
        except Exception:
            return

    threading.Thread(target=work, name="n8groker-versoes", daemon=True).start()


def _get_json(url: str, timeout: float, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "N8GrokerControlPlane/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(1_000_000)
    except Exception as exc:
        raise RegistryError("consulta falhou") from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError("json inválido") from exc
    if not isinstance(payload, dict):
        raise RegistryError("resposta inválida")
    return payload


def fetch_registry_tags(pin: ImageRef, *, timeout: float = 5) -> list[str]:
    if pin.registry == "ghcr.io":
        token_payload = _get_json(
            f"https://ghcr.io/token?service=ghcr.io&scope=repository:{pin.name}:pull",
            timeout,
        )
        token = token_payload.get("token")
        if not isinstance(token, str) or not token:
            raise RegistryError("sem token")
        payload = _get_json(
            f"https://ghcr.io/v2/{pin.name}/tags/list",
            timeout,
            headers={"Authorization": f"Bearer {token}"},
        )
        tags = payload.get("tags")
    elif pin.registry in {"docker.io", "index.docker.io"}:
        payload = _get_json(
            f"https://hub.docker.com/v2/repositories/{pin.name}/tags?page_size=100&ordering=last_updated",
            timeout,
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise RegistryError("resposta sem tags")
        tags = [item.get("name") for item in results if isinstance(item, dict)]
    else:
        raise RegistryError("registro sem consulta")
    if not isinstance(tags, list):
        raise RegistryError("resposta sem tags")
    return [tag for tag in tags if isinstance(tag, str)]
