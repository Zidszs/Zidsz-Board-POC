import json
import time
from pathlib import Path

import pytest

from control_plane.admin_token import (
    AUD,
    ISS,
    _assinar,
    aceitar,
    conferir,
    init_keys,
    issue,
    key_path,
    main,
    pub_path,
    rotate_keys,
)
from control_plane.auth import AuthError


def test_emite_aceita_uma_vez_e_a_borda_recusa(tmp_path):
    init_keys(tmp_path)
    assert key_path(tmp_path).stat().st_mode & 0o077 == 0
    token = issue(tmp_path)
    assert token.count(".") == 2
    with pytest.raises(AuthError) as borda:
        aceitar(tmp_path, token, modo="edge", ip="203.0.113.9")
    assert borda.value.motivo == "tela"
    assert "borda" not in borda.value.message
    aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
    with pytest.raises(AuthError) as repetido:
        aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
    assert repetido.value.motivo == "repetido"
    auditoria = (tmp_path / ".n8groker" / "audit.jsonl").read_text(encoding="utf-8")
    assert "sessão admin aberta" in auditoria
    assert token not in auditoria
    assert "eyJ" not in auditoria


def test_aud_iss_e_vida_longa_falham(tmp_path):
    init_keys(tmp_path)
    privada = key_path(tmp_path).read_bytes()
    agora = int(time.time())
    base = {"iss": ISS, "aud": AUD, "sub": "admin", "iat": agora, "nbf": agora, "exp": agora + 300, "jti": "ab" * 16}
    with pytest.raises(AuthError):
        conferir(tmp_path, _assinar(privada, {**base, "aud": "outro"}))
    with pytest.raises(AuthError):
        conferir(tmp_path, _assinar(privada, {**base, "iss": "outro"}))
    with pytest.raises(AuthError):
        conferir(tmp_path, _assinar(privada, {**base, "exp": agora + 601}))


def test_rotacao_recusa_na_hora_e_sobe_admin_geracao(tmp_path):
    from control_plane.admin_token import ler_geracao

    init_keys(tmp_path)
    antigo = issue(tmp_path)
    conferir(tmp_path, antigo)
    assert ler_geracao(tmp_path) == 0
    rotate_keys(tmp_path)
    with pytest.raises(AuthError) as imediato:
        conferir(tmp_path, antigo)
    assert imediato.value.motivo == "rotacionado"
    assert ler_geracao(tmp_path) == 1
    novo = issue(tmp_path)
    conferir(tmp_path, novo)
    rotate_keys(tmp_path)
    assert ler_geracao(tmp_path) == 2
    with pytest.raises(AuthError) as segunda:
        conferir(tmp_path, novo)
    assert segunda.value.motivo == "rotacionado"
    assert main(["--root", str(tmp_path)]) == 0


def test_raiz_do_comando_e_a_do_painel(monkeypatch, tmp_path):
    from control_plane.admin_token import raiz_padrao
    from control_plane.config import default_root

    monkeypatch.chdir(tmp_path)
    assert raiz_padrao() == default_root()
    assert raiz_padrao() != tmp_path


def test_cola_com_espaco_e_prazo_vai_para_a_trilha(tmp_path):
    init_keys(tmp_path)
    token = issue(tmp_path)
    conferir(tmp_path, " \n" + token + "\r\n ")
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, token, agora=time.time() + 400, ip="127.0.0.1")
    assert exc.value.motivo == "prazo"
    assert "venceu" in exc.value.message
    auth = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert '"passo": "auth.login"' in auth
    assert '"resultado": "recusado"' in auth
    assert '"motivo": "prazo"' in auth
    assert token not in auth
    assert "eyJ" not in auth
    assert not (tmp_path / ".n8groker" / "trilha" / "trilha.jsonl").exists()
    assert not (tmp_path / ".n8groker" / "trilha" / "alertas.jsonl").exists()
    with pytest.raises(AuthError) as vazio:
        conferir(tmp_path, "  \n")
    assert vazio.value.motivo == "vazio"
    assert vazio.value.message == "Preencha o campo"


