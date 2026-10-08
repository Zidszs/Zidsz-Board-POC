"""Operações no host via subprocess, com lista fechada de comandos.

Não usa shell=True. O `iniciar_servicos.ps1` continua dono do preparo da
máquina, do HUD, do sync da URL do ngrok e do desligamento pela tecla Q.
Este módulo sobe os mesmos compose files sem substituir esse fluxo.

Containers que podem ser reiniciados (container_name nos compose):
    n8n_app, ngrok_service, scout-backend, langfuse-web, langfuse-worker, litellm
Para incluir outro, acrescente o nome em ALLOWED_CONTAINERS e no ServiceSpec.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from control_plane.envfile import (
    llm_configured,
    ngrok_tunnel_host,
    ngrok_tunnel_target,
    portas_publicadas,
    read_env_file,
    scout_enabled,
)

# Nomes reais de container_name. Não inclua banco/redis/clickhouse aqui.
ALLOWED_CONTAINERS = frozenset(
    {
        "n8n_app",
        "ngrok_service",
        "scout-backend",
        "langfuse-web",
        "langfuse-worker",
        "litellm",
    }
)

ALLOWED_COMPOSE = frozenset(
    {
        "n8n/docker-compose.yml",
        "ngrok/docker-compose.yml",
        "Scout_OSINT_Docker/docker-compose.yml",
        "llm/docker-compose.yml",
    }
)

ALLOWED_COMPOSE_ARGS = frozenset(
    {
        ("up", "-d"),
        ("up", "-d", "--build"),
        ("down",),
        ("restart",),
    }
)

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_ORCHESTRATOR = "iniciar_servicos.ps1"
_PORTEIRO_REL = Path("Porteiro") / "porteiro.js"
_PID_REL = Path("control_plane") / ".porteiro.cp.pid"


class OperationError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    title: str
    detail: str


@dataclass(frozen=True)
class OperationReport:
    ok: bool
    steps: tuple[OperationResult, ...]


def _under_root(root: Path, relative: str) -> Path:
    if relative not in ALLOWED_COMPOSE and relative not in {_ORCHESTRATOR, str(_PORTEIRO_REL).replace("\\", "/")}:
        # relative de compose é validado à parte; este helper é genérico para arquivos conhecidos
        pass
    root_resolved = root.resolve()
    path = (root_resolved / relative).resolve()
    if not path.is_relative_to(root_resolved):
        raise OperationError("Caminho fora da pasta do projeto.")
    return path


def resolve_compose_file(root: Path, relative: str) -> Path:
    if relative not in ALLOWED_COMPOSE:
        raise OperationError("Compose fora da lista permitida.")
    path = _under_root(root, relative)
    if not path.is_file():
        raise OperationError(f"Arquivo de compose não encontrado: {relative}")
    return path


def compose_prefix() -> list[str]:
    # O orquestrador PowerShell chama `docker-compose`. Preferimos o mesmo binário.
    docker_compose = shutil.which("docker-compose")
    if docker_compose:
        return [docker_compose]
    docker = shutil.which("docker")
    if docker:
        return [docker, "compose"]
    raise OperationError("Docker não está no PATH. Abra o Docker Desktop e tente de novo.")


def build_compose_argv(
    *,
    compose_file: Path,
    env_file: Path,
    compose_args: tuple[str, ...],
    prefix: list[str],
) -> list[str]:
    if compose_args not in ALLOWED_COMPOSE_ARGS:
        raise OperationError("Argumentos de compose fora da lista permitida.")
    if not prefix or any(not isinstance(part, str) or not part for part in prefix):
        raise OperationError("Executável do Docker inválido.")
    return [
        *prefix,
        "-f",
        str(compose_file),
        "--env-file",
        str(env_file),
        *compose_args,
    ]


def _run(
    argv: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if isinstance(argv, str) or not isinstance(argv, list) or not argv:
        raise OperationError("Comando recusado.")
    if not all(isinstance(part, str) for part in argv):
        raise OperationError("Comando recusado.")
    try:
        return subprocess.run(
            argv,
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OperationError(f"A operação excedeu o tempo limite ({int(timeout)}s).") from exc
    except FileNotFoundError:
        raise OperationError(f"Não encontrei o executável «{argv[0]}». Confira se ele está no PATH.") from None
    except OSError as exc:
        raise OperationError(f"Falha ao executar o comando: {exc.strerror or 'erro do sistema'}") from exc


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    text = (proc.stderr or proc.stdout or "").strip()
    text = " ".join(text.split())
    if len(text) > 400:
        text = text[:400] + "…"
    return text


_DAEMON_MARKERS = (
    "cannot connect to the docker daemon",
    "is the docker daemon running",
    "error during connect",
    "dockerdesktoplinuxengine",
    "npipe",
    "the docker engine",
)


def _fail(title: str, proc: subprocess.CompletedProcess[str]) -> str:
    detail = _tail(proc)
    aviso = ""
    if any(marker in detail.lower() for marker in _DAEMON_MARKERS):
        aviso = (
            "O Docker está no PATH, mas o motor não respondeu. "
            "Abra o Docker Desktop e espere ele ficar no ar. "
        )
    if detail:
        return f"{aviso}{title} falhou (código {proc.returncode}). {detail}"
    return f"{aviso}{title} falhou (código {proc.returncode})."


def _env_file(root: Path) -> Path:
    path = (root / ".env").resolve()
    if not path.is_file() or not path.is_relative_to(root.resolve()):
        raise OperationError(
            "Arquivo .env não encontrado na raiz do projeto. Rode o Setup ou python scripts/init_env.py "
            "e preencha o NGROK_AUTHTOKEN."
        )
    return path


def _child_env(
    base: dict[str, str],
    tunnel_target: str,
    tunnel_host: str,
    values: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base)
    env["NGROK_TUNNEL_TARGET"] = tunnel_target
    env["NGROK_TUNNEL_HOST"] = tunnel_host
    if values is not None:
        env.update(portas_publicadas(values))
    return env


def _porteiro_env(base: dict[str, str], values: dict[str, str]) -> dict[str, str]:
    env = dict(base)
    # `values` é o .env. Não é copiado para o Node: o Scout monta esse arquivo.
    if "PORTEIRO_TOKEN" in values or "PORTEIRO_TOKEN" in env:
        env.pop("PORTEIRO_TOKEN", None)
    return env


def _pid_path(root: Path) -> Path:
    return root / _PID_REL


def _read_pid(path: Path) -> int:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OperationError("Não consegui ler o PID do Porteiro.") from exc
    if not raw.isdigit():
        raise OperationError("PID do Porteiro inválido. Nada foi encerrado.")
    pid = int(raw)
    if pid < 2:
        raise OperationError("PID do Porteiro inválido. Nada foi encerrado.")
    return pid


def process_command_line(pid: int) -> str:
    if os.name == "nt":
        # pid já é int validado. Lista de argumentos, sem shell=True.
        proc = _run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
            ],
            timeout=15,
        )
        return proc.stdout or ""
    cmdline = Path(f"/proc/{pid}/cmdline")
    if not cmdline.exists():
        return ""
    try:
        return cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def _compose(
    root: Path,
    relative: str,
    args: tuple[str, ...],
    *,
    timeout: float,
    env: dict[str, str],
    title: str,
) -> OperationResult:
    env_file = _env_file(root)
    compose_file = resolve_compose_file(root, relative)
    argv = build_compose_argv(
        compose_file=compose_file,
        env_file=env_file,
        compose_args=args,
        prefix=compose_prefix(),
    )
    proc = _run(argv, timeout=timeout, env=env, cwd=str(root))
    shown = shlex.join(argv)
    if proc.returncode != 0:
        raise OperationError(_fail(title, proc))
    return OperationResult(True, title, shown)


def start_porteiro(root: Path, values: dict[str, str], *, settle_seconds: float = 0.4) -> OperationResult:
    from control_plane.actor import concluir, exigir

    exigir("operar", "porteiro")
    script = _under_root(root, "Porteiro/porteiro.js")
    if not script.is_file():
        return OperationResult(False, "Porteiro", "Porteiro/porteiro.js não encontrado.")
    node = shutil.which("node")
    if not node:
        return OperationResult(False, "Porteiro", "Node.js não está no PATH. O restante da stack pode subir mesmo assim.")

    pid_file = _pid_path(root)
    if pid_file.is_file():
        try:
            pid = _read_pid(pid_file)
        except OperationError:
            pid = 0
        if pid and process_command_line(pid).find("porteiro.js") >= 0:
            return OperationResult(True, "Porteiro", "Porteiro já está em execução por este painel.")

    from control_plane.porteiro_token import garantir

    garantir(root)
    proc = subprocess.Popen(
        [node, "porteiro.js"],
        cwd=str(script.parent),
        env=_porteiro_env(os.environ.copy(), values),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        start_new_session=True,
    )
    if settle_seconds > 0:
        try:
            proc.wait(timeout=settle_seconds)
        except subprocess.TimeoutExpired:
            pass
    if proc.poll() is not None:
        return OperationResult(
            False,
            "Porteiro",
            "O processo do Porteiro encerrou ao iniciar. A porta 5677 pode já estar em uso pelo PowerShell.",
        )
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    concluir("operar", "porteiro")
    return OperationResult(True, "Porteiro", f"Porteiro iniciado (PID {proc.pid}).")


def stop_managed_porteiro(root: Path) -> OperationResult:
    """Encerra só o Porteiro que este painel subiu. Não mexe no PID do PowerShell."""
    from control_plane.actor import concluir, exigir

    exigir("operar", "porteiro")
    pid_file = _pid_path(root)
    if not pid_file.is_file():
        return OperationResult(True, "Porteiro", "Nenhum Porteiro foi iniciado por este painel.")
    pid = _read_pid(pid_file)
    cmdline = process_command_line(pid)
    if not cmdline.strip():
        pid_file.unlink(missing_ok=True)
        return OperationResult(True, "Porteiro", "O processo já tinha encerrado.")
    if "porteiro.js" not in cmdline:
        raise OperationError("O PID guardado não corresponde ao Porteiro. Nada foi encerrado.")
    if os.name == "nt":
        argv = ["taskkill", "/PID", str(pid), "/F"]
    else:
        argv = ["kill", str(pid)]
    proc = _run(argv, timeout=15)
    pid_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise OperationError(_fail("Encerrar Porteiro", proc))
    concluir("operar", "porteiro")
    return OperationResult(True, "Porteiro", "Porteiro iniciado por este painel foi encerrado.")


def _require_allowed_container(name: str) -> str:
    if not isinstance(name, str) or name not in ALLOWED_CONTAINERS or not _SAFE_NAME.fullmatch(name):
        raise OperationError(f"«{name}» não está na lista de containers permitidos.")
    return name


def _docker_bin() -> str:
    docker = shutil.which("docker")
    if not docker:
        raise OperationError("Docker não está no PATH. Abra o Docker Desktop e tente de novo.")
    return docker


def run_allowed_container(verb: str, name: str, *, timeout: float = 60, service_id: str | None = None) -> OperationResult:
    """start, stop ou restart só de container da lista. Sem shell."""
    from control_plane.actor import concluir, exigir

    exigir("operar", service_id)
    if verb not in {"start", "stop", "restart"}:
        raise OperationError("Comando recusado.")
    allowed = _require_allowed_container(name)
    titles = {"start": "Iniciar", "stop": "Parar", "restart": "Reiniciar"}
    argv = [_docker_bin(), verb, allowed]
    proc = _run(argv, timeout=timeout)
    if proc.returncode != 0:
        raise OperationError(_fail(f"{titles[verb]} {allowed}", proc))
    detail = {"start": "Container iniciado.", "stop": "Container parado.", "restart": "Container reiniciado."}[verb]
    concluir("operar", service_id or allowed)
    return OperationResult(True, allowed, detail)


def restart_container(name: str, *, timeout: float = 60, service_id: str | None = None) -> OperationResult:
    return run_allowed_container("restart", name, timeout=timeout, service_id=service_id)


def container_logs(name: str, *, tail: int = 80, timeout: float = 20, service_id: str | None = None) -> OperationResult:
    """docker logs --tail N, com N limitado. A redação de segredo fica no chamador."""
    from control_plane.actor import concluir, exigir

    exigir("operar", service_id)
    allowed = _require_allowed_container(name)
    if isinstance(tail, bool) or not isinstance(tail, int) or tail < 1 or tail > 200:
        raise OperationError("A quantidade de linhas precisa ser um inteiro de 1 a 200.")
    argv = [_docker_bin(), "logs", "--tail", str(tail), allowed]
    proc = _run(argv, timeout=timeout)
    if proc.returncode != 0:
        raise OperationError(_fail(f"Logs de {allowed}", proc))
    text = "\n".join(part for part in ((proc.stdout or "").rstrip(), (proc.stderr or "").rstrip()) if part)
    if len(text) > 8000:
        text = text[-8000:]
    concluir("operar", service_id or allowed)
    return OperationResult(True, allowed, text or "(sem linhas)")


def _report(steps: list[OperationResult]) -> OperationReport:
    return OperationReport(ok=all(step.ok for step in steps), steps=tuple(steps))


def ensure_rede(*, timeout: float = 30) -> OperationResult:
    """Cria `rede_comunicacao` se o motor estiver no ar e a rede ainda não existir."""
    docker = _docker_bin()
    inspect = _run([docker, "network", "inspect", "rede_comunicacao"], timeout=timeout)
    if inspect.returncode == 0:
        return OperationResult(True, "Rede", "rede_comunicacao já existe.")
    created = _run([docker, "network", "create", "rede_comunicacao"], timeout=timeout)
    if created.returncode != 0:
        raise OperationError(_fail("Rede rede_comunicacao", created))
    return OperationResult(True, "Rede", "rede_comunicacao criada.")


def _child_for(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    env_path = _env_file(root)
    values = read_env_file(env_path)
    try:
        target = ngrok_tunnel_target(values)
    except ValueError as exc:
        raise OperationError(str(exc)) from exc
    return values, _child_env(os.environ.copy(), target, ngrok_tunnel_host(values), values)


def _container_running(name: str, *, timeout: float = 15) -> bool:
    try:
        docker = _docker_bin()
    except OperationError:
        return False
    proc = _run([docker, "inspect", "-f", "{{.State.Running}}", name], timeout=timeout)
    return proc.returncode == 0 and (proc.stdout or "").strip().lower() == "true"


def _llm_no_ar() -> bool:
    return _container_running("langfuse-web") and _container_running("litellm")


def _exigir_stack(spec) -> None:
    from control_plane.actor import exigir

    for service_id in spec.operar:
        exigir("operar", service_id)


def _anotar_stack(root: Path, stack_id: str, verbo: str) -> None:
    """Registra quem ligou ou desligou. A trilha não pode derrubar a operação."""
    try:
        import sys

        from control_plane.actor import get_actor

        actor = get_actor()
        if isinstance(actor, dict):
            conta = "admin" if actor.get("admin") else str(actor.get("username") or "painel")
            ip = str(actor.get("ip") or "127.0.0.1")
        else:
            conta = "painel"
            ip = "127.0.0.1"
        for base in (root, Path(__file__).resolve().parents[1]):
            scout_root = base / "Scout_OSINT_Docker"
            if (scout_root / "scout" / "core" / "trilha.py").is_file():
                if str(scout_root) not in sys.path:
                    sys.path.insert(0, str(scout_root))
                break
        from scout.core.trilha import Trilha, pasta_de

        Trilha(pasta_de(root)).anotar(
            ip=ip or "127.0.0.1",
            conta=conta,
            app=stack_id,
            passo=f"{conta} {verbo} {stack_id}",
            resultado="STACK",
        )
    except Exception:
        return


def start_infrastructure(root: Path, *, scout_build: bool = False, compose_timeout: float = 180, porteiro_settle_seconds: float = 0.4) -> OperationReport:
    """Sobe o núcleo: rede, Porteiro, Scout (se USE_SCOUT=1) e ngrok.

    Não sobe n8n nem Langfuse/LiteLLM. Isso fica em `start_stack`. O botão do
    console chama esta função; `STACKS_BOOT` só vale para o boot do PowerShell.
    """
    from control_plane.actor import concluir, exigir

    exigir("stack")
    from control_plane.auth import AuthError
    from control_plane.user_token import garantir

    try:
        garantir(root)
    except AuthError as exc:
        return _report([OperationResult(False, "Chave de usuário", exc.message)])
    values, child = _child_for(root)
    steps: list[OperationResult] = []
    if scout_enabled(values) and values.get("SCOUT_PUBLIC_PORT", "").strip() == "8501":
        steps.append(
            OperationResult(
                True,
                "Túnel",
                "A porta 8501 é o HUD-admin. O alvo do ngrok não muda para ela e segue 4050.",
            )
        )
    steps.append(ensure_rede(timeout=compose_timeout))
    steps.append(start_porteiro(root, values, settle_seconds=porteiro_settle_seconds))

    if scout_enabled(values):
        scout_args = ("up", "-d", "--build") if scout_build else ("up", "-d")
        steps.append(
            _compose(
                root,
                "Scout_OSINT_Docker/docker-compose.yml",
                scout_args,
                timeout=compose_timeout,
                env=child,
                title="Scout",
            )
        )

    steps.append(
        _compose(root, "ngrok/docker-compose.yml", ("up", "-d"), timeout=compose_timeout, env=child, title="ngrok")
    )
    concluir("stack", "start")
    return _report(steps)


def start_stack(root: Path, stack_id: str, *, compose_timeout: float = 180) -> OperationReport:
    from control_plane.actor import concluir
    from control_plane.stacks import LLM_FORA_DO_AR, nodes_exclude_json, stack_spec

    try:
        spec = stack_spec(stack_id)
    except KeyError as exc:
        raise OperationError("Stack fora da lista permitida.") from exc
    _exigir_stack(spec)
    values, child = _child_for(root)
    if stack_id == "llm" and not llm_configured(values):
        return _report(
            [
                OperationResult(
                    False,
                    "Langfuse e LiteLLM",
                    "Chaves ausentes no .env. Rode o Setup para gerá-las. O n8n segue sem o proxy.",
                )
            ]
        )
    steps: list[OperationResult] = [ensure_rede(timeout=compose_timeout)]
    if stack_id == "n8n":
        if not _llm_no_ar():
            steps.append(OperationResult(True, "n8n", LLM_FORA_DO_AR))
        child = dict(child)
        child["NODES_EXCLUDE"] = nodes_exclude_json(values.get("N8N_NODES_EXCLUDE", ""))
    composed = _compose(root, spec.compose, ("up", "-d"), timeout=compose_timeout, env=child, title=spec.title)
    steps.append(composed)
    _anotar_stack(root, stack_id, "ligou")
    concluir("operar", stack_id)
    return _report(steps)


def stop_stack(root: Path, stack_id: str, *, confirmed: bool, compose_timeout: float = 180) -> OperationReport:
    from control_plane.actor import concluir
    from control_plane.stacks import stack_spec

    try:
        spec = stack_spec(stack_id)
    except KeyError as exc:
        raise OperationError("Stack fora da lista permitida.") from exc
    _exigir_stack(spec)
    if not confirmed:
        raise OperationError("Parada cancelada: confirme a ação antes de derrubar a stack.")
    values, child = _child_for(root)
    if stack_id == "llm" and not llm_configured(values):
        return _report(
            [
                OperationResult(
                    False,
                    spec.title,
                    "Chaves ausentes no .env. Rode o Setup para gerá-las. Nada foi derrubado.",
                )
            ]
        )
    composed = _compose(root, spec.compose, ("down",), timeout=compose_timeout, env=child, title=spec.title)
    _anotar_stack(root, stack_id, "desligou")
    concluir("operar", stack_id)
    return _report([composed])


def restart_stack(root: Path, stack_id: str, *, compose_timeout: float = 180) -> OperationReport:
    from control_plane.actor import concluir
    from control_plane.stacks import stack_spec

    try:
        spec = stack_spec(stack_id)
    except KeyError as exc:
        raise OperationError("Stack fora da lista permitida.") from exc
    _exigir_stack(spec)
    if stack_id == "llm":
        values, child = _child_for(root)
        if not llm_configured(values):
            return _report(
                [
                    OperationResult(
                        False,
                        "Langfuse e LiteLLM",
                        "Chaves ausentes no .env. Rode o Setup para gerá-las. O n8n segue sem o proxy.",
                    )
                ]
            )
    else:
        _values, child = _child_for(root)
    composed = _compose(root, spec.compose, ("restart",), timeout=compose_timeout, env=child, title=spec.title)
    _anotar_stack(root, stack_id, "reiniciou")
    concluir("operar", stack_id)
    return _report([composed])


def stop_infrastructure(root: Path, *, confirmed: bool, compose_timeout: float = 180) -> OperationReport:
    from control_plane.actor import concluir, exigir

    exigir("stack")
    if not confirmed:
        raise OperationError("Parada cancelada: confirme a ação antes de derrubar a infraestrutura.")
    env_path = _env_file(root)
    values = read_env_file(env_path)
    try:
        target = ngrok_tunnel_target(values)
    except ValueError as exc:
        raise OperationError(str(exc)) from exc
    child = _child_env(os.environ.copy(), target, ngrok_tunnel_host(values), values)
    steps: list[OperationResult] = []
    # Mesma ordem do Stop-Tudo: borda primeiro. Sem as chaves, o compose do LLM
    # nem interpola; nesse caso não há stack para derrubar.
    plan = [
        ("ngrok/docker-compose.yml", "ngrok"),
        ("n8n/docker-compose.yml", "n8n"),
    ]
    if llm_configured(values):
        plan.append(("llm/docker-compose.yml", "Langfuse e LiteLLM"))
    plan.append(("Scout_OSINT_Docker/docker-compose.yml", "Scout"))
    for relative, title in plan:
        steps.append(_compose(root, relative, ("down",), timeout=compose_timeout, env=child, title=title))
    steps.append(stop_managed_porteiro(root))
    concluir("stack", "stop")
    return _report(steps)


def orchestrator_command(powershell: str, script: Path) -> list[str]:
    return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]


def launch_orchestrator(root: Path) -> OperationResult:
    """Abre o HUD original numa janela nova. Fechar essa janela dispara o Stop-Tudo do script."""
    script = _under_root(root, _ORCHESTRATOR)
    if not script.is_file():
        raise OperationError("iniciar_servicos.ps1 não encontrado na raiz do projeto.")
    if os.name != "nt":
        raise OperationError(
            "O orquestrador é um script PowerShell do Windows. Neste sistema, use «Iniciar núcleo» e os botões de cada stack."
        )
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise OperationError("PowerShell não encontrado.")
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(
        orchestrator_command(powershell, script),
        cwd=str(root),
        shell=False,
        start_new_session=True,
        creationflags=flags,
    )
    return OperationResult(True, "HUD do núcleo", "HUD do núcleo aberto numa janela nova.")
