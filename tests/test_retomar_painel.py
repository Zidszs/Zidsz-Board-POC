"""Recarregar /painel depois de aprovar o navegador não pede o token outra vez."""

import json
import os

from control_plane.auth import create_user
from control_plane.sessao_host import ao_criar
from control_plane.user_token import conferir, emitir
from scout.core.origem import cookies_de_prova, gerar_chave, horario_utc, impressao
from scout.core.sessao_cookie import caminho_chave, caminho_geracao, emitir as emitir_cookie
from scout.core.sessao_cookie import garantir as garantir_sessao
from scout.core.sessao_cookie import subir_geracao

IP = "203.0.113.10"


def _vinculo(tmp_path):
    pasta = tmp_path / "n8n" / "storage" / "Porteiro"
    pasta.mkdir(parents=True)
    (pasta / "controle_acesso.json").write_text(
        json.dumps(
            {
                "visitantes": [
                    {
                        "ip": IP,
                        "status": "aprovado",
                        "conta_vinculada": "ana",
                        "vinculo": "ativo",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _cookie(tmp_path):
    create_user(tmp_path, "ana", ver=[], operar=[], abrir=["n8n"])
    ao_criar(tmp_path, "ana")
    garantir_sessao(tmp_path)
    _vinculo(tmp_path)
    privada_ec, spki = gerar_chave()
    origem = "orig-1"
    nonce = "nonce-1"
    horario = horario_utc()
    prova = cookies_de_prova(privada_ec, spki, origem, nonce, horario)
    sessao = emitir_cookie(
        caminho_chave(tmp_path).read_bytes(),
        {
            "u": "ana",
            "ip": IP,
            "g": 1,
            "abrir": ["n8n"],
            "origem": origem,
            "dispositivo": impressao(spki),
            "nonce": nonce,
            "sid": "ab" * 8,
        },
    )
    return prova + "; n8groker_sessao=" + sessao, sessao


class _Tela:
    def __init__(self, cookie):
        self.session_state = {"cp_ip": IP}
        self.context = type("Ctx", (), {"headers": {"cookie": cookie}})()
        self.chamadas = []
        self.textos = []

    def __getattr__(self, nome):
        def metodo(*args, **_kwargs):
            self.chamadas.append(nome)
            if args and isinstance(args[0], str):
                self.textos.append(args[0])
            return None

        return metodo


class _Settings:
    def __init__(self, root):
        self.root = root


def test_recarregar_retoma_pelo_cookie_sem_token(tmp_path, monkeypatch):
    import control_plane.app as painel

    cabecalho, sessao = _cookie(tmp_path)
    token = emitir(
        tmp_path,
        "ana",
        ver=[],
        operar=[],
        abrir=["n8n"],
        por="admin",
        ip="127.0.0.1",
    )
    jti_antes = (tmp_path / ".n8groker" / "usuario-jti.json").read_text(encoding="utf-8")
    auth_antes = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    tela = _Tela(cabecalho)
    monkeypatch.setattr(painel, "st", tela)
    monkeypatch.setenv("PANEL_MODE", "edge")
    assert painel._require_login(_Settings(tmp_path)) is True
    assert tela.session_state.get("cp_user") == "ana"
    assert tela.session_state.get("cp_abrir") == ["n8n"]
    assert tela.session_state.get("cp_aguardando") is False
    assert "subheader" not in tela.chamadas
    assert tela.session_state.get("cp_acesso", "") == ""
    guardado = json.dumps(tela.session_state, default=str)
    assert token not in guardado
    assert sessao not in guardado
    assert conferir(tmp_path, token, ip="203.0.113.44")["jti"]
    assert (tmp_path / ".n8groker" / "usuario-jti.json").read_text(encoding="utf-8") == jti_antes
    assert (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8") == auth_antes
    assert painel._require_login(_Settings(tmp_path)) is True
    subir_geracao(caminho_geracao(tmp_path), "ana")
    tela.chamadas.clear()
    assert painel._require_login(_Settings(tmp_path)) is False
    assert tela.session_state.get("cp_authenticated") is not True
    assert "subheader" in tela.chamadas


def test_sid_revogado_nao_retoma_nem_emite_cookie(tmp_path, monkeypatch):
    """Revogar o sid devolve a tela do token e o iframe não assina outro cookie."""
    import control_plane.app as painel
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from control_plane.auth import entrar_com_token, load_users
    from scout.core.politica_portal import montar_politica
    from scout.core.porta_apps import antes_do_porteiro
    from scout.core.sessao_cookie import ler_geracao, verificar
    from scout.core.ticket_sessao import emitir_ticket
    from scout.core.trilha import Trilha, pasta_de

    cabecalho, _sessao = _cookie(tmp_path)
    token = emitir(
        tmp_path,
        "ana",
        ver=[],
        operar=[],
        abrir=["n8n"],
        por="admin",
        ip="127.0.0.1",
    )
    geracao_antes = (tmp_path / ".n8groker" / "sessoes-geracao.json").read_text(encoding="utf-8")
    auth_antes = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    grade = Trilha(pasta_de(tmp_path))
    grade.revogar("ab" * 8)
    tela = _Tela(cabecalho)
    monkeypatch.setattr(painel, "st", tela)
    monkeypatch.setenv("PANEL_MODE", "edge")
    assert painel._require_login(_Settings(tmp_path)) is False
    assert tela.session_state.get("cp_authenticated") is not True
    assert any("Cole o token" in texto for texto in tela.textos)
    assert load_users(tmp_path)[0]["status"] == "ativo"
    assert ler_geracao(caminho_geracao(tmp_path), "ana") == {"g": 1, "ativo": True}
    assert (tmp_path / ".n8groker" / "sessoes-geracao.json").read_text(encoding="utf-8") == geracao_antes
    assert (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8") == auth_antes
    assert conferir(tmp_path, token, ip="203.0.113.44")["jti"]

    chave = tmp_path / "porteiro-hmac.key"
    chave.write_bytes(b"k" * 32)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(chave))
    ticket = emitir_ticket(chave.read_bytes(), usuario="ana", ip=IP, geracao=1, abrir=["n8n"])
    privada = caminho_chave(tmp_path).read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()

    class _Fila:
        def consultar(self, ip, origem, conta, app):
            return {
                "status": "aprovado",
                "ip": ip,
                "conta_vinculada": "ana",
                "origem": "orig-1",
                "vinculo": "ativo",
            }

    fila = _Fila()
    politica = montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
        trilha=grade,
    )
    pedido = cabecalho + "; n8groker_desafio=nonce-1"
    sem_sessao = "; ".join(
        parte.strip()
        for parte in pedido.split(";")
        if parte.strip() and not parte.strip().startswith("n8groker_sessao=")
    )
    recusado = politica(
        {
            "caminho": "/painel/sessao",
            "query": "t=" + ticket,
            "headers": {"x-forwarded-for": IP, "cookie": pedido},
        },
        "127.0.0.1",
        {},
    )
    assert recusado["status"] == 403
    assert not any("n8groker_sessao=" in str(item) for item in (recusado.get("headers") or []))
    assert grade.sessoes() == []
    assert (tmp_path / ".n8groker" / "sessoes-geracao.json").read_text(encoding="utf-8") == geracao_antes

    fresco = politica(
        {
            "caminho": "/painel/sessao",
            "query": "t=" + ticket,
            "headers": {
                "x-forwarded-for": IP,
                "cookie": sem_sessao,
            },
        },
        "127.0.0.1",
        {},
    )
    assert fresco["status"] == 200
    assert any("n8groker_sessao=" in str(item) for item in fresco["headers"])

    de_novo = politica(
        {
            "caminho": "/painel/escolher",
            "query": "app=n8n&t=" + ticket,
            "headers": {"x-forwarded-for": IP, "cookie": pedido},
        },
        "127.0.0.1",
        {},
    )
    assert de_novo["status"] == 303
    linha = next(item for item in de_novo["headers"] if str(item).startswith("Set-Cookie:"))
    novo = verificar(linha.split("=", 1)[1].split(";", 1)[0], publica, caminho_geracao(tmp_path), IP)
    assert novo["sid"] != "ab" * 8
    assert load_users(tmp_path)[0]["status"] == "ativo"

    monkeypatch.setenv("PANEL_MODE", "console")
    assert painel._retomar_cookie(_Settings(tmp_path)) is None
    console = entrar_com_token(tmp_path, token, ip="127.0.0.1", modo="console")
    assert console["username"] == "ana"
    assert console["admin"] is False
    assert load_users(tmp_path)[0]["status"] == "ativo"
    assert ler_geracao(caminho_geracao(tmp_path), "ana") == {"g": 1, "ativo": True}


def test_cookie_sem_prova_de_origem_nao_retoma(tmp_path, monkeypatch):
    import control_plane.app as painel

    _cabecalho, sessao = _cookie(tmp_path)
    tela = _Tela("n8groker_sessao=" + sessao)
    monkeypatch.setattr(painel, "st", tela)
    monkeypatch.setenv("PANEL_MODE", "edge")
    assert painel._require_login(_Settings(tmp_path)) is False
    assert tela.session_state.get("cp_authenticated") is not True
    assert os.environ.get("PANEL_MODE") == "edge"