def test_admin_jti_ilegivel_recusa_sem_regravar_e_a_sessao_aberta_segue(tmp_path, monkeypatch):
    import control_plane.app as painel
    from control_plane.admin_token import reparar_jti

    init_keys(tmp_path)
    token = issue(tmp_path)
    aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
    path = tmp_path / ".n8groker" / "admin-jti.json"
    path.write_text("{quebrado", encoding="utf-8")
    antes = path.read_bytes()
    with pytest.raises(AuthError) as exc:
        aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
    assert exc.value.motivo == "registro"
    assert "admin-jti.json" in exc.value.message
    assert "--reparar-jti" in exc.value.message
    assert path.read_bytes() == antes
    assert main(["--init", "--root", str(tmp_path)]) != 0
    assert path.read_bytes() == antes

    class _Tela:
        def __init__(self):
            self.session_state = {
                "cp_authenticated": True,
                "cp_admin": True,
                "cp_must_change": False,
                "cp_ip": "127.0.0.1",
            }

    class _Settings:
        root = tmp_path

    tela = _Tela()
    monkeypatch.setattr(painel, "st", tela)
    assert painel.sessao_admin_aberta(tela.session_state) is True
    assert painel._require_login(_Settings()) is True
    assert path.read_bytes() == antes

    assert main(["--reparar-jti", "--root", str(tmp_path)]) == 0
    assert path.is_file()
    reparado = json.loads(path.read_text(encoding="utf-8"))
    assert reparado["items"] == []
    assert isinstance(reparado["recusar_emitidos_antes"], (int, float))
    with pytest.raises(AuthError) as velho:
        aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
    assert velho.value.motivo == "revogado"
    quebrados = list((tmp_path / ".n8groker").glob("admin-jti.json.quebrado*"))
    assert len(quebrados) == 1
    assert quebrados[0].read_text(encoding="utf-8") == "{quebrado"
    path.write_text('{"items":[{"jti":"' + "ab" * 16 + '","exp":9999999999}]}', encoding="utf-8")
    assert "legível" in reparar_jti(tmp_path)
    assert path.is_file()
    novo = issue(tmp_path)
    aceitar(tmp_path, novo, modo="console", ip="127.0.0.1")


def test_lista_admin_invalida_nao_regrava_e_o_reparo_corta_o_iat(tmp_path):
    from control_plane.admin_token import reparar_jti

    init_keys(tmp_path)
    token = issue(tmp_path)
    aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
    path = tmp_path / ".n8groker" / "admin-jti.json"
    for bruto in ('{"items":[1]}', '{"items":[{}]}'):
        path.write_text(bruto, encoding="utf-8")
        with pytest.raises(AuthError) as exc:
            aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
        assert exc.value.motivo == "registro"
        assert path.read_text(encoding="utf-8") == bruto
        assert "legível" not in reparar_jti(tmp_path)
        assert not path.read_text(encoding="utf-8") == bruto
        gravado = json.loads(path.read_text(encoding="utf-8"))
        assert "recusar_emitidos_antes" in gravado
        with pytest.raises(AuthError) as cortado:
            aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
        assert cortado.value.motivo == "revogado"
        assert "recusar_emitidos_antes" in path.read_text(encoding="utf-8")
    path.write_text('{"items":[]}', encoding="utf-8")
    assert "legível" in reparar_jti(tmp_path)
    assert path.read_text(encoding="utf-8") == '{"items":[]}'
    outro = issue(tmp_path)
    aceitar(tmp_path, outro, modo="console", ip="127.0.0.1")
    # Lista vazia válida é «nenhum usado»: o jti antigo, sem registro, entra de novo.
    path.write_text('{"items":[]}', encoding="utf-8")
    aceitar(tmp_path, outro, modo="console", ip="127.0.0.1")


def test_campo_token_esvazia_depois_do_envio_e_a_entrada_nao_anuncia_folga():
    import ast

    from control_plane.app import marcar_campo, preparar_campo

    estado = {"auth_token": "eyJaaa.bbb.ccc", "auth_admin_token": "eyJddd.eee.fff"}
    marcar_campo(estado, "auth_token")
    marcar_campo(estado, "auth_admin_token")
    preparar_campo(estado, "auth_token")
    preparar_campo(estado, "auth_admin_token")
    assert estado["auth_token"] == ""
    assert estado["auth_admin_token"] == ""
    arvore = ast.parse(
        (Path(__file__).resolve().parents[1] / "control_plane" / "app.py").read_text(encoding="utf-8")
    )
    login = next(no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name == "_require_login")
    nomes = [
        no.func.id
        for no in ast.walk(login)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
    ]
    assert nomes.count("marcar_campo") >= 2
    assert "aviso_folga" not in nomes
    assert any(
        isinstance(no, ast.keyword)
        and no.arg == "clear_on_submit"
        and isinstance(no.value, ast.Constant)
        and no.value.value is True
        for no in ast.walk(login)
    )


