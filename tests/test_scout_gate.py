"""Cliente HTTP da aba Scout: mesmos caminhos da API, sem a janela Tk."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from control_plane.scout_client import (
    GUI_PARITY,
    GateError,
    ScoutGate,
    coerce_managed_upstream,
    describe_snapshot,
    parse_upstream,
    query_text,
    toggle_decision,
)
from control_plane.scout_panel import commit_scout_action
from scout.gate import refusal_text

ROOT = Path(__file__).resolve().parents[1]


def _gate(transport):
    return ScoutGate("ws://127.0.0.1:8765/ws", transport=transport)


def test_acoes_usam_os_caminhos_http():
    calls = []

    def transport(method, url, payload, timeout):
        calls.append((method, url, payload, timeout))
        return {"ok": True, "entries": []}

    gate = _gate(transport)
    assert gate.base == "http://127.0.0.1:8765"
    gate.toggle("porteiro-manual", True)
    gate.patch("rota-manual", {"mode": "https", "listen_port": 4050, "name": "extra"})
    gate.delete("rota-manual")
    gate.add_manual("extra", 5680)
    gate.sync_docker()
    gate.firewall_sync()
    gate.set_alias("10.0.0.8", "casa")
    gate.block_ip("10.0.0.8")
    gate.unblock_ip("10.0.0.8")
    gate.remove_client("10.0.0.8")
    paths = [(method, url.split("8765", 1)[1]) for method, url, _payload, _timeout in calls]
    assert paths == [
        ("POST", "/redirections/toggle"),
        ("PATCH", "/redirections/rota-manual"),
        ("DELETE", "/redirections/rota-manual"),
        ("POST", "/redirections/manual"),
        ("POST", "/redirections/sync"),
        ("POST", "/firewall/sync"),
        ("POST", "/aliases"),
        ("POST", "/blocklist"),
        ("DELETE", "/blocklist/10.0.0.8"),
        ("DELETE", "/clients/10.0.0.8"),
    ]
    assert calls[0][2] == {"id": "porteiro-manual", "enabled": True}
    assert calls[3][2]["enabled"] is False
    assert calls[3][2]["upstream_host"] == "host.docker.internal"


def test_snapshot_le_health_rotas_trafego_e_clientes():
    vistos = []

    def transport(method, url, payload, timeout):
        vistos.append(url)
        if url.endswith("/health"):
            return {"public_port": 4050, "admin_port": 8765, "summary": {"active": 1}}
        if url.endswith("/redirections"):
            return {"entries": [{"id": "porteiro-manual"}], "summary": {"total": 1}}
        if url.endswith("/traffic"):
            return {"traffic": [{"client_ip": "10.0.0.1"}], "alerts": []}
        if url.endswith("/clients"):
            return {"clients": [{"ip": "10.0.0.1"}]}
        raise AssertionError(url)

    snap = _gate(transport).snapshot()
    assert [url.rsplit("/", 1)[-1] for url in vistos] == ["health", "redirections", "traffic", "clients"]
    assert snap["entries"][0]["id"] == "porteiro-manual"
    assert "10.0.0.1" in describe_snapshot("scout_trafego", snap)


def test_scout_fora_do_ar_nao_vaza_traceback():
    def transport(method, url, payload, timeout):
        raise OSError("connection refused")

    with pytest.raises(GateError) as exc:
        _gate(transport).health()
    assert "8765" in exc.value.message
    assert "4050" in exc.value.message
    assert "Traceback" not in exc.value.message
    texto = query_text("scout_resumo", transport=transport)
    assert "8765" in texto
    assert "Traceback" not in texto


def test_recusa_id_e_upstream_invalidos():
    calls = []

    def transport(method, url, payload, timeout):
        calls.append(url)
        return {}

    gate = _gate(transport)
    with pytest.raises(GateError):
        gate.toggle("../etc/passwd", True)
    with pytest.raises(GateError):
        gate.block_ip("1.2.3.4; rm")
    with pytest.raises(GateError):
        parse_upstream("sem-porta")
    with pytest.raises(GateError):
        gate.patch("rota", {"mode": "ftp"})
    assert calls == []


def test_regras_da_janela_para_n8n_ngrok_e_localhost():
    assert toggle_decision("docker-n8n_app", True, managed=True) == "confirm_n8n"
    assert toggle_decision("ngrok_service", True, managed=True) == "reject_ngrok"
    assert toggle_decision("porteiro-manual", True, managed=True) == "allow"
    host, port, aviso = coerce_managed_upstream("127.0.0.1", 5677, managed=True)
    assert (host, port) == ("host.docker.internal", 5677)
    assert aviso
    host, port, aviso = coerce_managed_upstream("127.0.0.1", 5677, managed=False)
    assert host == "127.0.0.1" and aviso is None


def test_api_recusada_vira_mensagem():
    def transport(method, url, payload, timeout):
        return {"ok": False, "error": "rota protegida"}

    with pytest.raises(GateError) as exc:
        _gate(transport).sync_docker()
    assert exc.value.message == "O Scout recusou a operação: rota protegida"


def test_recusa_prefere_reason_depois_error_e_detail():
    assert refusal_text({"ok": False, "reason": "  iptables indisponível ou desactivado  ", "error": "outro"}) == (
        "O Scout recusou a operação: iptables indisponível ou desactivado"
    )
    assert refusal_text({"ok": False, "reason": "  ", "error": "rota protegida", "detail": "porta"}) == (
        "O Scout recusou a operação: rota protegida"
    )
    assert refusal_text({"ok": False, "detail": "porta ocupada"}) == "O Scout recusou a operação: porta ocupada"
    assert refusal_text({"ok": False, "reason": 12, "error": None}) == "O Scout recusou a operação."
    assert refusal_text("nao-dict") == "O Scout recusou a operação."

    def firewall(method, url, payload, timeout):
        assert method == "POST" and url.endswith("/firewall/sync")
        return {"ok": False, "reason": "iptables indisponível ou desactivado"}

    with pytest.raises(GateError) as exc:
        _gate(firewall).firewall_sync()
    assert exc.value.message == "O Scout recusou a operação: iptables indisponível ou desactivado"

    def detalhe(method, url, payload, timeout):
        return {"ok": False, "detail": "conflito de porta"}

    with pytest.raises(GateError) as exc:
        _gate(detalhe).set_alias("10.0.0.8", "casa")
    assert exc.value.message == "O Scout recusou a operação: conflito de porta"


def _estado_http():
    estado = {
        "clients": [{"ip": "10.0.0.8", "alias": ""}],
        "entries": [{"id": "porteiro-manual", "enabled": True, "mode": "tcp"}],
    }

    def transport(method, url, payload, timeout):
        path = url.split("8765", 1)[1]
        if method == "POST" and path == "/aliases":
            estado["clients"] = [{"ip": payload["ip"], "alias": payload["alias"]}]
            return {"ok": True}
        if method == "POST" and path == "/redirections/manual":
            estado["entries"] = estado["entries"] + [
                {"id": "extra", "name": payload["name"], "enabled": False, "mode": payload["mode"]}
            ]
            return {"ok": True}
        if method == "POST" and path == "/firewall/sync":
            return {"ok": False, "reason": "iptables indisponível ou desactivado"}
        if method == "GET" and path == "/health":
            return {"public_port": 4050, "summary": {"active": 1, "total": len(estado["entries"])}}
        if method == "GET" and path == "/redirections":
            return {"entries": list(estado["entries"]), "summary": {"total": len(estado["entries"])}}
        if method == "GET" and path == "/traffic":
            return {"traffic": [], "alerts": []}
        if method == "GET" and path == "/clients":
            return {"clients": list(estado["clients"])}
        if method in {"POST", "PATCH", "DELETE"}:
            return {"ok": True}
        raise AssertionError((method, path))

    return estado, transport


def test_alias_e_porta_manual_invalidam_o_snapshot():
    _estado, transport = _estado_http()
    gate = _gate(transport)
    session = {
        "scout_snap": {"clients": [{"ip": "10.0.0.8", "alias": ""}], "entries": [{"id": "porteiro-manual"}]},
        "scout_down": "antigo",
        "scout_health_text": "antigo",
        "scout_log": [],
    }
    message = commit_scout_action(gate, {"kind": "alias", "ip": "10.0.0.8", "alias": "casa"}, session)
    assert message == "Alias gravado."
    assert "scout_snap" not in session
    assert "scout_down" not in session
    assert "scout_health_text" not in session
    assert session["scout_log"][0] == "Alias gravado."
    fresco = gate.snapshot()
    assert fresco["clients"][0]["alias"] == "casa"
    assert fresco["entries"][0]["id"] == "porteiro-manual"

    session["scout_snap"] = fresco
    message = commit_scout_action(
        gate,
        {"kind": "manual", "name": "extra", "upstream_port": 5680, "upstream_host": "host.docker.internal"},
        session,
    )
    assert message == "Porta manual incluída, desligada."
    assert "scout_snap" not in session
    fresco = gate.snapshot()
    assert fresco["entries"][-1]["name"] == "extra"
    assert fresco["entries"][-1]["enabled"] is False


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "toggle", "id": "porteiro-manual", "enabled": False},
        {"kind": "patch", "id": "rota-manual", "fields": {"mode": "https"}},
        {"kind": "delete", "id": "rota-manual"},
        {"kind": "block", "ip": "10.0.0.8"},
        {"kind": "unblock", "ip": "10.0.0.8"},
        {"kind": "remove_client", "ip": "10.0.0.8"},
        {"kind": "sync_docker"},
        {"kind": "firewall"},
    ],
)
def test_acao_que_muda_estado_descarta_listas_em_cache(action):
    def transport(method, url, payload, timeout):
        return {"ok": True}

    session = {"scout_snap": {"stale": True}, "scout_log": []}
    commit_scout_action(_gate(transport), action, session)
    assert "scout_snap" not in session
    assert session["scout_log"]


def test_firewall_recusado_mantem_as_listas():
    _estado, transport = _estado_http()
    session = {
        "scout_snap": {"entries": [{"id": "porteiro-manual"}], "clients": [{"ip": "10.0.0.8"}]},
        "scout_log": ["linha anterior"],
    }
    with pytest.raises(GateError) as exc:
        commit_scout_action(_gate(transport), {"kind": "firewall"}, session)
    assert exc.value.message == "O Scout recusou a operação: iptables indisponível ou desactivado"
    assert session["scout_snap"]["entries"][0]["id"] == "porteiro-manual"
    assert session["scout_log"] == ["linha anterior"]


def test_confirmacao_pede_releitura_depois_da_acao():
    painel = (ROOT / "control_plane" / "scout_panel.py").read_text(encoding="utf-8")
    assert 'session.pop("scout_snap", None)' in painel
    confirmacao = painel.split('key="scout_confirm"', 1)[1].split("with no:", 1)[0]
    assert "_run_pending" in confirmacao
    assert "st.rerun()" in confirmacao


def test_paridade_cobre_controles_e_explica_o_que_ficou_de_fora():
    texto = " ".join(row["gui"] + row["painel"] for row in GUI_PARITY)
    for trecho in (
        "Sync Docker",
        "Porta manual",
        "Sync Firewall",
        "Combobox de modo",
        "n8n_app",
        "ngrok_service",
        "Apagar",
        "Tráfego",
        "Clientes",
        "Config",
        "Desbloquear",
        "Tick WebSocket",
        "Bipe",
        "Fechar a janela",
    ):
        assert trecho in texto
    for row in GUI_PARITY:
        assert row["gui"] and row["painel"]
        assert "ported" in row
        if not row["ported"]:
            assert row.get("motivo")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Paridade da aba Scout" in readme
    for row in GUI_PARITY:
        if not row["ported"]:
            assert row["gui"] in readme


def test_rota_manual_nao_aponta_para_o_porteiro():
    gate = _gate(lambda method, url, payload, timeout: {"ok": True})
    for porta in (5676, 5677):
        with pytest.raises(GateError, match="Porteiro"):
            gate.add_manual("extra", porta)
        with pytest.raises(GateError, match="Porteiro"):
            gate.patch("rota-extra", {"upstream_port": porta})
        with pytest.raises(GateError, match="Porteiro"):
            gate.patch("rota-extra", {"listen_port": porta})


def test_rota_manual_nao_aponta_para_8501():
    gate = _gate(lambda method, url, payload, timeout: {"ok": True})
    with pytest.raises(GateError, match="8501"):
        gate.add_manual("extra", 8501)
    with pytest.raises(GateError, match="8501"):
        gate.patch("porteiro-manual", {"upstream_port": 8501})
    with pytest.raises(GateError, match="8501"):
        gate.patch("rota-extra", {"listen_port": 8501})
    for porta in (5676, 5677):
        with pytest.raises(GateError, match="Porteiro"):
            gate.add_manual("extra", porta)


def test_alias_nao_abre_rota():
    calls = []

    def transport(method, url, payload, timeout):
        calls.append((method, url, payload))
        return {"ok": True, "aliases": [{"ip": "203.0.113.9", "alias": "teste-gui"}]}

    gate = _gate(transport)
    gate.set_alias("203.0.113.9", "teste-gui")
    assert len(calls) == 1
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/aliases")
    assert "5676" not in json.dumps(calls[0][2])
    assert "5677" not in json.dumps(calls[0][2])


def test_nenhum_codigo_depende_da_janela():
    assert not (ROOT / "Scout_OSINT_Docker" / "scout" / "app.py").exists()
    assert not (ROOT / "Scout_OSINT_Docker" / "Scout_network.py").exists()
    for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.ps1")):
        if "tests" in path.parts:
            continue
        if any(part in {".venv", "site-packages", ".pytest_cache"} for part in path.parts):
            continue
        texto = path.read_text(encoding="utf-8", errors="replace")
        assert "Scout_network.py" not in texto, path
        assert "from scout.app" not in texto, path
        assert "Start-ScoutGui" not in texto, path
        assert "import requests, websocket" not in texto, path
    setup = (ROOT / "Scout_OSINT_Docker" / "setup.ps1").read_text(encoding="utf-8")
    assert "http://localhost:8501/?aba=scout" in setup
    assert "Start-Gui" not in setup
    projeto = (ROOT / "setup_projeto.ps1").read_text(encoding="utf-8")
    assert "Invoke-AutoScoutPython" not in projeto
    app = (ROOT / "control_plane" / "app.py").read_text(encoding="utf-8")
    main = app.split("def main", 1)[1]
    assert main.index("_require_login") < main.index("render_scout_tab")
    assert '"aba"' in app


def test_http_de_erro_com_ok_false_mostra_reason():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            corpo = json.dumps({"ok": False, "reason": "iptables indisponível"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, fmt, *args):
            return

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    try:
        porta = servidor.server_address[1]
        gate = ScoutGate(f"http://127.0.0.1:{porta}", timeout=2)
        with pytest.raises(GateError) as exc:
            gate.firewall_sync()
    finally:
        servidor.shutdown()
    assert exc.value.message == "O Scout recusou a operação: iptables indisponível"
