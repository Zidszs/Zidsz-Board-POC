"""Papéis nas funções, fila local e refresh. Sem docker de verdade."""

import json
from pathlib import Path

import pytest

from control_plane.actor import AccessDenied, allows, set_actor
from control_plane.auth import create_user
from control_plane.config import default_services
from control_plane.operations import restart_container
from control_plane.porteiro_admin import agir, aplicar, mensagem_recusa
from control_plane.support_chat import decide_tool, tool_schemas


def _ator(**extra):
    base = {
        "admin": False,
        "must_change": False,
        "aguardando": False,
        "ver": ["n8n"],
        "operar": [],
        "abrir": [],
        "username": "Gabriel",
        "ip": "203.0.113.40",
        "root": "/tmp/nao-usado",
    }
    base.update(extra)
    return base


def test_ver_nao_reinicia_e_chat_fora_da_lista_recusa(monkeypatch, tmp_path):
    monkeypatch.setenv("PANEL_MODE", "console")
    set_actor(_ator(root=str(tmp_path)))
    assert allows("ver", "n8n") is True
    assert allows("operar", "n8n") is False
    assert allows("stack") is False
    with pytest.raises(AccessDenied):
        restart_container("n8n_app", service_id="n8n")
    decisao = decide_tool("reiniciar_servico", {"service_id": "n8n"}, default_services())
    assert decisao.kind == "rejected"
    decisao = decide_tool("status_servico", {"service_id": "n8n"}, default_services())
    assert decisao.kind == "readonly"
    nomes = [item["function"]["name"] for item in tool_schemas(default_services())]
    assert "status_servico" in nomes
    assert "reiniciar_servico" not in nomes
    set_actor(_ator(must_change=True, operar=["n8n"], root=str(tmp_path)))
    assert allows("ver", "n8n") is False
    set_actor(_ator(aguardando=True, operar=["n8n"], root=str(tmp_path)))
    assert allows("operar", "n8n") is False
    set_actor(_ator(admin=True, root=str(tmp_path)))
    assert allows("stack") is True
    assert allows("backup") is True


def test_fila_local_atualiza_na_hora_e_ignora_webhook_404():
    vistos = []

    def transport(method, url, headers):
        vistos.append(url)
        if url.endswith("/n8n/fila"):
            corpo = {
                "visitantes": [
                    {
                        "ip": "203.0.113.40",
                        "status": "pendente",
                        "data_primeiro_acesso": "2026-10-01T12:00:00Z",
                        "conta_solicitada": "Gabriel",
                    }
                ]
            }
            return 200, json.dumps(corpo)
        if "/n8n/aprovar" in url:
            return 200, "Sucesso! O IP 203.0.113.40 agora esta aprovado."
        if "/n8n/solicitar" in url:
            return 200, json.dumps({"ok": True, "n8n_status": 404, "conta_solicitada": "Gabriel"})
        if "/n8n/bloquear" in url:
            return 200, "bloqueado"
        if "/n8n/vincular" in url:
            return 409, "O IP precisa estar aprovado antes do vinculo."
        return 500, "nao"

    fila = agir("solicitar", "203.0.113.40", "Gabriel", transport=transport)
    assert fila["ok"] is True
    assert fila["n8n_status"] == 404
    assert vistos[-1].startswith("http://127.0.0.1:5676/n8n/solicitar")
    session = {"porteiro_fila": [{"ip": "antigo"}]}
    recusa = aplicar(session, "vincular", "203.0.113.40", "Gabriel", transport=transport)
    assert recusa["ok"] is False
    assert "porteiro_fila" in session
    ok = aplicar(session, "aprovar", "203.0.113.40", transport=transport)
    assert ok["ok"] is True
    assert "porteiro_fila" not in session
    assert all("webhook" not in url for url in vistos)
    assert all(url.startswith("http://127.0.0.1:5676/") for url in vistos)


def test_plano_separa_navegador_novo_de_aprovar_o_ip():
    from control_plane.porteiro_admin import plano_da_fila

    pendente = {
        "status": "pendente",
        "origens": [{"origem": "orig-a", "status": "pendente"}],
    }
    plano = plano_da_fila(pendente)
    assert plano["aprovar"] == "orig-a"
    assert plano["navegadores"] == []
    assert plano["liberar"] == "orig-a"
    sem_origem = {"status": "pendente", "origens": []}
    assert plano_da_fila(sem_origem)["aprovar"] == ""
    aprovado = {
        "status": "aprovado",
        "origens": [
            {"origem": "antiga", "status": "aprovado"},
            {"origem": "nova", "status": "pendente"},
        ],
    }
    plano = plano_da_fila(aprovado)
    assert plano["aprovar"] == ""
    assert plano["navegadores"] == ["nova"]
    assert plano["vincular"] == "antiga"
    assert plano["liberar"] == "nova"