def test_cli_rotate_chama_o_keeper_e_o_token_anterior_nao_entra(tmp_path, capsys):
    from control_plane.admin_token import MENSAGEM_ROTATE, ler_geracao

    init_keys(tmp_path)
    antigo = issue(tmp_path)
    antes = key_path(tmp_path).read_bytes()
    assert main(["--rotate", "--root", str(tmp_path)]) == 0
    saida = capsys.readouterr().out
    assert MENSAGEM_ROTATE in saida
    assert "5 minutos" not in saida
    assert key_path(tmp_path).read_bytes() != antes
    assert ler_geracao(tmp_path) == 1
    with pytest.raises(AuthError) as exc:
        aceitar(tmp_path, antigo, modo="console", ip="127.0.0.1")
    assert exc.value.motivo == "rotacionado"
    trilha = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert '"passo": "auth.rotacionar"' in trilha
    assert '"motivo": "rotacionado"' in trilha
    assert antigo not in trilha
    novo = issue(tmp_path)
    aceitar(tmp_path, novo, modo="console", ip="127.0.0.1")


def test_pub_ausente_ou_errada_e_regravada_na_emissao(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    init_keys(tmp_path)
    pub = pub_path(tmp_path)
    pub.unlink()
    token = issue(tmp_path)
    derivada = Ed25519PrivateKey.from_private_bytes(key_path(tmp_path).read_bytes()).public_key().public_bytes_raw()
    assert pub.read_bytes() == derivada
    conferir(tmp_path, token)
    pub.write_bytes(b"\x11" * 32)
    outro = issue(tmp_path)
    assert pub.read_bytes() == derivada
    conferir(tmp_path, outro)


def test_admin_colado_com_quebra_abre_a_sessao_no_console(tmp_path):
    from control_plane.auth import entrar_com_token

    init_keys(tmp_path)
    token = issue(tmp_path)
    colado = token[:40] + "\r\n" + token[40:]
    claims = aceitar(tmp_path, colado, modo="console", ip="127.0.0.1")
    assert claims["sub"] == "admin"
    auth = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert '"passo": "auth.login"' in auth
    assert '"resultado": "ok"' in auth
    fresco = issue(tmp_path)
    with pytest.raises(AuthError) as borda:
        entrar_com_token(tmp_path, "\n" + fresco + "\n", ip="203.0.113.9", modo="edge")
    assert borda.value.motivo == "tela"
    assert borda.value.message == "Esta tela não aceita este acesso"
    # A borda não consome o jti: o mesmo token ainda abre o console.
    aceitar(tmp_path, " " + fresco[:20] + "\n" + fresco[20:] + " ", modo="console", ip="127.0.0.1")


def test_relogio_adiantado_nao_e_prazo(tmp_path):
    init_keys(tmp_path)
    token = issue(tmp_path)
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, token, agora=time.time() - 120, ip="127.0.0.1")
    assert exc.value.motivo == "relogio"
    assert "relógio" in exc.value.message


def test_init_nao_grava_token_em_arquivo(tmp_path, capsys):
    assert main(["--init", "--root", str(tmp_path)]) == 0
    codigo = main(["--root", str(tmp_path)])
    assert codigo == 0
    saida = capsys.readouterr().out
    assert saida.count(".") >= 2
    nomes = {item.name for item in (tmp_path / ".n8groker").iterdir()}
    assert "admin.key" in nomes
    assert "admin.pub" in nomes
    assert not any(nome.endswith(".jwt") or nome == "token" for nome in nomes)
    texto = json.dumps(sorted(nomes))
    assert saida.strip() not in (tmp_path / ".n8groker" / "admin.key").read_text(encoding="latin1", errors="replace")
    assert "token" not in texto


def test_init_sem_chave_imprime_jwt_valido(tmp_path, capsys):
    assert main(["--init", "--root", str(tmp_path)]) == 0
    saida = capsys.readouterr().out.strip()
    assert saida.count(".") == 2
    assert len(key_path(tmp_path).read_bytes()) == 32
    conferir(tmp_path, saida)


def test_chave_invalida_explica_no_stdout(tmp_path, capsys):
    pasta = tmp_path / ".n8groker"
    pasta.mkdir(parents=True)
    (pasta / "admin.key").write_bytes(b"curta")
    (pasta / "admin.pub").write_bytes(b"\x00" * 32)
    assert main(["--root", str(tmp_path)]) == 2
    saida = capsys.readouterr().out
    assert saida.strip()
    assert "Apague .n8groker/admin.key" in saida
    assert "admin.pub" in saida
    assert "--init" in saida
    assert (pasta / "admin.key").read_bytes() == b"curta"


