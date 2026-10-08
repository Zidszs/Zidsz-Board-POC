import subprocess
from pathlib import Path

import pytest

from control_plane.operations import (
    ALLOWED_CONTAINERS,
    OperationError,
    build_compose_argv,
    launch_orchestrator,
    orchestrator_command,
    restart_container,
    start_infrastructure,
    stop_infrastructure,
    stop_managed_porteiro,
)


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


class _Proc:
    def __init__(self, pid=4242, exits=False):
        self.pid = pid
        self._exits = exits
        self.returncode = 0

    def poll(self):
        return 0 if self._exits else None

    def wait(self, timeout=None):
        if self._exits:
            return 0
        raise subprocess.TimeoutExpired(cmd="node", timeout=timeout or 0)


def _patch_run(monkeypatch, code=0, stdout="", stderr=""):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    monkeypatch.setattr("control_plane.operations.subprocess.run", fake_run)
    return calls


def _which(monkeypatch, mapping):
    monkeypatch.setattr("control_plane.operations.shutil.which", lambda name: mapping.get(name))


def test_restart_uses_argument_list_without_shell(monkeypatch):
    calls = _patch_run(monkeypatch)
    _which(monkeypatch, {"docker": "/usr/bin/docker"})
    result = restart_container("n8n_app", timeout=5)
    argv, kwargs = calls[0]
    assert argv == ["/usr/bin/docker", "restart", "n8n_app"]
    assert kwargs["shell"] is False
    assert result.ok
    assert "litellm" in ALLOWED_CONTAINERS
    assert "langfuse-web" in ALLOWED_CONTAINERS


@pytest.mark.parametrize("name", ["", "n8n_app;rm", "../n8n_app", "langfuse-postgres", "postgres"])
def test_restart_rejects_names_outside_allowlist(monkeypatch, name):
    calls = _patch_run(monkeypatch)
    _which(monkeypatch, {"docker": "/usr/bin/docker"})
    with pytest.raises(OperationError):
        restart_container(name)
    assert calls == []


