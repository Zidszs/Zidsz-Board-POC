import json
import re
from pathlib import Path

import pytest

from control_plane.auth import (
    AuthError,
    account_exists,
    create_user,
    entrar_com_token,
    load_users,
    main,
    reset_account,
    users_path,
)
from control_plane.binding import vinculo_ativo
from control_plane.user_token import emitir


def _token(tmp_path, nome="Gabriel", **listas):
    ver = listas.get("ver", ["n8n"])
    operar = listas.get("operar", ["n8n"])
    abrir = listas.get("abrir", ["n8n"])
    create_user(tmp_path, nome, ver=ver, operar=operar, abrir=abrir)
    return emitir(tmp_path, nome, ver=ver, operar=operar, abrir=abrir, por="admin", ip="127.0.0.1")


def test_conta_nova_nao_tem_senha_e_o_legado_nao_entra(tmp_path):
    legado = {
        "username": "admin",
        "salt": "aa" * 16,
        "hash": "bb" * 32,
        "n": 2**14,
        "r": 8,
        "p": 1,
    }
    pasta = tmp_path / ".n8groker"
    pasta.mkdir()
    (pasta / "auth.json").write_text(json.dumps(legado), encoding="utf-8")
    (pasta / "audit.jsonl").write_text('{"acao":"antiga"}\n', encoding="utf-8")
    token = _token(tmp_path)
    assert not (pasta / "auth.json").is_file()
    assert (pasta / "auth.json.legado").is_file()
    with pytest.raises(AuthError):
        entrar_com_token(tmp_path, "senha-temporaria", ip="203.0.113.10", modo="console")
    sessao = entrar_com_token(tmp_path, token, ip="203.0.113.10", modo="console")
    assert sessao["must_change"] is False
    assert sessao["ver"] == ["n8n"]
    assert sessao["operar"] == ["n8n"]
    assert sessao["abrir"] == ["n8n"]
    bruto = (pasta / "users.json").read_text(encoding="utf-8")
    assert "hash" not in bruto
    assert token not in bruto
    assert reset_account(tmp_path) is False
    assert account_exists(tmp_path) is True
    assert (pasta / "audit.jsonl").read_text(encoding="utf-8").count("antiga") == 1
    assert main(["--reset", "--root", str(tmp_path)]) == 0
    assert users_path(tmp_path).is_file()
    assert load_users(tmp_path)


def test_borda_sem_vinculo_fica_aguardando(tmp_path):
    token = _token(tmp_path, ver=["scout"], operar=["n8n"], abrir=[])
    ip = "203.0.113.40"
    sessao = entrar_com_token(tmp_path, token, ip=ip, modo="edge")
    assert sessao["aguardando"] is True
    assert sessao["ver"] == []
    assert sessao["operar"] == []
    pasta = tmp_path / "n8n" / "storage" / "Porteiro"
    pasta.mkdir(parents=True)
    (pasta / "controle_acesso.json").write_text(
        json.dumps(
            {
                "visitantes": [
                    {"ip": ip, "status": "aprovado", "conta_vinculada": "", "vinculo": ""}
                ]
            }
        ),
        encoding="utf-8",
    )
    assert vinculo_ativo(tmp_path, ip, "Gabriel") is False
    ainda = entrar_com_token(tmp_path, token, ip=ip, modo="edge")
    assert ainda["aguardando"] is True
    (pasta / "controle_acesso.json").write_text(
        json.dumps(
            {
                "visitantes": [
                    {
                        "ip": ip,
                        "status": "aprovado",
                        "conta_vinculada": "Gabriel",
                        "vinculo": "ativo",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    liberada = entrar_com_token(tmp_path, token, ip=ip, modo="edge")
    assert liberada["aguardando"] is False
    assert liberada["ver"] == ["scout"]
    assert liberada["operar"] == ["n8n"]


def test_rejeita_usuario_ruim_e_nao_cria_senha(tmp_path):
    with pytest.raises(AuthError):
        create_user(tmp_path, "ab")
    with pytest.raises(AuthError):
        create_user(tmp_path, "admin;rm")
    assert main(["--root", str(tmp_path)]) == 2
    create_user(tmp_path, "ana")
    bruto = (tmp_path / ".n8groker" / "users.json").read_text(encoding="utf-8")
    assert "hash" not in bruto
    assert "must_change_password" not in bruto


def test_gitignore_nao_versiona_a_conta():
    text = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert ".n8groker/" in text


def test_painel_nao_tem_tela_de_criar_admin():
    app = (Path(__file__).resolve().parents[1] / "control_plane" / "app.py").read_text(encoding="utf-8")
    assert "Criar conta do administrador" not in app
    assert "Senha inicial" not in app
    assert "Definir senha temporária" not in app
    assert "Trocar a senha" not in app
    assert "Emitir token para" in app
    assert "Rotacionar chave" in app
    assert "Token de acesso" in app
    gravar = app.split("def _gravar_sessao", 1)[1].split("\ndef ", 1)[0]
    limpar = app.split("def _limpar_sessao", 1)[1].split("\ndef ", 1)[0]
    chaves_sessao = set(re.findall(r'"(cp_[^"]+)"', gravar + limpar))
    formularios = set(re.findall(r'st\.form\("([^"]+)"', app))
    assert "cp_admin" in chaves_sessao
    assert formularios
    assert not (formularios & chaves_sessao)
    assert "client_ip_from_header" in app
    assert "http://127.0.0.1:5676" in app
    assert "_require_login" in app
    main_fn = app.split("def main", 1)[1]
    assert main_fn.index("_exigir_borda") < main_fn.index("_require_login")
    assert main_fn.index("_require_login") < main_fn.index("render_scout_tab")


def test_hud_user_tem_um_campo_e_o_console_mantem_o_admin():
    app = (Path(__file__).resolve().parents[1] / "control_plane" / "app.py").read_text(encoding="utf-8")
    login = app.split("def _require_login", 1)[1].split("\ndef ", 1)[0]
    edge = login.split('if _modo() == "edge":', 1)[1].split("else:", 1)[0]
    assert 'rotulo_token = "Token"' in edge
    assert "Token de acesso" not in edge
    assert "Token de admin" not in edge
    console = login.split("else:", 1)[1]
    assert 'rotulo_token = "Token de acesso"' in console
    admin = login.split('if _modo() != "edge":', 1)[1]
    assert "Token de admin" in admin
    assert "cp_form_admin" in admin
    assert "aceitar(" in admin
    assert "normalizar_token" in login
