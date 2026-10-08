"""JWT de usuário: emissão, prazo, rotação, revogação e claims."""

import json
import time
from pathlib import Path

import pytest

from control_plane.auth import (
    AuthError,
    create_user,
    entrar_com_token,
    sessao_do_token,
    update_lists,
)
from control_plane.user_token import conferir, emitir, garantir, key_path, revogar_jti, rotacionar
from scout.core.sessao_cookie import caminho_chave, caminho_geracao, emitir as emitir_cookie, garantir as garantir_sessao, verificar


def _conta(tmp_path, nome="ana"):
    create_user(tmp_path, nome, ver=["n8n"], operar=[], abrir=["n8n"])
    return emitir(
        tmp_path,
        nome,
        ver=["n8n"],
        operar=[],
        abrir=["n8n"],
        por="admin",
        ip="127.0.0.1",
    )


def _cookie(tmp_path, nome="ana", geracao=1):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    garantir_sessao(tmp_path)
    privada = caminho_chave(tmp_path).read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    cookie = emitir_cookie(
        privada,
        {"u": nome, "ip": "203.0.113.10", "g": geracao, "abrir": ["n8n"]},
    )
    return cookie, publica


def test_emissao_guarda_jti_e_nao_o_jwt(tmp_path):
    token = _conta(tmp_path)
    assert token.count(".") == 2
    claims = conferir(tmp_path, token, ip="127.0.0.1")
    assert claims["sub"] == "ana"
    assert claims["ver"] == ["n8n"]
    assert claims["operar"] == []
    assert claims["abrir"] == ["n8n"]
    bruto_jti = (tmp_path / ".n8groker" / "usuario-jti.json").read_text(encoding="utf-8")
    bruto_users = (tmp_path / ".n8groker" / "users.json").read_text(encoding="utf-8")
    assert token not in bruto_jti
    assert token not in bruto_users
    assert claims["jti"] in bruto_jti
    assert "hash" not in bruto_users
    trilha = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert '"passo": "auth.emitir"' in trilha
    assert '"resultado": "ok"' in trilha
    assert token not in trilha
    assert not (tmp_path / ".n8groker" / "trilha" / "trilha.jsonl").exists()
    assert (tmp_path / ".n8groker" / "usuario.key").stat().st_size == 32


def test_expirado_recusa_e_deixa_motivo(tmp_path, monkeypatch):
    monkeypatch.setenv("N8GROKER_TOKEN_USUARIO_HORAS", "1")
    token = _conta(tmp_path)
    agora = time.time()
    assert conferir(tmp_path, token, agora=agora, ip="203.0.113.10")["sub"] == "ana"
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, token, agora=agora + 3600 + 31, ip="203.0.113.10")
    assert exc.value.motivo == "prazo"
    assert exc.value.motivo != "expirado"
    texto = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert '"motivo": "prazo"' in texto
    assert '"passo": "auth.login"' in texto
    assert '"resultado": "recusado"' in texto
    assert "expirado" not in texto
    assert token not in texto
    assert not (tmp_path / ".n8groker" / "trilha" / "alertas.jsonl").exists()
    assert not (tmp_path / ".n8groker" / "trilha" / "trilha.jsonl").exists()


def test_rotacao_derruba_token_e_cookie(tmp_path):
    token = _conta(tmp_path)
    cookie, publica = _cookie(tmp_path)
    assert verificar(cookie, publica, caminho_geracao(tmp_path), "203.0.113.10") is not None
    rotacionar(tmp_path, por="admin", ip="127.0.0.1")
    with pytest.raises(AuthError) as exc:
        sessao_do_token(tmp_path, token, ip="203.0.113.10", modo="console")
    assert exc.value.motivo == "rotacionado"
    assert verificar(cookie, publica, caminho_geracao(tmp_path), "203.0.113.10") is None
    novo = emitir(tmp_path, "ana", ver=["n8n"], operar=["n8n"], abrir=["n8n"], por="admin", ip="127.0.0.1")
    sessao = entrar_com_token(tmp_path, novo, ip="203.0.113.10", modo="console")
    assert sessao["operar"] == ["n8n"]
    trilha = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert '"passo": "auth.rotacionar"' in trilha
    assert '"motivo": "rotacionado"' in trilha
    assert token not in trilha
    assert novo not in trilha