def test_restart_timeout_and_failure_are_friendly(monkeypatch):
    def boom(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout") or 1)

    monkeypatch.setattr("control_plane.operations.subprocess.run", boom)
    _which(monkeypatch, {"docker": "/usr/bin/docker"})
    with pytest.raises(OperationError) as exc:
        restart_container("litellm", timeout=3)
    assert "tempo limite" in exc.value.message.lower()
    assert "Traceback" not in exc.value.message

    def failed(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "boom " * 80)

    monkeypatch.setattr("control_plane.operations.subprocess.run", failed)
    with pytest.raises(OperationError) as exc:
        restart_container("langfuse-worker", timeout=3)
    assert "código 1" in exc.value.message
    assert len(exc.value.message) < 500


def test_restart_missing_docker(monkeypatch):
    _which(monkeypatch, {})
    with pytest.raises(OperationError) as exc:
        restart_container("n8n_app")
    assert "Docker" in exc.value.message


def test_compose_args_reject_volume_wipe(tmp_path):
    with pytest.raises(OperationError):
        build_compose_argv(
            compose_file=tmp_path / "docker-compose.yml",
            env_file=tmp_path / ".env",
            compose_args=("down", "-v"),
            prefix=["docker-compose"],
        )


def test_resolve_compose_rejects_escape(tmp_path):
    from control_plane.operations import resolve_compose_file

    outside = tmp_path / "evil.yml"
    outside.write_text("x", encoding="utf-8")
    link_parent = tmp_path / "n8n"
    link_parent.mkdir()
    with pytest.raises(OperationError):
        resolve_compose_file(tmp_path, "../evil.yml")
    with pytest.raises(OperationError):
        resolve_compose_file(tmp_path, "n8n/../../etc/passwd")


def test_stop_requires_confirmation(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    root = _repo(tmp_path, ENV_READY)
    with pytest.raises(OperationError):
        stop_infrastructure(root, confirmed=False)
    assert calls == []


def test_stop_down_without_volumes_in_order(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    _which(monkeypatch, {"docker-compose": "/usr/bin/docker-compose"})
    monkeypatch.setattr("control_plane.operations.process_command_line", lambda pid: "")
    root = _repo(tmp_path, ENV_READY)
    report = stop_infrastructure(root, confirmed=True, compose_timeout=5)
    assert report.ok
    commands = [call[0] for call in calls]
    relatives = [argv[argv.index("-f") + 1] for argv in commands]
    assert [Path(path).as_posix().split("/")[-2] + "/" + Path(path).name for path in relatives] == [
        "ngrok/docker-compose.yml",
        "n8n/docker-compose.yml",
        "llm/docker-compose.yml",
        "Scout_OSINT_Docker/docker-compose.yml",
    ]


def test_stop_skips_llm_when_unconfigured(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    _which(monkeypatch, {"docker-compose": "/usr/bin/docker-compose"})
    root = _repo(tmp_path, "USE_SCOUT=0\n")
    report = stop_infrastructure(root, confirmed=True, compose_timeout=5)
    assert report.ok
    parents = [Path(argv[argv.index("-f") + 1]).parent.name for argv, _ in calls]
    assert parents == ["ngrok", "n8n", "Scout_OSINT_Docker"]
    for argv, kwargs in calls:
        assert kwargs["shell"] is False
        assert "-v" not in argv
        assert "--volumes" not in argv
        assert argv[-1] == "down"


def _compose_calls(calls):
    return [(argv, kwargs) for argv, kwargs in calls if "-f" in argv]


def test_start_wires_tunnel_and_core_only(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    popens = []

    def fake_popen(argv, **kwargs):
        popens.append((list(argv), kwargs))
        return _Proc()

    monkeypatch.setattr("control_plane.operations.subprocess.Popen", fake_popen)
    _which(monkeypatch, {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose", "node": "/usr/bin/node"})
    root = _repo(tmp_path, ENV_READY)
    report = start_infrastructure(root, scout_build=False, compose_timeout=5, porteiro_settle_seconds=0)
    assert report.ok
    assert popens[0][0] == ["/usr/bin/node", "porteiro.js"]
    assert popens[0][1]["shell"] is False
    assert "PORTEIRO_TOKEN" not in popens[0][1]["env"]
    assert "abc123" not in " ".join(popens[0][0])
    painel = (root / ".n8groker" / "porteiro-painel.token").read_text(encoding="utf-8").strip()
    n8n = (root / ".n8groker" / "porteiro-n8n.token").read_text(encoding="utf-8").strip()
    assert painel != "abc123"
    assert painel != n8n
    assert "PORTEIRO_N8N_TOKEN=" in (root / ".n8groker" / "porteiro-n8n.env").read_text(encoding="utf-8")
    compose_targets = []
    for argv, kwargs in _compose_calls(calls):
        assert kwargs["shell"] is False
        assert kwargs["env"]["NGROK_TUNNEL_TARGET"] == "4050"
        assert kwargs["env"]["NGROK_TUNNEL_HOST"] == "scout-backend"
        compose_targets.append(Path(argv[argv.index("-f") + 1]).parent.name)
        assert "up" in argv and "-d" in argv
        assert "--build" not in argv
    assert compose_targets == ["Scout_OSINT_Docker", "ngrok"]
    assert any(argv[:3] == ["/usr/bin/docker", "network", "inspect"] for argv, _ in calls)
    assert not any("llm" in " ".join(argv) for argv, _ in _compose_calls(calls))


def test_start_nao_aponta_o_tunel_para_8501(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("control_plane.operations.subprocess.Popen", lambda *a, **k: _Proc())
    _which(monkeypatch, {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose", "node": "/usr/bin/node"})
    root = _repo(
        tmp_path,
        "USE_SCOUT=1\nSCOUT_PUBLIC_PORT=8501\nPAINEL_BORDA_PORT=8501\nPORTEIRO_TOKEN=t\n",
    )
    report = start_infrastructure(root, compose_timeout=5, porteiro_settle_seconds=0)
    assert report.ok
    disco = (root / ".env").read_text(encoding="utf-8")
    assert "SCOUT_PUBLIC_PORT=8501" in disco
    assert "PAINEL_BORDA_PORT=8501" in disco
    assert any(step.title == "Túnel" and "8501" in step.detail and "4050" in step.detail for step in report.steps)
    compose = _compose_calls(calls)
    assert compose
    assert all(kwargs["env"]["NGROK_TUNNEL_TARGET"] == "4050" for _, kwargs in compose)
    scout = next(
        kwargs
        for argv, kwargs in compose
        if "Scout_OSINT_Docker" in argv[argv.index("-f") + 1]
    )
    assert scout["env"]["SCOUT_PUBLIC_PORT"] == "4050"
    assert scout["env"]["PAINEL_BORDA_PORT"] == "8502"


def test_start_legacy_mode_is_porteiro_and_ngrok(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("control_plane.operations.subprocess.Popen", lambda *a, **k: _Proc())
    _which(monkeypatch, {"docker": "/usr/bin/docker", "docker-compose": "/usr/bin/docker-compose", "node": "/usr/bin/node"})
    root = _repo(tmp_path, "USE_SCOUT=0\nPORTEIRO_TOKEN=t\n")
    report = start_infrastructure(root, compose_timeout=5, porteiro_settle_seconds=0)
    assert report.ok
    assert not any(step.title == "Langfuse e LiteLLM" for step in report.steps)
    compose = _compose_calls(calls)
    parents = [Path(argv[argv.index("-f") + 1]).parent.name for argv, _ in compose]
    assert parents == ["ngrok"]
    assert compose[0][1]["env"]["NGROK_TUNNEL_TARGET"] == "5677"
    assert compose[0][1]["env"]["NGROK_TUNNEL_HOST"] == "host.docker.internal"


def test_start_scout_build_flag(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("control_plane.operations.subprocess.Popen", lambda *a, **k: _Proc())
    _which(monkeypatch, {"docker": "/usr/bin/docker", "node": "/usr/bin/node"})
    root = _repo(tmp_path, "USE_SCOUT=1\nSCOUT_PUBLIC_PORT=4050\n")
    start_infrastructure(root, scout_build=True, compose_timeout=5, porteiro_settle_seconds=0)
    scout = next(
        argv
        for argv, _ in calls
        if "-f" in argv and "Scout_OSINT_Docker" in argv[argv.index("-f") + 1]
    )
    assert scout[:3] == ["/usr/bin/docker", "compose", "-f"]
    assert scout[-3:] == ["up", "-d", "--build"]


def test_porteiro_stop_refuses_foreign_pid(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("control_plane.operations.process_command_line", lambda pid: "python other.py")
    root = _repo(tmp_path, ENV_READY)
    pid_file = root / "control_plane" / ".porteiro.cp.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("4321", encoding="utf-8")
    with pytest.raises(OperationError) as exc:
        stop_managed_porteiro(root)
    assert "Nada foi encerrado" in exc.value.message
    assert calls == []


def test_porteiro_stop_sends_kill_list(monkeypatch, tmp_path):
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("control_plane.operations.process_command_line", lambda pid: "/usr/bin/node porteiro.js")
    root = _repo(tmp_path, ENV_READY)
    pid_file = root / "control_plane" / ".porteiro.cp.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("4321", encoding="utf-8")
    result = stop_managed_porteiro(root)
    assert result.ok
    assert calls[0][0] == ["kill", "4321"]
    assert calls[0][1]["shell"] is False
    assert not pid_file.exists()


def test_missing_executable_is_friendly(monkeypatch):
    def boom(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr("control_plane.operations.subprocess.run", boom)
    _which(monkeypatch, {"docker": "docker"})
    with pytest.raises(OperationError) as exc:
        restart_container("ngrok_service")
    assert "Não encontrei" in exc.value.message


def test_orchestrator_command_is_a_list():
    argv = orchestrator_command(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", Path("iniciar_servicos.ps1"))
    assert isinstance(argv, list)
    assert argv[1:5] == ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert "-Command" not in argv


def test_launch_orchestrator_on_linux_does_not_start_a_process(monkeypatch, tmp_path):
    root = _repo(tmp_path, ENV_READY)
    (root / "iniciar_servicos.ps1").write_text("Write-Host hi\n", encoding="utf-8")
    monkeypatch.setattr("control_plane.operations.os.name", "posix")
    with pytest.raises(OperationError) as exc:
        launch_orchestrator(root)
    assert "Windows" in exc.value.message


def test_symlink_compose_outside_root_is_rejected(tmp_path):
    from control_plane.operations import resolve_compose_file

    root = tmp_path / "proj"
    outside = tmp_path / "outside.yml"
    outside.write_text("nope", encoding="utf-8")
    (root / "n8n").mkdir(parents=True)
    (root / "n8n" / "docker-compose.yml").symlink_to(outside)
    with pytest.raises(OperationError):
        resolve_compose_file(root, "n8n/docker-compose.yml")