def test_init_com_chave_invalida_nao_cala_o_stdout(tmp_path, capsys):
    pasta = tmp_path / ".n8groker"
    pasta.mkdir(parents=True)
    (pasta / "admin.key").write_bytes(b"x" * 31)
    assert main(["--init", "--root", str(tmp_path)]) != 0
    saida = capsys.readouterr().out
    assert "Apague .n8groker/admin.key" in saida
    assert (pasta / "admin.key").read_bytes() == b"x" * 31


def test_meta_de_folga_nao_reabre_o_token_anterior(tmp_path):
    init_keys(tmp_path)
    antigo = issue(tmp_path)
    rotate_keys(tmp_path)
    meta = tmp_path / ".n8groker" / "admin-prev.json"
    meta.write_text(json.dumps({"until": time.time() + 9999}), encoding="utf-8")
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, antigo)
    assert exc.value.motivo == "rotacionado"


def test_sessao_admin_cai_quando_a_geracao_sobe_e_o_convidado_fica(tmp_path, monkeypatch):
    import control_plane.app as painel
    from control_plane.admin_token import geracao_confere, numero_para_sessao

    init_keys(tmp_path)
    token = issue(tmp_path)
    aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
    estado = {
        "cp_authenticated": True,
        "cp_admin": True,
        "cp_user": "admin",
        "cp_admin_geracao": numero_para_sessao(tmp_path),
        "cp_ip": "127.0.0.1",
    }
    assert geracao_confere(estado, tmp_path) is True
    assert painel.encerrar_admin_se_girou(dict(estado), tmp_path) is False

    class _Settings:
        root = tmp_path

    class _Tela:
        def __init__(self, session_state):
            self.session_state = session_state
            self.erros = []

        def subheader(self, *_args, **_kwargs):
            return None

        def caption(self, *_args, **_kwargs):
            return None

        def text_area(self, *_args, **_kwargs):
            return ""

        def button(self, *_args, **_kwargs):
            return False

        def divider(self, *_args, **_kwargs):
            return None

        def error(self, msg, *_args, **_kwargs):
            self.erros.append(str(msg))

        def warning(self, msg, *_args, **_kwargs):
            self.erros.append(str(msg))

        def form(self, *_args, **_kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def form_submit_button(self, *_args, **_kwargs):
            return False

    tela_aberta = _Tela(dict(estado))
    monkeypatch.setattr(painel, "st", tela_aberta)
    assert painel._require_login(_Settings()) is True

    rotate_keys(tmp_path)
    usuario = {
        "cp_authenticated": True,
        "cp_admin": False,
        "cp_user": "ana",
        "cp_admin_geracao": 0,
    }
    assert painel.encerrar_admin_se_girou(usuario, tmp_path) is False
    assert usuario["cp_authenticated"] is True

    tela = _Tela(
        {
            "cp_authenticated": True,
            "cp_admin": True,
            "cp_user": "admin",
            "cp_admin_geracao": 0,
            "cp_ip": "127.0.0.1",
        }
    )
    monkeypatch.setattr(painel, "st", tela)
    assert painel._require_login(_Settings()) is False
    assert tela.session_state.get("cp_admin") is not True
    assert any("Cole um token novo" in item for item in tela.erros)
    fresco = issue(tmp_path)
    aceitar(tmp_path, fresco, modo="console", ip="127.0.0.1")


def test_geracao_ilegivel_nao_fecha_e_ausente_nao_tranca(tmp_path):
    from control_plane.admin_token import geracao_confere

    pasta = tmp_path / ".n8groker"
    pasta.mkdir()
    estado = {}
    assert geracao_confere(estado, tmp_path) is True
    assert estado["cp_admin_geracao"] == 0
    (pasta / "admin-geracao").write_text("nao-e-numero", encoding="utf-8")
    assert geracao_confere({"cp_admin_geracao": 0}, tmp_path) is True


def _stdin_json(monkeypatch, texto: bytes) -> None:
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(texto), encoding="utf-8"))


