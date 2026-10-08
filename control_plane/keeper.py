"""Keeper: um processo por pedido, sem porta.

Entrada: `python -m control_plane.keeper <pedido>`, com JSON opcional no stdin.
Saída: uma linha JSON e código 0 ou 2. A linha não leva chave privada.
Só `emitir` devolve o JWT, uma vez, no campo `jwt`.

O filho grava `.n8groker/keeper.pid` ao nascer e apaga ao sair.
`abrir` não entra neste lote: o pedido é recusado e não grava ticket.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCOUT = Path(__file__).resolve().parents[1] / "Scout_OSINT_Docker"
if str(_SCOUT) not in sys.path:
    sys.path.insert(0, str(_SCOUT))

from control_plane.auth import AuthError
from control_plane.trilha_auth import MOTIVOS

_PEDIDOS = frozenset({"status", "emitir", "revogar-jti", "revogar-conta", "rotacionar"})


def raiz_padrao() -> Path:
    return Path(__file__).resolve().parent.parent


def _dentro(root: Path, nome: str) -> Path:
    base = Path(root).resolve()
    path = (base / ".n8groker" / nome).resolve()
    if path.parent != (base / ".n8groker").resolve():
        raise AuthError("Arquivo fora da pasta local.", motivo="formato")
    return path


def _linha(ok: bool, motivo: str, **extra: object) -> int:
    if motivo not in MOTIVOS:
        motivo = "formato"
    corpo: dict[str, object] = {"ok": ok, "motivo": motivo}
    for chave, valor in extra.items():
        if chave == "jwt" and isinstance(valor, str):
            corpo["jwt"] = valor
    sys.stdout.write(json.dumps(corpo, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if ok else 2


def _gravar_pid(root: Path, pid: int) -> None:
    from scout.core.gravar_arquivo import gravar_bytes

    gravar_bytes(_dentro(root, "keeper.pid"), f"{pid}\n".encode("ascii"))


def _soltar_pid(root: Path, pid: int) -> None:
    path = _dentro(root, "keeper.pid")
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


def _maquina_presente(root: Path) -> bool:
    path = _dentro(root, "maquina.key")
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _corpo_stdin() -> dict:
    bruto = sys.stdin.buffer.read()
    if not bruto.strip():
        return {}
    try:
        data = json.loads(bruto.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuthError("Pedido recusado.", motivo="formato") from exc
    if not isinstance(data, dict):
        raise AuthError("Pedido recusado.", motivo="formato")
    return data


def _ip(corpo: dict) -> str:
    valor = corpo.get("ip")
    if not isinstance(valor, str) or not valor.strip():
        return "127.0.0.1"
    return valor.strip()[:64]


def _listas(corpo: dict, nome: str) -> list:
    valor = corpo.get(nome, [])
    if valor is None:
        return []
    if not isinstance(valor, list):
        raise AuthError("Pedido recusado.", motivo="formato")
    return valor


def _status(root: Path) -> int:
    if not _maquina_presente(root):
        return _linha(False, "chave")
    return _linha(True, "ok")


def _emitir(root: Path, corpo: dict) -> int:
    from control_plane.user_token import emitir

    conta = corpo.get("conta")
    if not isinstance(conta, str) or not conta.strip():
        return _linha(False, "conta")
    try:
        jwt = emitir(
            root,
            conta,
            ver=_listas(corpo, "ver"),
            operar=_listas(corpo, "operar"),
            abrir=_listas(corpo, "abrir"),
            por="admin",
            ip=_ip(corpo),
        )
    except AuthError as exc:
        return _linha(False, exc.motivo or "conta")
    return _linha(True, "ok", jwt=jwt)


def _revogar_jti(root: Path, corpo: dict) -> int:
    from control_plane.user_token import revogar_jti

    jti = corpo.get("jti")
    if not isinstance(jti, str):
        return _linha(False, "formato")
    try:
        revogar_jti(root, jti, por="admin", ip=_ip(corpo))
    except AuthError as exc:
        return _linha(False, exc.motivo or "formato")
    return _linha(True, "ok")


def _revogar_conta(root: Path, corpo: dict) -> int:
    from control_plane.auth import desativar_conta

    conta = corpo.get("conta")
    if not isinstance(conta, str) or not conta.strip():
        return _linha(False, "conta")
    try:
        desativar_conta(root, conta, ip=_ip(corpo))
    except AuthError as exc:
        return _linha(False, exc.motivo or "conta")
    return _linha(True, "ok")


def _rotacionar(root: Path, corpo: dict) -> int:
    alvo = corpo.get("alvo")
    if alvo == "admin":
        from control_plane.admin_token import rotate_keys

        try:
            rotate_keys(root, ip=_ip(corpo))
        except AuthError as exc:
            return _linha(False, exc.motivo or "chave")
        return _linha(True, "ok")
    if alvo == "usuario":
        from control_plane.user_token import rotacionar

        try:
            rotacionar(root, por="admin", ip=_ip(corpo))
        except AuthError as exc:
            return _linha(False, exc.motivo or "chave")
        return _linha(True, "ok")
    return _linha(False, "formato")


def _despachar(root: Path, pedido: str) -> int:
    if pedido == "status":
        return _status(root)
    if pedido == "abrir":
        return _linha(False, "formato")
    if pedido not in _PEDIDOS:
        return _linha(False, "formato")
    try:
        corpo = _corpo_stdin()
    except AuthError as exc:
        return _linha(False, exc.motivo or "formato")
    if pedido == "emitir":
        return _emitir(root, corpo)
    if pedido == "revogar-jti":
        return _revogar_jti(root, corpo)
    if pedido == "revogar-conta":
        return _revogar_conta(root, corpo)
    return _rotacionar(root, corpo)


def _parse(argv: list[str]) -> tuple[str, Path]:
    args = list(argv)
    root = raiz_padrao()
    if "--root" in args:
        index = args.index("--root")
        try:
            root = Path(args[index + 1])
        except IndexError as exc:
            raise AuthError("Informe a pasta depois de --root.", motivo="formato") from exc
        del args[index : index + 2]
    pedidos = [item for item in args if item and not item.startswith("-")]
    if len(pedidos) != 1:
        raise AuthError("Pedido recusado.", motivo="formato")
    return pedidos[0], root


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        pedido, root = _parse(args)
    except AuthError as exc:
        return _linha(False, exc.motivo or "formato")
    pid = os.getpid()
    try:
        _gravar_pid(root, pid)
    except (OSError, AuthError):
        return _linha(False, "chave")
    try:
        return _despachar(root, pedido)
    except AuthError as exc:
        return _linha(False, exc.motivo or "chave")
    except Exception:
        return _linha(False, "chave")
    finally:
        try:
            _soltar_pid(root, pid)
        except (AuthError, OSError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
