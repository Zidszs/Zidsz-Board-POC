"""Grava sem trocar o inode de um arquivo que o Docker já montou.

No Docker Desktop do Windows, os.replace de um arquivo bind-mounted
devolve WinError 5. O container continua no inode antigo e a montagem quebra.
Arquivo que já existe é truncado no lugar, com nova tentativa se o arquivo
estiver aberto. Arquivo novo nasce com O_EXCL: ainda não há montagem.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def gravar_bytes(path: Path, data: bytes) -> None:
    destino = Path(path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.is_dir():
        raise IsADirectoryError(str(destino))
    if destino.is_file():
        _no_lugar(destino, data)
        return
    try:
        _criar(destino, data)
    except FileExistsError:
        _no_lugar(destino, data)


def substituir_bytes(path: Path, data: bytes) -> None:
    """Grava o conteúdo inteiro num temporário e troca com replace.

    Disco cheio ou processo morto no meio da escrita deixa o arquivo antigo
    inteiro: o temporário é que fica incompleto, e ele é apagado.
    Não usar em arquivo que o Docker monta sozinho. No Docker Desktop do
    Windows, replace muda o inode e o bind de arquivo único quebra.
    usuario.key, usuario.pub e usuario-jti.json não estão nesse compose.
    """
    destino = Path(path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.is_dir():
        raise IsADirectoryError(str(destino))
    fd, nome = tempfile.mkstemp(prefix="." + destino.name + ".", dir=str(destino.parent))
    tmp = Path(nome)
    try:
        os.write(fd, data)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    _chmod(tmp)
    ultimo: PermissionError | None = None
    espera = 0.02
    for _ in range(6):
        try:
            os.replace(tmp, destino)
            ultimo = None
            break
        except PermissionError as exc:
            ultimo = exc
            time.sleep(espera)
            espera = min(espera * 2, 0.4)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
    if ultimo is not None:
        tmp.unlink(missing_ok=True)
        raise ultimo
    _chmod(destino)
    try:
        dirfd = os.open(str(destino.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dirfd)
    except OSError:
        return
    finally:
        os.close(dirfd)


def renomear(origem: Path, destino: Path) -> None:
    """Troca de nome dentro de uma pasta montada. Repete se o Windows recusar."""
    ultimo: PermissionError | None = None
    espera = 0.02
    for _ in range(6):
        try:
            Path(origem).replace(destino)
            return
        except PermissionError as exc:
            ultimo = exc
            time.sleep(espera)
            espera = min(espera * 2, 0.4)
    if ultimo is not None:
        raise ultimo


def _chmod(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


def _no_lugar(path: Path, data: bytes) -> None:
    ultimo: PermissionError | None = None
    espera = 0.02
    for _ in range(6):
        try:
            with open(path, "r+b") as handle:
                handle.seek(0)
                handle.write(data)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            _chmod(path)
            return
        except PermissionError as exc:
            ultimo = exc
            time.sleep(espera)
            espera = min(espera * 2, 0.4)
    if ultimo is not None:
        raise ultimo


def _criar(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    _chmod(path)