def test_revogacao_do_jti_recusa_e_sobe_a_geracao(tmp_path):
    token = _conta(tmp_path)
    jti = conferir(tmp_path, token)["jti"]
    cookie, publica = _cookie(tmp_path)
    assert verificar(cookie, publica, caminho_geracao(tmp_path), "203.0.113.10") is not None
    revogar_jti(tmp_path, jti, por="admin", ip="127.0.0.1")
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, token, ip="203.0.113.10")
    assert exc.value.motivo == "revogado"
    assert verificar(cookie, publica, caminho_geracao(tmp_path), "203.0.113.10") is None


def test_permissao_vem_do_claim_e_nao_da_lista_gravada(tmp_path):
    token = _conta(tmp_path)
    sessao = entrar_com_token(tmp_path, token, ip="127.0.0.1", modo="console")
    assert sessao["ver"] == ["n8n"]
    assert sessao["operar"] == []
    update_lists(tmp_path, "ana", ver=["n8n", "scout"], operar=["n8n"], abrir=["n8n"])
    de_novo = entrar_com_token(tmp_path, token, ip="127.0.0.1", modo="console")
    assert de_novo["operar"] == []
    assert de_novo["ver"] == ["n8n"]
    outro = emitir(
        tmp_path,
        "ana",
        ver=["n8n", "scout"],
        operar=["n8n"],
        abrir=["scout"],
        por="admin",
        ip="127.0.0.1",
    )
    assert entrar_com_token(tmp_path, outro, ip="127.0.0.1", modo="console")["operar"] == ["n8n"]
    assert entrar_com_token(tmp_path, token, ip="127.0.0.1", modo="console")["operar"] == []


