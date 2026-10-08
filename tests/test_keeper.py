"""Keeper: sem porta, um processo por pedido, timeout e a privada fora do painel."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from control_plane.admin_token import init_keys, issue, key_path, ler_geracao
from control_plane.auth import AuthError, create_user, load_users
from control_plane.keeper import main as keeper_main
from control_plane.keeper_cliente import (
    BANNER_KEEPER_FORA,
    TIMEOUT_S,
    chave_liberada,
    chamar,
    consultar_uma_vez,
    emitir_token,
    executar,
    linha_diagnostico,
    revogar_conta_keeper,
    revogar_jti_keeper,
    rotacionar_keeper,
)
from control_plane.user_token import conferir, garantir, listar


def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _rodar(tmp_path: Path, pedido: str, corpo: dict | None = None) -> subprocess.CompletedProcess:
    from control_plane.keeper_cliente import _ambiente, _raiz_codigo

    entrada = b""
    if corpo is not None:
        entrada = json.dumps(corpo).encode("utf-8")
    return subprocess.run(
        [sys.executable, "-m", "control_plane.keeper", pedido, "--root", str(tmp_path)],
        input=entrada,
        capture_output=True,
        cwd=str(_raiz_codigo()),
        env=_ambiente(),
        check=False,
    )


def test_status_segue_o_contrato_e_nao_imprime_a_chave(tmp_path):
    segredo = b"SEGREDO-MAQUINA-0123456789ABCDEF"
    assert len(segredo) == 32
    recusa = _rodar(tmp_path, "status")
    assert recusa.returncode == 2
    assert json.loads(recusa.stdout.decode("utf-8")) == {"ok": False, "motivo": "chave"}
    assert segredo.decode("ascii") not in recusa.stdout.decode("utf-8", "replace")
    pasta = tmp_path / ".n8groker"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "maquina.key").write_bytes(segredo)
    ok = _rodar(tmp_path, "status")
    assert ok.returncode == 0
    texto = ok.stdout.decode("utf-8")
    assert json.loads(texto) == {"ok": True, "motivo": "ok"}
    assert texto.strip().count("\n") == 0
    assert segredo.hex() not in texto
    assert segredo.decode("ascii") not in texto
    assert segredo.decode("ascii") not in ok.stderr.decode("utf-8", "replace")


def test_status_nao_abre_porta(tmp_path, monkeypatch, capsys):
    chamadas = []
    original = socket.socket

    class Vigia(original):
        def bind(self, *args, **kwargs):
            chamadas.append("bind")
            return super().bind(*args, **kwargs)

        def listen(self, *args, **kwargs):
            chamadas.append("listen")
            return super().listen(*args, **kwargs)

    monkeypatch.setattr(socket, "socket", Vigia)
    assert keeper_main(["status", "--root", str(tmp_path)]) == 2
    assert chamadas == []
    assert "ok" in capsys.readouterr().out


def test_abrir_nao_grava_ticket_nem_admin_key(tmp_path):
    resposta = chamar(tmp_path, "abrir")
    assert resposta["ok"] is False
    assert resposta["motivo"] == "formato"
    assert not (tmp_path / ".n8groker" / "admin-abrir.ticket").exists()
    assert not (tmp_path / ".n8groker" / "admin.key").exists()


def test_pid_nasce_com_o_processo_e_some_ao_sair(tmp_path):
    from control_plane.keeper_cliente import _ambiente, _raiz_codigo

    proc = subprocess.Popen(
        [sys.executable, "-m", "control_plane.keeper", "emitir", "--root", str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(_raiz_codigo()),
        env=_ambiente(),
    )
    pid_path = tmp_path / ".n8groker" / "keeper.pid"
    prazo = time.time() + 3
    while time.time() < prazo and not pid_path.exists():
        if proc.poll() is not None:
            break
        time.sleep(0.02)
    assert pid_path.is_file()
    assert pid_path.read_text(encoding="ascii").strip() == str(proc.pid)
    assert proc.stdin is not None
    proc.stdin.write(b"{}")
    proc.stdin.close()
    proc.wait(timeout=3)
    saida = proc.stdout.read() if proc.stdout is not None else b""
    erro = proc.stderr.read() if proc.stderr is not None else b""
    assert proc.returncode == 2
    assert not pid_path.exists()
    corpo = json.loads(saida.decode("utf-8"))
    assert corpo["ok"] is False
    assert corpo["motivo"] == "conta"
    assert "jwt" not in corpo
    assert "BEGIN" not in erro.decode("utf-8", "replace")


def test_estouro_mata_o_filho_e_nao_repete_no_mesmo_rerun(tmp_path):
    assert TIMEOUT_S == 3
    marca = tmp_path / "pid-filho"
    resultado = executar(
        [sys.executable, "-c", "import os,sys,time,pathlib; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)", str(marca)],
        b"",
        0.4,
    )
    assert resultado["fora"] is True
    pid = int(marca.read_text(encoding="ascii"))
    assert _vivo(pid) is False
    chamadas = {"n": 0}

    def obter():
        chamadas["n"] += 1
        return {"ok": False, "fora": True}

    estado = {}
    assert consultar_uma_vez(estado, obter)["fora"] is True
    assert consultar_uma_vez(estado, obter)["fora"] is True
    assert chamadas["n"] == 1
    assert consultar_uma_vez({}, obter)["fora"] is True
    assert chamadas["n"] == 2
    assert linha_diagnostico({"ok": False, "fora": True}) == BANNER_KEEPER_FORA
    assert linha_diagnostico({"ok": False, "motivo": "chave"}) == ""
    assert chave_liberada({"ok": False, "motivo": "chave"}) is True
    assert chave_liberada({"ok": False, "fora": True}) is False


def test_painel_emite_sem_ler_a_privada_e_nao_apaga_jti(tmp_path, monkeypatch):
    garantir(tmp_path)
    create_user(tmp_path, "ana", ver=["n8n"], operar=["n8n"], abrir=["n8n"])
    original = Path.read_bytes

    def vigia(self, *args, **kwargs):
        if self.name in {"usuario.key", "admin.key", "maquina.key"}:
            raise AssertionError(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", vigia)
    primeiro = emitir_token(tmp_path, "ana", ver=["n8n"], operar=["n8n"], abrir=["n8n"], ip="127.0.0.1")
    segundo = emitir_token(tmp_path, "ana", ver=["n8n"], operar=[], abrir=["n8n"], ip="127.0.0.1")
    monkeypatch.undo()
    assert conferir(tmp_path, primeiro)["sub"] == "ana"
    assert conferir(tmp_path, segundo)["sub"] == "ana"
    assert len({item["jti"] for item in listar(tmp_path, "ana")}) == 2
    trilha = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert '"passo": "auth.emitir"' in trilha
    assert primeiro not in trilha
    assert segundo not in trilha
    with pytest.raises(AuthError):
        emitir_token(tmp_path, "ninguem", ver=[], operar=[], abrir=[], ip="127.0.0.1")


def test_revogar_e_rotacionar_usuario_passam_pelo_keeper(tmp_path):
    from scout.core.sessao_cookie import caminho_geracao, emitir as emitir_cookie, garantir as garantir_sessao, verificar
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    garantir(tmp_path)
    create_user(tmp_path, "ana", ver=["n8n"], operar=["n8n"], abrir=["n8n"])
    token = emitir_token(tmp_path, "ana", ver=["n8n"], operar=["n8n"], abrir=["n8n"], ip="127.0.0.1")
    jti = conferir(tmp_path, token)["jti"]
    garantir_sessao(tmp_path)
    privada = (tmp_path / ".n8groker" / "sessao.key").read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    cookie = emitir_cookie(privada, {"u": "ana", "ip": "203.0.113.10", "g": 1, "abrir": ["n8n"]})
    assert verificar(cookie, publica, caminho_geracao(tmp_path), "203.0.113.10") is not None
    geracao_admin = ler_geracao(tmp_path)
    revogar_jti_keeper(tmp_path, jti, ip="127.0.0.1")
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, token, ip="203.0.113.10")
    assert exc.value.motivo == "revogado"
    assert verificar(cookie, publica, caminho_geracao(tmp_path), "203.0.113.10") is None
    assert ler_geracao(tmp_path) == geracao_admin
    outro = emitir_token(tmp_path, "ana", ver=["n8n"], operar=["n8n"], abrir=["n8n"], ip="127.0.0.1")
    rotacionar_keeper(tmp_path, "usuario", ip="127.0.0.1")
    with pytest.raises(AuthError) as giro:
        conferir(tmp_path, outro, ip="203.0.113.10")
    assert giro.value.motivo == "rotacionado"
    assert ler_geracao(tmp_path) == geracao_admin
    recusa = chamar(tmp_path, "revogar-jti", {"jti": "curto"})
    assert recusa["ok"] is False
    assert recusa["motivo"] == "formato"


def test_revogar_conta_inativa_e_conta_inexistente_recusa(tmp_path):
    garantir(tmp_path)
    create_user(tmp_path, "ana", ver=["n8n"], operar=[], abrir=[])
    token = emitir_token(tmp_path, "ana", ver=["n8n"], operar=[], abrir=[], ip="127.0.0.1")
    revogar_conta_keeper(tmp_path, "ana", ip="127.0.0.1")
    conta = next(item for item in load_users(tmp_path) if item["username"] == "ana")
    assert conta["status"] == "inativo"
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, token, ip="203.0.113.10")
    assert exc.value.motivo == "revogado"
    resposta = chamar(tmp_path, "revogar-conta", {"conta": "ninguem"})
    assert resposta["ok"] is False
    assert resposta["motivo"] == "conta"


def test_rotate_do_admin_estourado_nao_troca_a_chave(tmp_path, monkeypatch, capsys):
    from control_plane.admin_token import main

    init_keys(tmp_path)
    antes = key_path(tmp_path).read_bytes()

    class Lento:
        def __init__(self, *_args, **_kwargs):
            self.pid = 4242
            self.killed = False

        def communicate(self, data=None, timeout=None):
            raise subprocess.TimeoutExpired(cmd="keeper", timeout=timeout or 1)

        def kill(self):
            self.killed = True

    criado = {}

    def popen(*args, **kwargs):
        proc = Lento()
        criado["proc"] = proc
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)
    assert main(["--rotate", "--root", str(tmp_path)]) == 2
    saida = capsys.readouterr().out
    assert BANNER_KEEPER_FORA in saida
    assert key_path(tmp_path).read_bytes() == antes
    assert ler_geracao(tmp_path) == 0
    assert criado["proc"].killed is True


def test_init_continua_sem_o_keeper(tmp_path, monkeypatch, capsys):
    from control_plane.admin_token import main

    def estoura(*_args, **_kwargs):
        raise AssertionError("keeper")

    monkeypatch.setattr(subprocess, "Popen", estoura)
    assert main(["--init", "--root", str(tmp_path)]) == 0
    token = capsys.readouterr().out.strip().splitlines()[0]
    assert key_path(tmp_path).is_file()
    from control_plane.admin_token import conferir

    conferir(tmp_path, token)


def test_inventario_le_o_disco_sem_a_privada(tmp_path, monkeypatch):
    from control_plane.inventario import FRASE_SESSAO, texto_inventario

    garantir(tmp_path)
    init_keys(tmp_path)
    create_user(tmp_path, "ana", ver=["n8n"], operar=[], abrir=[])
    token = emitir_token(tmp_path, "ana", ver=["n8n"], operar=[], abrir=[], ip="127.0.0.1")
    jti = conferir(tmp_path, token)["jti"]
    pasta = tmp_path / ".n8groker"
    segredo = b"SEGREDO-MAQUINA-0123456789ABCDEF"
    (pasta / "maquina.key").write_bytes(segredo)
    (pasta / "sessao.key").write_bytes(b"sessao-secreta-que-nao-sai")
    (pasta / "porteiro-hmac.key").write_bytes(b"hmac-secreto-que-nao-sai")
    (pasta / "porteiro-painel.token").write_text("token-painel-secreto\n", encoding="utf-8")
    (pasta / "porteiro-n8n.token").write_text("token-n8n-secreto\n", encoding="utf-8")
    original = Path.read_bytes

    def vigia(self, *args, **kwargs):
        if self.name.endswith(".key") or self.name.endswith(".token"):
            raise AssertionError(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", vigia)
    texto = texto_inventario(tmp_path)
    assert "admin.pub: sim" in texto
    assert "usuario.pub: sim" in texto
    assert "maquina.key: sim" in texto
    assert "admin-geracao: 0" in texto
    assert jti in texto
    assert "revogado não" in texto
    assert FRASE_SESSAO in texto
    assert "Quem tinha cookie entra de novo em /painel." in texto
    assert segredo.decode("ascii") not in texto
    assert "token-painel-secreto" not in texto
    assert "token-n8n-secreto" not in texto
    assert "hmac-secreto-que-nao-sai" not in texto
    assert "sessao-secreta-que-nao-sai" not in texto
    assert token not in texto
    (pasta / "maquina.key").unlink()
    assert "maquina.key: não" in texto_inventario(tmp_path)
