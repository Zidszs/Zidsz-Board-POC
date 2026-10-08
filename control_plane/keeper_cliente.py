"""Chama o Keeper: um processo por pedido, no máximo 3 segundos, sem porta.

Estouro mata o filho e vira Keeper fora. O mesmo rerun não tenta de novo.
A linha JSON não é relida da privada: quem lê a chave é o processo filho.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 3.0
BANNER_KEEPER_FORA = "Keeper fora, não dá para emitir nem girar chave"

_MENSAGENS = {
    "conta": "Conta não encontrada ou inativa. Nada foi alterado.",
    "formato": "O pedido foi recusado. Nada foi alterado.",
    "chave": "Falta a chave nesta máquina. Nada foi emitido nem girado.",
    "registro": "O registro não pôde ser lido. A sessão de admin continua.",
}


class KeeperFora(Exception):
    """O filho não respondeu a tempo, ou nem chegou a responder JSON."""


def mensagem_recusa(motivo: str) -> str:
    return _MENSAGENS.get(motivo, "O Keeper recusou. Nada foi alterado.")


def linha_diagnostico(resposta: dict) -> str:
    """Uma linha, sem segredo. Só quando o processo não respondeu."""
    if resposta.get("fora"):
        return BANNER_KEEPER_FORA
    return ""


def chave_liberada(resposta: dict) -> bool:
    """Emitir, revogar e girar seguem se o Keeper respondeu. Chave ausente não é fora."""
    return not bool(resposta.get("fora"))


def consultar_uma_vez(estado: dict, obter) -> dict:
    """A segunda consulta no mesmo dict não dispara outro processo."""
    guardado = estado.get("_keeper_resposta")
    if isinstance(guardado, dict):
        return guardado
    resposta = obter()
    if not isinstance(resposta, dict):
        resposta = {"ok": False, "fora": True}
    estado["_keeper_resposta"] = resposta
    return resposta


def _raiz_codigo() -> Path:
    return Path(__file__).resolve().parent.parent


def _argv(pedido: str, root: Path) -> list[str]:
    return [sys.executable, "-m", "control_plane.keeper", pedido, "--root", str(root)]


def _ambiente() -> dict[str, str]:
    env = os.environ.copy()
    raiz = str(_raiz_codigo())
    atual = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = raiz if not atual else raiz + os.pathsep + atual
    return env


def _limpar_pid_morto(root: Path, pid: int) -> None:
    path = Path(root) / ".n8groker" / "keeper.pid"
    try:
        atual = path.read_text(encoding="ascii").strip()
    except OSError:
        return
    if atual != str(pid):
        return
    try:
        path.unlink()
    except OSError:
        return


def _ler_json(saida: bytes) -> dict | None:
    texto = saida.decode("utf-8", errors="replace").strip()
    if not texto:
        return None
    linha = texto.splitlines()[-1].strip()
    try:
        data = json.loads(linha)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "ok" not in data:
        return None
    return data


def executar(argv: list[str], entrada: bytes, timeout: float, raiz: Path | None = None) -> dict:
    """Roda o filho. Estouro mata esse PID e devolve fora, sem segunda tentativa."""
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_raiz_codigo()),
            env=_ambiente(),
        )
    except OSError:
        return {"ok": False, "fora": True}
    try:
        saida, _erro = proc.communicate(entrada, timeout=timeout)
    except subprocess.TimeoutExpired:
        pid = proc.pid
        proc.kill()
        try:
            proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        if raiz is not None and pid:
            _limpar_pid_morto(raiz, pid)
        return {"ok": False, "fora": True}
    data = _ler_json(saida)
    if data is None:
        return {"ok": False, "fora": True}
    return data


def chamar(root: Path, pedido: str, corpo: dict | None = None, *, timeout: float = TIMEOUT_S) -> dict:
    entrada = b""
    if corpo is not None:
        entrada = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
    return executar(_argv(pedido, Path(root)), entrada, timeout, raiz=Path(root))


def _exigir(resposta: dict) -> None:
    if resposta.get("fora"):
        raise KeeperFora(BANNER_KEEPER_FORA)
    if not resposta.get("ok"):
        motivo = str(resposta.get("motivo") or "formato")
        from control_plane.auth import AuthError

        raise AuthError(mensagem_recusa(motivo), motivo=motivo)


def emitir_token(
    root: Path,
    conta: str,
    *,
    ver: list,
    operar: list,
    abrir: list,
    ip: str,
) -> str:
    resposta = chamar(
        root,
        "emitir",
        {"conta": conta, "ver": list(ver), "operar": list(operar), "abrir": list(abrir), "ip": ip},
    )
    _exigir(resposta)
    jwt = resposta.get("jwt")
    if not isinstance(jwt, str) or jwt.count(".") != 2:
        from control_plane.auth import AuthError

        raise AuthError(mensagem_recusa("chave"), motivo="chave")
    return jwt


def revogar_jti_keeper(root: Path, jti: str, *, ip: str) -> None:
    _exigir(chamar(root, "revogar-jti", {"jti": jti, "ip": ip}))


def revogar_conta_keeper(root: Path, conta: str, *, ip: str) -> None:
    _exigir(chamar(root, "revogar-conta", {"conta": conta, "ip": ip}))


def rotacionar_keeper(root: Path, alvo: str, *, ip: str) -> None:
    _exigir(chamar(root, "rotacionar", {"alvo": alvo, "ip": ip}))
