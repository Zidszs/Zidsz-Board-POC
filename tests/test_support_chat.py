"""Chat de suporte: allowlist, confirmação, redação e Ollama fora do ar."""

import urllib.error
from pathlib import Path

import pytest

from control_plane.config import default_services
from control_plane.operations import OperationResult
from control_plane.support_chat import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    ChatConfig,
    NeedsConfirmation,
    OllamaError,
    apply_model_message,
    check_ollama,
    decide_tool,
    perform,
    redact_text,
    tool_schemas,
)


def _call(name, arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def test_dispatch_rejects_unknown_service_and_free_command():
    services = default_services()
    rejected = [
        decide_tool("exec", {"command": "rm -rf /"}, services),
        decide_tool("logs_servico", {"service_id": "n8n", "command": "docker rm -f n8n_app"}, services),
        decide_tool("status_servico", {"service_id": "n8n_app; rm"}, services),
        decide_tool("parar_servico", {"service_id": "langfuse-postgres"}, services),
        decide_tool("reiniciar_servico", {"service_id": "control-plane"}, services),
        decide_tool("logs_servico", {"service_id": "n8n", "tail": "80; rm"}, services),
        decide_tool("iniciar_servico", {"service_id": "scout-gui"}, services),
    ]
    assert all(item.kind == "rejected" for item in rejected)
    assert any("Control Plane" in item.message for item in rejected)


def test_state_change_waits_for_confirmation(monkeypatch):
    calls = []

    def fake(verb, name, timeout=60, service_id=None):
        calls.append((verb, name))
        return OperationResult(True, name, "executado")

    monkeypatch.setattr("control_plane.support_chat.run_allowed_container", fake)
    services = default_services()
    message = _call("reiniciar_servico", {"service_id": "n8n"})
    text, pending = apply_model_message(
        message, services=services, health_by_id={}, env={}, root=Path(".")
    )
    assert pending is not None and pending.mutating
    assert "Nada foi executado" in text
    assert calls == []
    with pytest.raises(NeedsConfirmation):
        perform(pending, confirmed=False, services=services, health_by_id={}, env={}, root=Path("."))
    assert calls == []
    done = perform(pending, confirmed=True, services=services, health_by_id={}, env={}, root=Path("."))
    assert calls == [("restart", "n8n_app")]
    assert done == "executado"


def test_logs_pass_through_redaction(monkeypatch):
    monkeypatch.setattr(
        "control_plane.support_chat.container_logs",
        lambda name, tail=80, timeout=20, service_id=None: OperationResult(True, name, "vazou sk-supersegredo"),
    )
    services = default_services()
    decision = decide_tool("logs_servico", {"service_id": "n8n", "tail": 10}, services)
    assert decision.kind == "readonly"
    text = perform(
        decision,
        confirmed=True,
        services=services,
        health_by_id={},
        env={"LITELLM_MASTER_KEY": "sk-supersegredo"},
        root=Path("."),
    )
    assert "sk-supersegredo" not in text
    assert "«redigido»" in text


def test_redaction_hides_secret_values_only():
    env = {
        "LITELLM_MASTER_KEY": "sk-supersegredo",
        "NGROK_AUTHTOKEN": "token-longo-ngrok",
        "PORTEIRO_PASS": "senha-grande",
        "PORTEIRO_USER": "admin",
        "USE_SCOUT": "1",
    }
    text = redact_text(
        "chave sk-supersegredo token token-longo-ngrok senha senha-grande user admin",
        env,
    )
    assert "sk-supersegredo" not in text
    assert "token-longo-ngrok" not in text
    assert "senha-grande" not in text
    assert text.count("«redigido»") == 3
    assert "admin" in text


def test_scout_readonly_passa_e_mutacao_fica_fora(monkeypatch):
    services = default_services()
    consultas = []

    def fake(kind, base=None, transport=None):
        consultas.append((kind, base))
        return f"rota ok sk-supersegredo {kind}"

    monkeypatch.setattr("control_plane.scout_client.query_text", fake)
    decision = decide_tool("scout_rotas", {}, services)
    assert decision.kind == "readonly"
    text = perform(
        decision,
        confirmed=True,
        services=services,
        health_by_id={},
        env={"SCOUT_BACKEND_URL": "ws://127.0.0.1:8765/ws", "LITELLM_MASTER_KEY": "sk-supersegredo"},
        root=Path("."),
    )
    assert consultas == [("scout_rotas", "ws://127.0.0.1:8765/ws")]
    assert "sk-supersegredo" not in text
    assert "«redigido»" in text
    recusados = [
        decide_tool("scout_rotas", {"command": "rm"}, services),
        decide_tool("scout_toggle", {}, services),
        decide_tool("block_ip", {"ip": "10.0.0.1"}, services),
        decide_tool("firewall_sync", {}, services),
        decide_tool("set_alias", {"ip": "10.0.0.1", "alias": "x"}, services),
    ]
    assert all(item.kind == "rejected" for item in recusados)
    nomes = [item["function"]["name"] for item in tool_schemas(services)]
    assert {"scout_resumo", "scout_rotas", "scout_trafego", "scout_clientes"} <= set(nomes)
    assert "block_ip" not in nomes
    assert "scout_toggle" not in nomes


def test_ollama_down_or_model_missing_is_a_friendly_message():
    def down(method, url, payload, timeout, stream):
        raise urllib.error.URLError("connection refused")

    try:
        check_ollama(ChatConfig(DEFAULT_OLLAMA_URL, DEFAULT_MODEL), transport=down)
    except OllamaError as exc:
        assert "ollama serve" in exc.message
        assert "Traceback" not in exc.message
    else:
        raise AssertionError("Ollama fora do ar não avisou")

    def tags(method, url, payload, timeout, stream):
        return {"models": [{"name": "outro:latest"}]}

    try:
        check_ollama(ChatConfig(DEFAULT_OLLAMA_URL, DEFAULT_MODEL), transport=tags)
    except OllamaError as exc:
        assert f"ollama pull {DEFAULT_MODEL}" in exc.message
        assert "Traceback" not in exc.message
    else:
        raise AssertionError("modelo ausente não avisou")