def test_liberar_aprova_e_vincula_no_mesmo_gesto():
    from control_plane.porteiro_admin import liberar

    vistos = []

    def transport(method, url, headers):
        vistos.append(url)
        if "/n8n/aprovar" in url:
            assert "origem=orig-a" in url
            return 200, "aprovada"
        if "/n8n/vincular" in url:
            assert "origem=orig-a" in url
            assert "conta=ana" in url
            return 200, "vinculado"
        return 500, "nao"

    session = {"porteiro_fila": [{"ip": "antigo"}]}
    resultado = liberar(session, "203.0.113.40", "ana", "orig-a", transport=transport, token="t")
    assert resultado["ok"] is True
    assert resultado["passo"] == "vincular"
    assert "porteiro_fila" not in session
    assert len(vistos) == 2
    assert "/n8n/aprovar?" in vistos[0]
    assert "/n8n/vincular?" in vistos[1]
    vistos.clear()
    sem = liberar(session, "203.0.113.40", "ana", "", transport=transport, token="t")
    assert sem["ok"] is False
    assert sem["passo"] == "origem"
    assert vistos == []

    def recusa(method, url, headers):
        vistos.append(url)
        return 400, "Origem obrigatoria. Dispositivo nao aprovado."

    falhou = liberar(session, "203.0.113.40", "ana", "orig-a", transport=recusa, token="t")
    assert falhou["ok"] is False
    assert falhou["passo"] == "aprovar"
    assert len(vistos) == 1


def test_tela_de_espera_diz_o_passo_e_o_token_continua(tmp_path):
    from control_plane.binding import mensagem_espera

    ip = "203.0.113.40"
    pasta = tmp_path / "n8n" / "storage" / "Porteiro"
    pasta.mkdir(parents=True)
    arquivo = pasta / "controle_acesso.json"
    assert "aprovar o IP" in mensagem_espera(tmp_path, ip, "ana")
    assert "token continua valendo" in mensagem_espera(tmp_path, ip, "ana")
    arquivo.write_text(
        json.dumps({"visitantes": [{"ip": ip, "status": "pendente"}]}),
        encoding="utf-8",
    )
    texto = mensagem_espera(tmp_path, ip, "ana")
    assert "Falta aprovar o IP" in texto
    assert "vincular" not in texto
    assert "token continua valendo" in texto
    arquivo.write_text(
        json.dumps(
            {"visitantes": [{"ip": ip, "status": "aprovado", "conta_vinculada": "", "vinculo": ""}]}
        ),
        encoding="utf-8",
    )
    texto = mensagem_espera(tmp_path, ip, "ana")
    assert "já está aprovado" in texto
    assert "Falta vincular a conta ana" in texto
    assert "token continua valendo" in texto
    arquivo.write_text("{", encoding="utf-8")
    assert "Não deu para ler" in mensagem_espera(tmp_path, ip, "ana")


def test_aba_admin_tem_liberar_e_aprovar_navegador():
    import ast

    app = (Path(__file__).resolve().parents[1] / "control_plane" / "app.py").read_text(encoding="utf-8")
    arvore = ast.parse(app)
    render = next(no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name == "_render_admin")
    pais = {}
    for no in ast.walk(render):
        for filho in ast.iter_child_nodes(no):
            pais[filho] = no
    bloqueios = []
    for no in ast.walk(render):
        if not isinstance(no, ast.If) or not isinstance(no.test, ast.Call):
            continue
        chamada = no.test
        if not isinstance(chamada.func, ast.Name) or chamada.func.id != "_button":
            continue
        if not chamada.args or not isinstance(chamada.args[0], ast.Constant):
            continue
        if chamada.args[0].value != "Bloquear o IP inteiro":
            continue
        acoes = []
        for sub in ast.walk(ast.Module(body=no.body, type_ignores=[])):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "_fila":
                if len(sub.args) >= 2 and isinstance(sub.args[1], ast.Constant):
                    acoes.append(sub.args[1].value)
        ancestrais = []
        cursor = no
        while cursor in pais:
            cursor = pais[cursor]
            ancestrais.append(cursor)
        dentro_de_navegador_novo = any(
            isinstance(item, ast.If) and "navegadores" in ast.dump(item.test) for item in ancestrais
        )
        dentro_do_cartao = any(isinstance(item, ast.For) for item in ancestrais)
        bloqueios.append((acoes, dentro_de_navegador_novo, dentro_do_cartao))
    assert bloqueios == [(["bloquear"], False, True)]
    assert '_button("Reprovar"' not in app
    assert "Reprovar" not in app
    assert "Liberar este IP para esta conta" in app
    assert "Aprovar este navegador" in app


