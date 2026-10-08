"""Rota do Scout não aponta o túnel para o Porteiro, fora da entrada oficial."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.redirection_registry import PortaReservadaError, RedirectionRegistry  # noqa: E402
from scout.core.route_config import RouteConfig  # noqa: E402


def _registro(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    monkeypatch.setenv("SCOUT_UPSTREAM_PORT", "5677")
    monkeypatch.setenv("SCOUT_UPSTREAM_HOST", "host.docker.internal")
    return RedirectionRegistry(RouteConfig(), path=tmp_path / "redirections.json")


def test_porteiro_manual_segue_no_5677_e_o_resto_nao(tmp_path, monkeypatch):
    reg = _registro(tmp_path, monkeypatch)
    oficial = reg.get_by_id("porteiro-manual")
    assert oficial["enabled"] is True
    assert oficial["upstream_port"] == 5677
    assert oficial["listen_port"] == 4050

    with pytest.raises(PortaReservadaError):
        reg.update("porteiro-manual", upstream_port=5676)
    with pytest.raises(PortaReservadaError):
        reg.update("porteiro-manual", upstream_host="10.0.0.8")
    assert reg.get_by_id("porteiro-manual")["upstream_port"] == 5677

    with pytest.raises(PortaReservadaError):
        reg.add_manual("extra", "host.docker.internal", 5677)
    with pytest.raises(PortaReservadaError):
        reg.add_manual("extra", "host.docker.internal", 5676)
    with pytest.raises(PortaReservadaError):
        reg.add_manual("extra", "host.docker.internal", 5680, listen_port=5677)

    criada = reg.add_manual("extra", "host.docker.internal", 5680)
    assert criada["upstream_port"] == 5680
    with pytest.raises(PortaReservadaError):
        reg.update(criada["id"], upstream_port=5676)

    novas = reg.sync_docker(
        [
            {
                "name": "scout-backend",
                "published_ports": [{"host": 5677}, {"host": 5676}, {"host": 5681}],
            }
        ]
    )
    assert [item["upstream_port"] for item in novas] == [5681]
    alheias = [
        item
        for item in reg.list_all()
        if item.get("id") != "porteiro-manual" and item.get("upstream_port") in {5676, 5677}
    ]
    assert alheias == []
    assert reg.get_by_id("porteiro-manual")["enabled"] is True


def test_8501_nao_e_escuta_nem_upstream(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    monkeypatch.setenv("SCOUT_UPSTREAM_PORT", "8501")
    monkeypatch.setenv("SCOUT_UPSTREAM_HOST", "host.docker.internal")
    reg = RedirectionRegistry(RouteConfig(), path=tmp_path / "redirections.json")
    oficial = reg.get_by_id("porteiro-manual")
    assert oficial["enabled"] is True
    assert oficial["listen_port"] == 4050
    assert oficial["upstream_port"] == 5677

    with pytest.raises(PortaReservadaError, match="8501"):
        reg.add_manual("extra", "host.docker.internal", 8501)
    with pytest.raises(PortaReservadaError, match="8501"):
        reg.add_manual("extra", "host.docker.internal", 5680, listen_port=8501)
    with pytest.raises(PortaReservadaError, match="8501"):
        reg.update("porteiro-manual", upstream_port=8501)
    assert reg.get_by_id("porteiro-manual")["upstream_port"] == 5677

    criada = reg.add_manual("extra", "host.docker.internal", 5680)
    with pytest.raises(PortaReservadaError, match="8501"):
        reg.update(criada["id"], upstream_port=8501)
    with pytest.raises(PortaReservadaError, match="8501"):
        reg.update(criada["id"], listen_port=8501)
    assert reg.get_by_id(criada["id"])["upstream_port"] == 5680

    novas = reg.sync_docker(
        [{"name": "painel", "published_ports": [{"host": 8501}, {"host": 5682}]}]
    )
    assert [item["upstream_port"] for item in novas] == [5682]
    assert all(item.get("upstream_port") != 8501 for item in reg.list_all())


def test_reconcile_com_8501_mantem_o_destino_que_ja_era_seguro(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    monkeypatch.setenv("SCOUT_UPSTREAM_PORT", "5688")
    monkeypatch.setenv("SCOUT_UPSTREAM_HOST", "host.docker.internal")
    path = tmp_path / "redirections.json"
    reg = RedirectionRegistry(RouteConfig(), path=path)
    assert reg.get_by_id("porteiro-manual")["upstream_port"] == 5688

    monkeypatch.setenv("SCOUT_UPSTREAM_PORT", "8501")
    recarregado = RedirectionRegistry(RouteConfig(), path=path)
    assert recarregado.get_by_id("porteiro-manual")["upstream_port"] == 5688
    assert recarregado.get_by_id("porteiro-manual")["listen_port"] == 4050

    dados = json.loads(path.read_text(encoding="utf-8"))
    for entry in dados["entries"]:
        if entry["id"] == "porteiro-manual":
            entry["upstream_port"] = 8501
            entry["listen_port"] = 8501
        else:
            entry["enabled"] = True
            entry["upstream_port"] = 8501
    dados["entries"].append(
        {
            "id": "manual-painel",
            "source": "manual",
            "name": "painel",
            "listen_port": 4061,
            "upstream_host": "host.docker.internal",
            "upstream_port": 8501,
            "mode": "tcp",
            "enabled": True,
            "is_new": False,
        }
    )
    path.write_text(json.dumps(dados), encoding="utf-8")
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "8501")
    de_novo = RedirectionRegistry(RouteConfig(), path=path)
    oficial = de_novo.get_by_id("porteiro-manual")
    assert oficial["enabled"] is True
    assert oficial["upstream_port"] == 5677
    assert oficial["listen_port"] == 4050
    painel = de_novo.get_by_id("manual-painel")
    assert painel["enabled"] is False
    assert painel["upstream_port"] == 8501


def test_proxima_escuta_pula_8501(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_PORT", "4050")
    monkeypatch.setenv("SCOUT_UPSTREAM_PORT", "5677")
    monkeypatch.setenv("SCOUT_UPSTREAM_HOST", "host.docker.internal")
    monkeypatch.setenv("SCOUT_LISTEN_PORT_START", "8501")
    reg = RedirectionRegistry(RouteConfig(), path=tmp_path / "redirections.json")
    criada = reg.add_manual("extra", "host.docker.internal", 5680)
    assert criada["listen_port"] == 8502


def test_api_8765_fica_no_loopback():
    texto = (ROOT / "Scout_OSINT_Docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert '127.0.0.1:${SCOUT_ADMIN_PORT:-8765}:8765' in texto
    assert '"127.0.0.1:${SCOUT_PUBLIC_PORT:-4050}:${SCOUT_PUBLIC_PORT:-4050}"' in texto
