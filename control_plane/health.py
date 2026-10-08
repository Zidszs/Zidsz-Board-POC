"""Checagens HTTP concorrentes.

Critério:
- online: status esperado e latência até o limite
- degradado: respondeu, mas o status não é o esperado ou passou do limite
- offline: sem conexão ou tempo esgotado

Nada aqui levanta stack trace para a UI. Falha vira estado offline.
"""

from __future__ import annotations

import logging
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from control_plane.config import ServiceSpec

logger = logging.getLogger("control_plane.health")

ONLINE = "online"
OFFLINE = "offline"
DEGRADED = "degraded"

BADGES = {
    ONLINE: "🟢 No ar",
    OFFLINE: "🔴 Fora do ar",
    DEGRADED: "🟡 Resposta estranha",
}


@dataclass(frozen=True)
class HealthResult:
    service_id: str
    state: str
    detail: str
    http_status: int | None
    latency_ms: float | None


def classify(
    status_code: int | None,
    latency_ms: float | None,
    *,
    expected: tuple[int, ...],
    slow_ms: float,
    error: str | None,
) -> tuple[str, str]:
    if error:
        return OFFLINE, error
    if status_code is None:
        return OFFLINE, "Sem resposta HTTP."
    if status_code not in expected:
        expected_label = "/".join(str(code) for code in expected)
        return DEGRADED, f"Resposta HTTP {status_code} (esperado {expected_label})."
    if latency_ms is not None and latency_ms > slow_ms:
        return DEGRADED, f"Resposta lenta ({int(latency_ms)} ms)."
    elapsed = int(latency_ms or 0)
    return ONLINE, f"HTTP {status_code} em {elapsed} ms."


def _timeout_message() -> str:
    return "Tempo esgotado na checagem."


def _urlerror_message(exc: urllib.error.URLError) -> str:
    reason = exc.reason
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return _timeout_message()
    text = str(reason).lower()
    if "timed out" in text or "timeout" in text:
        return _timeout_message()
    if "certificate" in text or "ssl" in text:
        return "Falha de certificado TLS."
    if "connection refused" in text or "errno 111" in text or "10061" in text:
        return "Conexão recusada. O serviço parece parado."
    return "Sem conexão com o serviço."


def _http_open(request: urllib.request.Request, timeout: float):
    # Proxy do ambiente não pode desviar o probe de localhost.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def probe(service: ServiceSpec, timeout: float, slow_ms: float) -> HealthResult:
    started = time.perf_counter()
    request = urllib.request.Request(
        service.health_url,
        method="GET",
        headers={"User-Agent": "N8GrokerControlPlane/1.0", "Accept": "*/*"},
    )
    status_code: int | None = None
    error: str | None = None
    try:
        with _http_open(request, timeout) as response:
            status_code = getattr(response, "status", None) or response.getcode()
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        try:
            exc.close()
        except Exception:
            logger.debug("falha ao fechar HTTPError de %s", service.id)
    except (TimeoutError, socket.timeout):
        error = _timeout_message()
    except urllib.error.URLError as exc:
        error = _urlerror_message(exc)
    except Exception:
        logger.warning("checagem de %s falhou (%s)", service.id, "erro inesperado")
        error = "Não foi possível consultar este serviço."

    latency_ms = None if error else (time.perf_counter() - started) * 1000
    state, detail = classify(
        status_code,
        latency_ms,
        expected=service.expected_statuses,
        slow_ms=slow_ms,
        error=error,
    )
    return HealthResult(
        service_id=service.id,
        state=state,
        detail=detail,
        http_status=status_code,
        latency_ms=latency_ms,
    )


def check_services(
    services: tuple[ServiceSpec, ...] | list[ServiceSpec],
    timeout: float,
    slow_ms: float,
) -> list[HealthResult]:
    """Preserva a ordem dos serviços monitorados. As consultas correm em paralelo.

    Card sem porta no host (`monitored=False`) fica de fora: um HTTP local
    sempre falharia e pintaria a peça de vermelho.
    """
    specs = [spec for spec in services if spec.monitored]
    if not specs:
        return []
    results: list[HealthResult | None] = [None] * len(specs)
    workers = min(8, len(specs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe, spec, timeout, slow_ms): index for index, spec in enumerate(specs)}
        for future in as_completed(futures):
            index = futures[future]
            spec = specs[index]
            try:
                results[index] = future.result()
            except Exception:
                logger.warning("future de health falhou para %s", spec.id)
                results[index] = HealthResult(
                    service_id=spec.id,
                    state=OFFLINE,
                    detail="Não foi possível consultar este serviço.",
                    http_status=None,
                    latency_ms=None,
                )
    return [item for item in results if item is not None]
