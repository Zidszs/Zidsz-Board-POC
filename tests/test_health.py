import socket
import time
import urllib.error
import urllib.request

from control_plane.config import ServiceSpec
from control_plane.health import BADGES, DEGRADED, OFFLINE, ONLINE, check_services, classify, probe


def _spec(url="http://127.0.0.1:9/health", expected=(200,), service_id="demo"):
    return ServiceSpec(id=service_id, title=service_id, health_url=url, link_url=url, expected_statuses=expected)


def test_selos_dizem_fora_do_ar_em_portugues():
    assert BADGES[ONLINE] == "🟢 No ar"
    assert BADGES[OFFLINE] == "🔴 Fora do ar"
    assert BADGES[DEGRADED] == "🟡 Resposta estranha"
    assert "Offline" not in BADGES[OFFLINE]


def test_classify_online_degraded_and_offline():
    state, detail = classify(200, 120, expected=(200,), slow_ms=1000, error=None)
    assert state == ONLINE
    assert "200" in detail

    state, detail = classify(503, 50, expected=(200,), slow_ms=1000, error=None)
    assert state == DEGRADED
    assert "503" in detail

    state, detail = classify(200, 1800, expected=(200,), slow_ms=1000, error=None)
    assert state == DEGRADED
    assert "1800" in detail

    state, detail = classify(204, 10, expected=(204,), slow_ms=1000, error=None)
    assert state == ONLINE

    state, detail = classify(None, None, expected=(200,), slow_ms=1000, error="Tempo esgotado na checagem.")
    assert state == OFFLINE


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return self.status


def test_probe_success_and_expected_204(monkeypatch):
    monkeypatch.setattr("control_plane.health._http_open", lambda request, timeout: _Resp(200))
    result = probe(_spec(), timeout=1, slow_ms=1000)
    assert result.state == ONLINE
    assert result.http_status == 200

    monkeypatch.setattr("control_plane.health._http_open", lambda request, timeout: _Resp(204))
    result = probe(_spec(expected=(204,)), timeout=1, slow_ms=1000)
    assert result.state == ONLINE
    assert result.http_status == 204


def test_probe_http_error_is_degraded_not_a_crash(monkeypatch):
    def boom(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "no", hdrs=None, fp=None)

    monkeypatch.setattr("control_plane.health._http_open", boom)
    result = probe(_spec(), timeout=1, slow_ms=1000)
    assert result.state == DEGRADED
    assert result.http_status == 503


def test_probe_timeout_and_connection_refused(monkeypatch):
    def timeout(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("control_plane.health._http_open", timeout)
    result = probe(_spec(), timeout=1, slow_ms=1000)
    assert result.state == OFFLINE
    assert "Tempo esgotado" in result.detail

    def refused(request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr("control_plane.health._http_open", refused)
    result = probe(_spec(), timeout=1, slow_ms=1000)
    assert result.state == OFFLINE
    assert "recusada" in result.detail


def test_probe_unexpected_error_stays_friendly(monkeypatch):
    def broken(request, timeout):
        raise RuntimeError("trace secreto\nlinha 2")

    monkeypatch.setattr("control_plane.health._http_open", broken)
    result = probe(_spec(), timeout=1, slow_ms=1000)
    assert result.state == OFFLINE
    assert "trace secreto" not in result.detail


def test_probe_slow_success_is_degraded(monkeypatch):
    def slow(request, timeout):
        time.sleep(0.05)
        return _Resp(200)

    monkeypatch.setattr("control_plane.health._http_open", slow)
    result = probe(_spec(), timeout=1, slow_ms=1)
    assert result.state == DEGRADED


def test_check_services_keeps_input_order(monkeypatch):
    def fake_open(request, timeout):
        if "slow" in request.full_url:
            time.sleep(0.05)
        return _Resp(200)

    monkeypatch.setattr("control_plane.health._http_open", fake_open)
    services = [
        _spec("http://127.0.0.1/slow", service_id="slow"),
        _spec("http://127.0.0.1/fast", service_id="fast"),
    ]
    started = time.perf_counter()
    results = check_services(services, timeout=2, slow_ms=5000)
    elapsed = time.perf_counter() - started
    assert [item.service_id for item in results] == ["slow", "fast"]
    assert all(item.state == ONLINE for item in results)
    assert elapsed < 0.4


def test_socket_timeout_inside_urlerror(monkeypatch):
    def boom(request, timeout):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr("control_plane.health._http_open", boom)
    result = probe(_spec(), timeout=1, slow_ms=1000)
    assert result.state == OFFLINE
    assert "Tempo esgotado" in result.detail