def test_painel_avisa_origem_pendente_antes_da_lista(tmp_path):
    import ast

    from control_plane.binding import aviso_navegador_novo

    ip = "203.0.113.40"
    pasta = tmp_path / "n8n" / "storage" / "Porteiro"
    pasta.mkdir(parents=True)
    (pasta / "controle_acesso.json").write_text(
        json.dumps(
            {
                "visitantes": [
                    {
                        "ip": ip,
                        "status": "aprovado",
                        "conta_vinculada": "ana",
                        "vinculo": "ativo",
                        "origens": [
                            {"origem": "antiga", "status": "aprovado"},
                            {"origem": "nova", "status": "pendente"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    from control_plane.binding import aviso_para_painel, espera_registro_origem

    aviso = aviso_navegador_novo(tmp_path, ip, "nova")
    assert aviso == f"Navegador novo neste IP {ip}. Aguarde a aprovação deste navegador."
    assert aviso_navegador_novo(tmp_path, ip, "antiga") == ""
    assert aviso_navegador_novo(tmp_path, ip, "") == ""
    sem_cookie = aviso_para_painel(tmp_path, ip, "")
    assert sem_cookie == aviso
    assert aviso_para_painel(tmp_path, ip, "antiga") == ""
    assert espera_registro_origem("", "", 0) is True
    assert espera_registro_origem("", sem_cookie, 0) is False
    assert espera_registro_origem("antiga", "", 0) is False
    assert espera_registro_origem("", "", 2) is False
    app = (Path(__file__).resolve().parents[1] / "control_plane" / "app.py").read_text(encoding="utf-8")
    arvore = ast.parse(app)
    principal = next(no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name == "main")
    origem = next(no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name == "_origem_no_navegador")
    iframe = [
        no
        for no in ast.walk(origem)
        if isinstance(no, ast.Call)
        and isinstance(no.func, ast.Attribute)
        and no.func.attr == "iframe"
        and no.args
        and isinstance(no.args[0], ast.Constant)
        and no.args[0].value == "/painel/origem.html"
    ]
    assert iframe
    ordem = []
    for no in ast.walk(principal):
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name):
            ordem.append((no.lineno, no.func.id))
    ordem.sort()
    nomes = [nome for _, nome in ordem]
    assert nomes.index("_origem_no_navegador") < nomes.index("_require_login")
    avisos = [
        no
        for no in ast.walk(principal)
        if isinstance(no, ast.If) and isinstance(no.test, ast.Name) and no.test.id == "aviso_origem"
    ]
    assert len(avisos) == 1
    assert not any(isinstance(no, ast.Return) for no in ast.walk(ast.Module(body=avisos[0].body, type_ignores=[])))
    login = next(no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name == "_require_login")
    nomes_login = [
        no.func.id
        for no in ast.walk(login)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
    ]
    assert "_texto_origem_edge" in nomes_login


def test_recusa_da_fila_mostra_o_motivo_e_nao_o_json():
    assert mensagem_recusa('{"ok": false, "reason": "fila cheia"}') == "fila cheia"
    assert mensagem_recusa("Conta invalida.") == "Conta invalida."
    assert mensagem_recusa('{"ok": false}') == "O Porteiro recusou."
    assert mensagem_recusa("") == "O Porteiro recusou."


def test_token_de_usuario_nao_aprova_a_origem_pendente(tmp_path):
    from control_plane.auth import entrar_com_token
    from control_plane.user_token import emitir

    ip = "203.0.113.40"
    create_user(tmp_path, "ana", abrir=["n8n"])
    token = emitir(tmp_path, "ana", ver=[], operar=[], abrir=["n8n"], por="admin", ip="127.0.0.1")
    pasta = tmp_path / "n8n" / "storage" / "Porteiro"
    pasta.mkdir(parents=True)
    controle = pasta / "controle_acesso.json"
    controle.write_text(
        json.dumps(
            {
                "visitantes": [
                    {
                        "ip": ip,
                        "status": "aprovado",
                        "conta_vinculada": "ana",
                        "vinculo": "ativo",
                        "origens": [
                            {"origem": "antiga", "status": "aprovado"},
                            {"origem": "nova", "status": "pendente"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    sessao = entrar_com_token(tmp_path, token, ip=ip, modo="edge")
    assert sessao["aguardando"] is False
    assert sessao["username"] == "ana"
    gravado = json.loads(controle.read_text(encoding="utf-8"))
    origens = {item["origem"]: item["status"] for item in gravado["visitantes"][0]["origens"]}
    assert origens == {"antiga": "aprovado", "nova": "pendente"}


def test_conta_criada_nao_traz_o_jwt(tmp_path):
    create_user(tmp_path, "Gabriel", abrir=["n8n"])
    texto = (tmp_path / ".n8groker" / "users.json").read_text(encoding="utf-8")
    assert "must_change_password" not in texto
    assert "hash" not in texto
    assert "eyJ" not in texto