def test_garantir_aceita_str_e_path_e_grava_o_pub(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    semente = bytes(range(32))
    pasta = tmp_path / ".n8groker"
    pasta.mkdir()
    chave = pasta / "usuario.key"
    chave.write_bytes(semente)
    assert key_path(str(tmp_path)) == key_path(tmp_path)

    garantir(str(tmp_path))
    esperada = Ed25519PrivateKey.from_private_bytes(semente).public_key().public_bytes_raw()
    pub = pasta / "usuario.pub"
    assert chave.read_bytes() == semente
    assert pub.read_bytes() == esperada
    assert len(esperada) == 32

    garantir(tmp_path)
    assert chave.read_bytes() == semente
    assert pub.read_bytes() == esperada

    outro = tmp_path / "so-path"
    garantir(outro)
    privada = (outro / ".n8groker" / "usuario.key").read_bytes()
    publica = (outro / ".n8groker" / "usuario.pub").read_bytes()
    assert len(privada) == 32 and len(publica) == 32
    assert publica == Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()

    vazio = tmp_path / "so-str"
    garantir(str(vazio))
    privada = (vazio / ".n8groker" / "usuario.key").read_bytes()
    publica = (vazio / ".n8groker" / "usuario.pub").read_bytes()
    assert len(privada) == 32 and len(publica) == 32
    assert publica == Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()


def test_chave_nao_reescreve_arquivo_existente_e_recusa_pasta(tmp_path):
    garantir(tmp_path)
    chave = tmp_path / ".n8groker" / "usuario.key"
    antes = chave.read_bytes()
    garantir(tmp_path)
    assert chave.read_bytes() == antes
    chave.unlink()
    chave.mkdir()
    with pytest.raises(AuthError):
        garantir(tmp_path)


def test_boot_prepara_a_chave_sem_montar_no_scout():
    raiz = Path(__file__).resolve().parents[1]
    texto = (raiz / "iniciar_servicos.ps1").read_text(encoding="utf-8")
    assert "function Ensure-ChaveUsuario" in texto
    assert "usuario.key" in texto
    boot = texto.split("\ntry {\n", 1)[1]
    assert boot.index("Ensure-ArquivosMontados") < boot.index("Ensure-ChaveUsuario")
    compose = (raiz / "Scout_OSINT_Docker" / "docker-compose.yml").read_text(encoding="utf-8")
    montes = [linha for linha in compose.splitlines() if ":" in linha and not linha.strip().startswith("#")]
    assert all("usuario.key" not in linha and "admin.key" not in linha for linha in montes)


def test_repositorio_nao_semeia_conta_de_teste():
    raiz = Path(__file__).resolve().parents[1]
    for relativo in (
        ".env.example",
        ".env_template",
        "n8n/docker-compose.yml",
        "llm/docker-compose.yml",
        "Scout_OSINT_Docker/docker-compose.yml",
    ):
        texto = (raiz / relativo).read_text(encoding="utf-8")
        assert '"username": "visita"' not in texto
        assert '"username": "guia"' not in texto
    gitignore = (raiz / ".gitignore").read_text(encoding="utf-8")
    assert ".n8groker/" in gitignore
    assert "controle_acesso.json" in gitignore
    local = raiz / ".n8groker" / "users.json"
    if local.is_file():
        texto = local.read_text(encoding="utf-8")
        assert '"username": "visita"' not in texto
        assert '"username": "guia"' not in texto


def test_hash_no_controle_de_acesso_e_ignorado(tmp_path):
    pasta = tmp_path / "n8n" / "storage" / "Porteiro"
    pasta.mkdir(parents=True)
    (pasta / "controle_acesso.json").write_text(
        json.dumps(
            {
                "senha": "nao-usar",
                "visitantes": [
                    {
                        "ip": "203.0.113.10",
                        "status": "aprovado",
                        "hash": "abc",
                        "salt": "def",
                        "conta_vinculada": "ana",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".n8groker").mkdir()
    (tmp_path / ".n8groker" / "users.json").write_text(
        json.dumps(
            {
                "users": [
                    {
                        "username": "ana",
                        "salt": "aa" * 16,
                        "hash": "bb" * 32,
                        "n": 16384,
                        "r": 8,
                        "p": 1,
                        "must_change_password": True,
                        "ver": ["n8n"],
                        "operar": [],
                        "abrir": ["n8n"],
                        "status": "ativo",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    from control_plane.auth import load_users

    usuarios = load_users(tmp_path)
    assert "hash" not in usuarios[0]
    assert "salt" not in usuarios[0]
    bruto = (tmp_path / ".n8groker" / "users.json").read_text(encoding="utf-8")
    assert "bb" * 32 not in bruto
    fila = json.loads((pasta / "controle_acesso.json").read_text(encoding="utf-8"))
    assert "senha" not in fila
    assert "hash" not in fila["visitantes"][0]
    assert fila["visitantes"][0]["conta_vinculada"] == "ana"
    token = emitir(tmp_path, "ana", ver=["n8n"], operar=[], abrir=["n8n"], por="admin", ip="127.0.0.1")
    sessao = entrar_com_token(tmp_path, token, ip="127.0.0.1", modo="console")
    assert sessao["ver"] == ["n8n"]
    assert sessao["must_change"] is False


def _jti(tmp_path):
    return tmp_path / ".n8groker" / "usuario-jti.json"


def test_jti_ilegivel_nao_reativa_token_revogado(tmp_path):
    from control_plane.admin_token import aceitar, init_keys, issue

    token = _conta(tmp_path)
    jti = conferir(tmp_path, token, ip="203.0.113.10")["jti"]
    revogar_jti(tmp_path, jti, por="admin", ip="127.0.0.1")
    path = _jti(tmp_path)
    path.write_text("{nao e json", encoding="utf-8")
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, token, ip="203.0.113.10")
    assert exc.value.motivo == "registro"
    assert exc.value.message == "Espere o dono"
    assert "usuario-jti.json" not in exc.value.message
    assert "revogado" not in exc.value.message.lower()
    assert exc.value.motivo != "assinatura"
    trilha = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert '"motivo": "registro"' in trilha
    assert '"resultado": "recusado"' in trilha
    assert '"passo": "auth.login"' in trilha
    from control_plane.trilha_auth import legiveis_auth
    from scout.core.trilha import Trilha

    frase = legiveis_auth(tmp_path)[-1]
    assert "registro" in frase
    assert "usuario-jti.json" in frase
    assert "auth.login" in frase
    assert "recusado" in frase
    assert Trilha(tmp_path / ".n8groker" / "trilha").legiveis(ip="203.0.113.10") == []
    assert "LIBERADO" not in frase
    assert path.read_text(encoding="utf-8") == "{nao e json"
    with pytest.raises(AuthError) as lista:
        from control_plane.user_token import listar

        listar(tmp_path, "ana")
    assert lista.value.motivo == "registro"
    init_keys(tmp_path)
    admin = issue(tmp_path)
    aceitar(tmp_path, admin, modo="console", ip="127.0.0.1")
    assert path.read_text(encoding="utf-8") == "{nao e json"


def test_jti_ausente_na_verificacao_tambem_recusa(tmp_path):
    token = _conta(tmp_path)
    _jti(tmp_path).unlink()
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, token, ip="203.0.113.10")
    assert exc.value.motivo == "registro"
    texto = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert texto.count('"motivo": "registro"') >= 1
    assert '"passo": "auth.login"' in texto


def test_jti_legivel_sem_a_linha_recusa_como_revogado(tmp_path):
    token = _conta(tmp_path)
    path = _jti(tmp_path)
    path.write_text('{"items":[]}', encoding="utf-8")
    with pytest.raises(AuthError) as vazio:
        conferir(tmp_path, token, ip="203.0.113.10")
    assert vazio.value.motivo == "revogado"
    assert vazio.value.message == "Peça outro token ao dono"
    assert path.read_text(encoding="utf-8") == '{"items":[]}'
    path.write_text(
        json.dumps({"items": [{"jti": "cd" * 16, "revogado": False, "conta": "ana"}]}),
        encoding="utf-8",
    )
    with pytest.raises(AuthError) as outra:
        conferir(tmp_path, token, ip="203.0.113.10")
    assert outra.value.motivo == "revogado"
    assert "usuario-jti.json" not in outra.value.message


def test_emitir_nao_apaga_jti_ilegivel(tmp_path):
    _conta(tmp_path)
    path = _jti(tmp_path)
    path.write_bytes(b"\xff\xfe quebrado")
    antes = path.read_bytes()
    with pytest.raises(AuthError) as exc:
        emitir(tmp_path, "ana", ver=["n8n"], operar=[], abrir=["n8n"], por="admin", ip="127.0.0.1")
    assert exc.value.motivo == "registro"
    assert path.read_bytes() == antes


def test_cola_com_espaco_aspas_e_quebra_nao_cai_em_assinatura(tmp_path):
    import ast

    from control_plane.admin_token import _normalizar
    from control_plane.auth import normalizar_token
    from control_plane.trilha_auth import proximo_passo

    token = _conta(tmp_path)
    colado = '\n “' + token[:20] + " \n" + token[20:] + '” \r\n'
    assert normalizar_token(colado) == token
    assert _normalizar(colado) == normalizar_token(colado)
    assert entrar_com_token(tmp_path, colado, ip="127.0.0.1", modo="console")["username"] == "ana"
    assert entrar_com_token(tmp_path, "  '" + token + "'  ", ip="203.0.113.10", modo="edge")["username"] == "ana"
    sujo = "\ufeff'" + token[:12] + "\u200b" + token[12:24] + "\u00a0" + token[24:]
    assert entrar_com_token(tmp_path, sujo, ip="203.0.113.10", modo="edge")["username"] == "ana"
    assert entrar_com_token(tmp_path, "\u201c" + token, ip="127.0.0.1", modo="console")["username"] == "ana"
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, "nao-e-jwt\ncom aspas", ip="203.0.113.10")
    assert exc.value.motivo == "formato"
    assert exc.value.message == proximo_passo("formato")
    assert exc.value.message == "Peça outro token ao dono"
    assert "Cole de novo" not in exc.value.message
    assert exc.value.motivo != "assinatura"
    trilha = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert '"motivo": "formato"' in trilha
    assert "token" not in trilha.lower()
    with pytest.raises(AuthError) as vazio:
        conferir(tmp_path, " \n ", ip="203.0.113.10")
    assert vazio.value.motivo == "vazio"
    arvore = ast.parse(
        (Path(__file__).resolve().parents[1] / "control_plane" / "app.py").read_text(encoding="utf-8")
    )
    login = next(no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name == "_require_login")
    areas = []
    senhas = []
    for no in ast.walk(login):
        if not isinstance(no, ast.Call) or not isinstance(no.func, ast.Attribute):
            continue
        if no.func.attr == "text_area" and no.args:
            arg = no.args[0]
            areas.append(arg.id if isinstance(arg, ast.Name) else arg.value)
        if no.func.attr == "text_input":
            for chave in no.keywords:
                if chave.arg == "type" and isinstance(chave.value, ast.Constant) and chave.value.value == "password":
                    senhas.append(no)
    assert senhas == []
    assert areas == ["rotulo_token", "Token de admin"]


def test_gravacao_atomica_preserva_o_arquivo_se_o_replace_falha(tmp_path, monkeypatch):
    from scout.core.gravar_arquivo import gravar_bytes, substituir_bytes

    alvo = tmp_path / "usuario-jti.json"
    gravar_bytes(alvo, b'{"items":[]}')
    inode = alvo.stat().st_ino
    gravar_bytes(alvo, b'{"items":[1]}')
    assert alvo.stat().st_ino == inode
    substituir_bytes(alvo, b'{"items":[2]}')
    assert alvo.read_bytes() == b'{"items":[2]}'
    assert alvo.stat().st_ino != inode

    def falha(origem, destino):
        raise OSError("disco cheio")

    monkeypatch.setattr("scout.core.gravar_arquivo.os.replace", falha)
    with pytest.raises(OSError):
        substituir_bytes(alvo, b'{"items":[3]}')
    assert alvo.read_bytes() == b'{"items":[2]}'
    assert [item.name for item in tmp_path.iterdir()] == ["usuario-jti.json"]


def test_kid_diferente_sem_bater_na_publica_anterior_e_assinatura(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from control_plane.user_token import _assinar, key_path

    token = _conta(tmp_path)
    claims = dict(conferir(tmp_path, token))
    claims["kid"] = "0" * 16
    falso = _assinar(key_path(tmp_path).read_bytes(), claims)
    with pytest.raises(AuthError) as atual:
        conferir(tmp_path, falso)
    assert atual.value.motivo == "assinatura"
    outra = Ed25519PrivateKey.generate().private_bytes_raw()
    estranho = _assinar(outra, claims)
    with pytest.raises(AuthError) as alheia:
        conferir(tmp_path, estranho)
    assert alheia.value.motivo == "assinatura"


def test_rotacao_interrompida_nao_deixa_o_par_inconsistente(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from control_plane.user_token import pub_path

    token = _conta(tmp_path)
    chave = key_path(tmp_path)
    publica = pub_path(tmp_path)
    antiga_priv = chave.read_bytes()
    antiga_pub = publica.read_bytes()
    assert conferir(tmp_path, token)["sub"] == "ana"

    nova = Ed25519PrivateKey.generate()
    nova_priv = nova.private_bytes_raw()
    nova_pub = nova.public_key().public_bytes_raw()
    chave.with_name("usuario.key.novo").write_bytes(nova_priv)
    garantir(tmp_path)
    assert chave.read_bytes() == antiga_priv
    assert publica.read_bytes() == antiga_pub
    assert not chave.with_name("usuario.key.novo").exists()
    assert conferir(tmp_path, token)["sub"] == "ana"

    chave.with_name("usuario.key.novo").write_bytes(nova_priv)
    publica.with_name("usuario.pub.novo").write_bytes(nova_pub)
    garantir(tmp_path)
    assert chave.read_bytes() == nova_priv
    assert publica.read_bytes() == nova_pub
    assert not chave.with_name("usuario.key.novo").exists()
    with pytest.raises(AuthError) as exc:
        conferir(tmp_path, token)
    assert exc.value.motivo == "rotacionado"

    outro = Ed25519PrivateKey.generate()
    outro_priv = outro.private_bytes_raw()
    outro_pub = outro.public_key().public_bytes_raw()
    chave.write_bytes(outro_priv)
    publica.with_name("usuario.pub.novo").write_bytes(outro_pub)
    garantir(tmp_path)
    assert chave.read_bytes() == outro_priv
    assert publica.read_bytes() == outro_pub


def test_rotacionar_nao_troca_o_par_se_o_segundo_temporario_falha(tmp_path, monkeypatch):
    import control_plane.user_token as modulo
    from control_plane.user_token import pub_path

    _conta(tmp_path)
    chave = key_path(tmp_path)
    publica = pub_path(tmp_path)
    antes_priv = chave.read_bytes()
    antes_pub = publica.read_bytes()
    original = modulo.substituir_bytes

    def falha_no_pub(path, data):
        if str(path).endswith("usuario.pub.novo"):
            raise OSError("disco cheio")
        return original(path, data)

    monkeypatch.setattr(modulo, "substituir_bytes", falha_no_pub)
    with pytest.raises(AuthError, match="par anterior"):
        rotacionar(tmp_path, por="admin", ip="127.0.0.1")
    assert chave.read_bytes() == antes_priv
    assert publica.read_bytes() == antes_pub
    assert not chave.with_name("usuario.key.novo").exists()
    assert not publica.with_name("usuario.pub.novo").exists()


def test_compose_nao_monta_chave_nem_jti_de_usuario():
    raiz = Path(__file__).resolve().parents[1]
    compose = (raiz / "Scout_OSINT_Docker" / "docker-compose.yml").read_text(encoding="utf-8")
    montes = [linha for linha in compose.splitlines() if ":" in linha and not linha.strip().startswith("#")]
    for nome in ("usuario.key", "usuario.pub", "usuario-jti.json", "admin.key"):
        assert all(nome not in linha for linha in montes)