def test_giro_com_arquivo_apagado_derruba_sessao_em_1(tmp_path):
    from control_plane.admin_token import geracao_confere, ler_geracao, numero_para_sessao

    init_keys(tmp_path)
    pasta = tmp_path / ".n8groker"
    (pasta / "admin-geracao").write_text("1\n", encoding="ascii")
    assert numero_para_sessao(tmp_path) == 1
    (pasta / "admin-geracao").unlink()
    assert numero_para_sessao(tmp_path) == 0
    token = issue(tmp_path)
    conferir(tmp_path, token)
    rotate_keys(tmp_path)
    assert geracao_confere({"cp_admin_geracao": 1}, tmp_path) is False
    disco = ler_geracao(tmp_path)
    assert isinstance(disco, int) and 1 < disco <= 1_000_000_000
    fresco = issue(tmp_path)
    conferir(tmp_path, fresco)
    assert numero_para_sessao(tmp_path) == disco
    assert geracao_confere({"cp_admin_geracao": disco}, tmp_path) is True


def test_giro_com_nao_e_numero_derruba_sessao_em_1(tmp_path):
    from control_plane.admin_token import geracao_confere, ler_geracao, numero_para_sessao

    init_keys(tmp_path)
    pasta = tmp_path / ".n8groker"
    (pasta / "admin-geracao").write_text("1\n", encoding="ascii")
    assert numero_para_sessao(tmp_path) == 1
    (pasta / "admin-geracao").write_text("nao-e-numero", encoding="ascii")
    assert numero_para_sessao(tmp_path) == 0
    token = issue(tmp_path)
    conferir(tmp_path, token)
    rotate_keys(tmp_path)
    assert geracao_confere({"cp_admin_geracao": 1}, tmp_path) is False
    disco = ler_geracao(tmp_path)
    assert isinstance(disco, int) and 1 < disco <= 1_000_000_000
    conferir(tmp_path, issue(tmp_path))


def test_giro_com_nao_e_numero_derruba_sessao_em_4(tmp_path):
    from control_plane.admin_token import geracao_confere, ler_geracao, numero_para_sessao

    init_keys(tmp_path)
    pasta = tmp_path / ".n8groker"
    (pasta / "admin-geracao").write_text("4\n", encoding="ascii")
    assert numero_para_sessao(tmp_path) == 4
    (pasta / "admin-geracao").write_text("nao-e-numero", encoding="ascii")
    rotate_keys(tmp_path)
    assert geracao_confere({"cp_admin_geracao": 4}, tmp_path) is False
    disco = ler_geracao(tmp_path)
    assert isinstance(disco, int) and disco > 4
    conferir(tmp_path, issue(tmp_path))


def test_falha_no_contador_nao_troca_a_chave_nem_mente(tmp_path, monkeypatch, capsys):
    from control_plane.admin_token import _gravar_raw
    from control_plane.keeper import main as keeper_main

    init_keys(tmp_path)
    pub_antes = pub_path(tmp_path).read_bytes()
    chave_antes = key_path(tmp_path).read_bytes()

    def vigia(path, data):
        if Path(path).name == "admin-geracao":
            raise OSError("disco")
        _gravar_raw(path, data)

    monkeypatch.setattr("control_plane.admin_token._gravar_raw", vigia)
    _stdin_json(monkeypatch, b'{"alvo":"admin"}')
    codigo = keeper_main(["rotacionar", "--root", str(tmp_path)])
    saida = capsys.readouterr().out
    data = json.loads(saida.strip().splitlines()[-1])
    if pub_path(tmp_path).read_bytes() != pub_antes:
        assert data["ok"] is True
        assert codigo == 0
        assert "Nada foi emitido nem girado" not in saida
    else:
        assert data["ok"] is False
        assert data["motivo"] == "chave"
        assert codigo == 2
        assert key_path(tmp_path).read_bytes() == chave_antes


def test_erro_depois_da_chave_nova_nao_diz_que_nada_girou(tmp_path, monkeypatch, capsys):
    from control_plane.admin_token import geracao_confere
    from control_plane.keeper import main as keeper_main

    init_keys(tmp_path)
    pub_antes = pub_path(tmp_path).read_bytes()

    def boom(*_args, **_kwargs):
        raise RuntimeError("trilha")

    monkeypatch.setattr("control_plane.admin_token.anotar_auth", boom)
    _stdin_json(monkeypatch, b'{"alvo":"admin"}')
    codigo = keeper_main(["rotacionar", "--root", str(tmp_path)])
    saida = capsys.readouterr().out
    data = json.loads(saida.strip().splitlines()[-1])
    assert pub_path(tmp_path).read_bytes() != pub_antes
    assert data["ok"] is True
    assert codigo == 0
    assert "Nada foi emitido nem girado" not in saida
    assert geracao_confere({"cp_admin_geracao": 0}, tmp_path) is False
    conferir(tmp_path, issue(tmp_path))
