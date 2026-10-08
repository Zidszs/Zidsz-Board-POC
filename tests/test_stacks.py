"""Controle por stack: boot só do núcleo fica no PowerShell; aqui, o console."""

import json
import subprocess
from pathlib import Path

import pytest

from control_plane.actor import AccessDenied, set_actor
from control_plane.operations import OperationError, restart_stack, start_infrastructure, start_stack, stop_stack
from control_plane.stacks import nodes_exclude_json, stacks_desconhecidas, stacks_boot

ROOT = Path(__file__).resolve().parents[1]

ENV_READY = """
USE_SCOUT=1
SCOUT_PUBLIC_PORT=4050
PORTEIRO_TOKEN=abc123
LANGFUSE_PUBLIC_KEY=pk-lf-abc
LANGFUSE_SECRET_KEY=sk-lf-abc
LITELLM_MASTER_KEY=sk-abc
LANGFUSE_DB_PASSWORD=dbpass
ENCRYPTION_KEY=aa
N8N_CREDENTIALS_OVERWRITE_DATA={"openAiApi":{"apiKey":"sk-abc","url":"http://litellm:4000/v1"}}
"""


def _repo(tmp_path: Path, env_text: str) -> Path:
    for relative in (
        "n8n/docker-compose.yml",
        "ngrok/docker-compose.yml",
        "Scout_OSINT_Docker/docker-compose.yml",
        "llm/docker-compose.yml",
        "Porteiro/porteiro.js",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n", encoding="utf-8")
    (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    return tmp_path


def _patch_run(monkeypatch, code=0, stdout="", stderr=""):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    monkeypatch.setattr("control_plane.operations.subprocess.run", fake_run)
    return calls


def _which(monkeypatch, mapping):
    monkeypatch.setattr("control_plane.operations.shutil.which", lambda name: mapping.get(name))


def test_stacks_boot_padrao_e_so_o_nucleo():
    assert stacks_boot("") == ()
    assert stacks_boot(None) == ()
    assert stacks_boot("  ") == ()


def test_stacks_boot_preserva_ordem_e_ignora_desconhecido():
    assert stacks_boot("llm, n8n, llm, foo") == ("llm", "n8n")
    assert stacks_desconhecidas("llm, foo, BAR, n8n, foo") == ("foo", "bar")


def test_nodes_exclude_mantem_o_padrao():
    texto = nodes_exclude_json("")
    assert texto.startswith("[")
    assert "n8n-nodes-base.executeCommand" in texto
    assert "n8n-nodes-base.code" not in texto
    assert nodes_exclude_json('["ja-json"]') == '["ja-json"]'


def test_n8n_sem_llm_nao_e_erro(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    _which(monkeypatch, {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose"})
    root = _repo(tmp_path, ENV_READY)
    report = start_stack(root, "n8n", compose_timeout=5)
    assert report.ok
    assert any(step.ok and "não é erro" in step.detail for step in report.steps)
    compose = [argv for argv, _ in calls if "--env-file" in argv]
    assert len(compose) == 1
    assert compose[0][-2:] == ["up", "-d"]
    assert "n8n/docker-compose.yml" in compose[0][compose[0].index("-f") + 1]
    assert "NODES_EXCLUDE" in calls[-1][1]["env"]


def test_n8n_com_llm_no_ar_nao_avisa(monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        stdout = ""
        if "{{.State.Running}}" in argv:
            stdout = "true\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr("control_plane.operations.subprocess.run", fake_run)
    _which(monkeypatch, {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose"})
    root = _repo(tmp_path, ENV_READY)
    report = start_stack(root, "n8n", compose_timeout=5)
    assert report.ok
    assert not any("não é erro" in step.detail for step in report.steps)


def test_llm_sem_chaves_nao_sobe_e_nao_anota(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    _which(monkeypatch, {"docker": "/usr/bin/docker"})
    root = _repo(tmp_path, "USE_SCOUT=1\nSCOUT_PUBLIC_PORT=4050\n")
    report = start_stack(root, "llm", compose_timeout=5)
    assert not report.ok
    assert report.steps[0].title == "Langfuse e LiteLLM"
    assert "Chaves ausentes" in report.steps[0].detail
    assert calls == []
    assert not (root / ".n8groker" / "trilha" / "trilha.jsonl").exists()


def test_parar_stack_exige_confirmacao(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    _which(monkeypatch, {"docker-compose": "/usr/bin/docker-compose"})
    root = _repo(tmp_path, ENV_READY)
    with pytest.raises(OperationError) as exc:
        stop_stack(root, "n8n", confirmed=False)
    assert "confirme" in exc.value.message
    assert calls == []


def test_reiniciar_stack_usa_restart(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    _which(monkeypatch, {"docker-compose": "/usr/bin/docker-compose"})
    root = _repo(tmp_path, ENV_READY)
    report = restart_stack(root, "n8n", compose_timeout=5)
    assert report.ok
    argv = next(item for item, _ in calls if "--env-file" in item)
    assert argv[-1] == "restart"
    assert "down" not in argv
    assert "-v" not in argv


def test_so_quem_pode_operar_liga_a_stack(monkeypatch, tmp_path):
    monkeypatch.setenv("PANEL_MODE", "console")
    _patch_run(monkeypatch)
    _which(monkeypatch, {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose"})
    root = _repo(tmp_path, ENV_READY)
    set_actor(
        {
            "admin": False,
            "username": "visita",
            "ip": "203.0.113.40",
            "operar": ["langfuse"],
            "root": str(root),
        }
    )
    try:
        with pytest.raises(AccessDenied):
            start_stack(root, "llm", compose_timeout=5)
        with pytest.raises(AccessDenied):
            start_stack(root, "n8n", compose_timeout=5)
    finally:
        set_actor(None)


def test_admin_liga_a_stack_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("PANEL_MODE", "console")
    calls = _patch_run(monkeypatch)
    _which(monkeypatch, {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose"})
    root = _repo(tmp_path, ENV_READY)
    set_actor({"admin": True, "username": "admin", "ip": "127.0.0.1", "root": str(root)})
    try:
        report = start_stack(root, "llm", compose_timeout=5)
    finally:
        set_actor(None)
    assert report.ok
    compose = [argv for argv, _ in calls if "--env-file" in argv]
    assert "llm/docker-compose.yml" in compose[0][compose[0].index("-f") + 1]


def test_trilha_registra_quem_ligou(monkeypatch, tmp_path):
    monkeypatch.setenv("PANEL_MODE", "console")
    _patch_run(monkeypatch)
    _which(monkeypatch, {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose"})
    root = _repo(tmp_path, ENV_READY)
    set_actor(
        {
            "admin": False,
            "username": "guia",
            "ip": "203.0.113.40",
            "operar": ["n8n", "langfuse", "litellm"],
            "root": str(root),
        }
    )
    try:
        report = stop_stack(root, "n8n", confirmed=True, compose_timeout=5)
    finally:
        set_actor(None)
    assert report.ok
    arquivo = root / ".n8groker" / "trilha" / "trilha.jsonl"
    linha = json.loads(arquivo.read_text(encoding="utf-8").splitlines()[0])
    assert linha["conta"] == "guia"
    assert linha["passo"] == "guia desligou n8n"
    assert linha["resultado"] == "STACK"
    assert linha["ip"] == "203.0.113.40"
    assert linha["app"] == "n8n"


def test_docker_ausente_tem_mensagem_de_path(monkeypatch, tmp_path):
    _patch_run(monkeypatch)
    _which(monkeypatch, {})
    root = _repo(tmp_path, ENV_READY)
    with pytest.raises(OperationError) as exc:
        start_stack(root, "n8n", compose_timeout=5)
    assert "Docker não está no PATH" in exc.value.message


def test_motor_parado_avisa_para_abrir_o_desktop(monkeypatch, tmp_path):
    _patch_run(
        monkeypatch,
        code=1,
        stderr="Cannot connect to the Docker daemon at npipe:////./pipe/dockerDesktopLinuxEngine. Is the docker daemon running?",
    )
    _which(monkeypatch, {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose", "node": "/usr/bin/node"})
    monkeypatch.setattr("control_plane.operations.subprocess.Popen", lambda *a, **k: None)
    root = _repo(tmp_path, ENV_READY)
    with pytest.raises(OperationError) as exc:
        start_infrastructure(root, compose_timeout=5, porteiro_settle_seconds=0)
    assert "motor não respondeu" in exc.value.message
    assert "Docker Desktop" in exc.value.message


def test_console_nao_oferece_parar_o_nucleo():
    text = (ROOT / "control_plane" / "app.py").read_text(encoding="utf-8")
    assert "Iniciar núcleo" in text
    assert "Parar núcleo" not in text
    assert "Iniciar infraestrutura" not in text
    assert "Parar infraestrutura" not in text
    assert "Abrir HUD do núcleo" in text
    assert "Iniciar n8n" in text
    assert "Parar n8n" in text
    assert "Reiniciar n8n" in text
    assert "Iniciar Langfuse e LiteLLM" in text
    assert "Parar Langfuse e LiteLLM" in text
    assert "Reiniciar Langfuse e LiteLLM" in text
    assert "Pode operar em Langfuse e em LiteLLM" in text
