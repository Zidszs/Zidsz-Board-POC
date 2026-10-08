"""Trilha legível, rotação e um teste por regra de alerta. Alerta não bloqueia."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.politica_portal import montar_politica  # noqa: E402
from scout.core.porta_apps import antes_do_porteiro  # noqa: E402
from scout.core.sessao_cookie import emitir, garantir, iniciar_usuario  # noqa: E402
from scout.core.sessao_cookie import caminho_chave, caminho_geracao  # noqa: E402
from scout.core.trilha import Trilha, pasta_de, texto_duracao  # noqa: E402


def _relogio(inicio=1_700_000_000):
    marca = {"t": inicio}
    return marca, lambda: marca["t"]


def _grade(tmp_path, marca=None):
    agora = None if marca is None else (lambda: marca["t"])
    return Trilha(tmp_path / "trilha", agora=agora)


def test_trilha_legivel_bloqueio_e_liberado(tmp_path):
    grade = _grade(tmp_path)
    ip = "203.0.113.8"
    grade.anotar(ip=ip, passo="/n8n direto", resultado="BLOQUEIO", motivo="rota sem painel")
    grade.anotar(ip=ip, sid="aa" * 8, conta="ana", origem="orig-1", passo="/painel", resultado="ok")
    grade.anotar(ip=ip, sid="aa" * 8, conta="ana", origem="orig-1", passo="login ok", resultado="ok")
    grade.anotar(ip=ip, sid="aa" * 8, conta="ana", origem="orig-1", app="n8n", passo="Porteiro ok", resultado="ok")
    grade.anotar(ip=ip, sid="aa" * 8, conta="ana", origem="orig-1", app="n8n", passo="n8n", resultado="LIBERADO")
    linhas = grade.legiveis(ip=ip)
    assert linhas[0] == "203.0.113.8 > /n8n direto > BLOQUEIO (rota sem painel)"
    assert linhas[1] == "203.0.113.8 > /painel > login ok > Porteiro ok > n8n > LIBERADO"
    assert grade.legiveis(sid="aa" * 8) == [linhas[1]]


def test_trilha_nao_grava_senha_token_nem_cookie(tmp_path):
    grade = _grade(tmp_path)
    grade.anotar(
        ip="203.0.113.8",
        sid="bb" * 8,
        conta="ana",
        passo="login ok",
        resultado="ok",
        motivo="senha=segredo token=eyJhbGci cookie=n8groker_sessao=abc",
    )
    texto = (tmp_path / "trilha" / "trilha.jsonl").read_text(encoding="utf-8")
    assert "segredo" not in texto
    assert "eyJ" not in texto
    assert "n8groker_sessao" not in texto
    assert "cookie" not in texto.lower()


def test_rotacao_e_retencao(tmp_path, monkeypatch):
    monkeypatch.setenv("N8GROKER_TRILHA_MAX_BYTES", "120")
    monkeypatch.setenv("N8GROKER_TRILHA_COPIAS", "1")
    monkeypatch.setenv("N8GROKER_TRILHA_DIAS", "7")
    marca, _agora = _relogio()
    grade = Trilha(tmp_path / "trilha", agora=lambda: marca["t"])
    for indice in range(6):
        grade.anotar(ip="203.0.113.8", passo=f"passo-{indice}-comprido", resultado="ok", motivo="checkpoint")
    assert (tmp_path / "trilha" / "trilha.jsonl.1").is_file()
    assert not (tmp_path / "trilha" / "trilha.jsonl.2").exists()
    marca["t"] += 8 * 86400
    grade.anotar(ip="203.0.113.8", passo="novo", resultado="LIBERADO", app="n8n", sid="cc" * 8, conta="ana")
    texto = (tmp_path / "trilha" / "trilha.jsonl").read_text(encoding="utf-8")
    assert "passo-0-comprido" not in texto
    assert any("novo" in linha for linha in grade.legiveis())


def test_regra_pulou_etapas_nao_bloqueia(tmp_path):
    grade = _grade(tmp_path)
    linha = grade.anotar(ip="203.0.113.8", sid="dd" * 8, conta="ana", app="n8n", passo="n8n", resultado="LIBERADO")
    assert linha["resultado"] == "LIBERADO"
    assert grade.alertas()[-1]["regra"] == "pulou_etapas"
    assert grade.alertas()[-1]["visto"] is False


def test_regra_trocas_de_app(tmp_path, monkeypatch):
    monkeypatch.setenv("N8GROKER_ALERTA_TROCAS", "3")
    monkeypatch.setenv("N8GROKER_ALERTA_TROCAS_SEG", "60")
    grade = _grade(tmp_path)
    sid = "ee" * 8
    for app in ("n8n", "langfuse", "litellm", "outro"):
        grade.anotar(
            ip="203.0.113.8",
            sid=sid,
            conta="ana",
            app=app,
            passo="/painel",
            resultado="ok",
        )
        grade.anotar(
            ip="203.0.113.8",
            sid=sid,
            conta="ana",
            app=app,
            passo="Porteiro ok",
            resultado="ok",
        )
        grade.anotar(ip="203.0.113.8", sid=sid, conta="ana", app=app, passo=app, resultado="LIBERADO")
    regras = [item["regra"] for item in grade.alertas()]
    assert "trocas_app" in regras
    assert grade.legiveis(sid=sid)[-1].endswith("LIBERADO")


def test_regra_horario_fora_da_conta(tmp_path):
    marca = {"t": 1_700_000_000}
    while True:
        from datetime import datetime, timezone

        if datetime.fromtimestamp(marca["t"], timezone.utc).hour == 3:
            break
        marca["t"] += 3600
    (tmp_path / "trilha").mkdir()
    (tmp_path / "trilha" / "horarios.json").write_text(
        json.dumps({"ana": {"inicio": 8, "fim": 18}}),
        encoding="utf-8",
    )
    grade = Trilha(tmp_path / "trilha", agora=lambda: marca["t"])
    grade.anotar(ip="203.0.113.8", sid="ff" * 8, conta="ana", passo="login ok", resultado="ok")
    assert grade.alertas()[-1]["regra"] == "horario"


def test_regra_dois_ips_nao_derruba_a_sessao(tmp_path):
    marca, _agora = _relogio()
    grade = Trilha(tmp_path / "trilha", agora=lambda: marca["t"])
    grade.ver_sessao(sid="11" * 8, conta="ana", ip="203.0.113.8", origem="orig-1", app="n8n")
    grade.ver_sessao(sid="22" * 8, conta="ana", ip="203.0.113.9", origem="orig-2", app="n8n")
    assert [item["regra"] for item in grade.alertas()] == ["dois_ips"]
    assert len(grade.sessoes()) == 2
    grade.ver_sessao(sid="33" * 8, conta="ana", ip="203.0.113.8", origem="orig-3", app="litellm")
    assert "dois_ips" == grade.alertas()[-1]["regra"]
    assert len(grade.sessoes()) == 3


def test_regra_bloqueios_antes_do_liberado(tmp_path, monkeypatch):
    monkeypatch.setenv("N8GROKER_ALERTA_BLOQUEIOS", "3")
    grade = _grade(tmp_path)
    ip = "203.0.113.8"
    for indice in range(3):
        grade.anotar(ip=ip, passo=f"/rota{indice} direto", resultado="BLOQUEIO", motivo="rota sem painel")
    liberado = grade.anotar(
        ip=ip,
        sid="44" * 8,
        conta="ana",
        app="n8n",
        passo="/painel",
        resultado="ok",
    )
    grade.anotar(ip=ip, sid="44" * 8, conta="ana", app="n8n", passo="Porteiro ok", resultado="ok")
    liberado = grade.anotar(ip=ip, sid="44" * 8, conta="ana", app="n8n", passo="n8n", resultado="LIBERADO")
    assert liberado["resultado"] == "LIBERADO"
    assert "bloqueios_antes" in [item["regra"] for item in grade.alertas()]


def test_visto_auditoria_e_webhook_local(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("N8GROKER_AUDIT_ARQUIVO", str(audit))
    chamadas = []
    grade = Trilha(tmp_path / "trilha", transport=chamadas.append)
    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "https://exemplo.invalid/webhook/alerta-trilha")
    grade.anotar(ip="203.0.113.8", sid="55" * 8, conta="ana", app="n8n", passo="n8n", resultado="LIBERADO")
    assert chamadas == []
    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "http://127.0.0.1:5678/webhook/alerta-trilha")
    marca = {"t": 1_700_000_500}
    grade2 = Trilha(tmp_path / "trilha", agora=lambda: marca["t"], transport=chamadas.append)
    grade2.anotar(ip="203.0.113.9", sid="66" * 8, conta="bia", app="n8n", passo="n8n", resultado="LIBERADO")
    assert chamadas and chamadas[-1].startswith("http://127.0.0.1:5678/webhook/alerta-trilha?")
    assert "regra=pulou_etapas" in chamadas[-1]
    assert "eyJ" not in chamadas[-1]
    texto = audit.read_text(encoding="utf-8")
    assert "alerta_trilha" in texto
    alerta = grade2.alertas()[-1]
    grade2.marcar_visto(alerta["id"])
    assert grade2.alertas()[-1]["visto"] is True


def test_revogar_sessao_e_duracao(tmp_path):
    marca, _agora = _relogio()
    grade = Trilha(tmp_path / "trilha", agora=lambda: marca["t"])
    grade.ver_sessao(sid="77" * 8, conta="ana", ip="203.0.113.8", origem="orig-1", app="n8n")
    marca["t"] += 125
    item = grade.sessoes()[0]
    assert item["conta"] == "ana"
    assert item["origem"] == "orig-1"
    assert item["app"] == "n8n"
    assert texto_duracao(item["inicio"], marca["t"]) == "2 min"
    grade.revogar(item["sid"])
    assert grade.sessoes() == []
    assert grade.revogada(item["sid"]) is True
    aliases = {"203.0.113.8": "casa"}
    assert aliases == {"203.0.113.8": "casa"}


def test_portal_grava_bloqueio_e_liberado_sem_mudar_o_veredito(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from scout.core.origem import cookies_de_prova, gerar_chave, impressao

    garantir(tmp_path)
    iniciar_usuario(caminho_geracao(tmp_path), "ana")
    privada = caminho_chave(tmp_path).read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    ec_priv, spki = gerar_chave()
    token = emitir(
        privada,
        {
            "u": "ana",
            "ip": "203.0.113.10",
            "g": 1,
            "app": "n8n",
            "abrir": ["n8n"],
            "origem": "orig-1",
            "dispositivo": impressao(spki),
            "nonce": "nonce-1",
            "sid": "88" * 8,
        },
    )
    prova = cookies_de_prova(ec_priv, spki, "orig-1", "nonce-1", "2026-10-01T12:00:00Z")

    class _Fila:
        def consultar(self, ip, origem, conta, app):
            return {
                "status": "aprovado",
                "ip": ip,
                "conta_vinculada": "ana",
                "origem": "orig-1",
                "vinculo": "ativo",
            }

    grade = Trilha(tmp_path / ".n8groker" / "trilha")
    fila = _Fila()
    antes = montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
        trilha=grade,
    )
    seco = antes(
        {"caminho": "/n8n", "headers": {"x-forwarded-for": "203.0.113.10"}, "query": ""},
        "127.0.0.1",
        {},
    )
    assert seco["liga"] is False
    assert seco["status"] == 403
    pedido = {
        "caminho": "/",
        "headers": {
            "x-forwarded-for": "203.0.113.10",
            "cookie": f"n8groker_sessao={token}; {prova}",
        },
        "query": "",
    }
    liberado = antes(pedido, "127.0.0.1", {})
    assert liberado["liga"] is True
    frases = grade.legiveis(ip="203.0.113.10")
    assert any("BLOQUEIO (rota sem painel)" in frase for frase in frases)
    assert any(frase.endswith("LIBERADO") for frase in frases)
    assert pasta_de(tmp_path) == tmp_path / ".n8groker" / "trilha"


def test_auth_jsonl_nao_entra_na_janela_de_20(tmp_path, monkeypatch):
    monkeypatch.setenv("N8GROKER_ALERTA_BLOQUEIOS", "3")
    pasta = tmp_path / "trilha"
    grade = Trilha(pasta)
    ip = "203.0.113.8"
    for indice in range(3):
        grade.anotar(ip=ip, passo=f"/rota{indice} direto", resultado="BLOQUEIO", motivo="rota sem painel")
    agora = int(time.time())
    linhas = []
    for indice in range(20):
        linhas.append(
            json.dumps(
                {
                    "quando": agora + 10 + indice,
                    "hora": "2026-01-01T00:00:00Z",
                    "ip": ip,
                    "conta": "ana",
                    "passo": "auth.login",
                    "resultado": "recusado",
                    "motivo": "formato",
                }
            )
        )
    (pasta / "auth.jsonl").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    recarregado = Trilha(pasta)
    assert recarregado._resultados[ip] == ["BLOQUEIO", "BLOQUEIO", "BLOQUEIO"]
    assert "recusado" not in recarregado._resultados[ip]
    eventos = recarregado._ler_eventos()
    assert eventos
    assert all(item.get("passo") != "auth.login" for item in eventos)
    recarregado.anotar(ip=ip, sid="44" * 8, conta="ana", app="n8n", passo="n8n", resultado="LIBERADO")
    assert "bloqueios_antes" in [item["regra"] for item in recarregado.alertas()]
    assert all("auth.login" not in frase for frase in recarregado.legiveis(ip=ip))


def _jwt_longo(meio: str, fim: str) -> str:
    return "eyJhbGciOiJFZERTQSJ9." + meio + "." + fim


def test_passo_auth_nao_entra_na_janela_nem_no_webhook(tmp_path, monkeypatch):
    """Passo auth.* na trilha de caminho mudaria a janela de 20 e o webhook."""
    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "http://127.0.0.1:5678/webhook/alerta-trilha")
    chamadas = []
    grade = Trilha(tmp_path / "trilha", transport=chamadas.append)
    jwt = _jwt_longo("E" * 24, "F" * 24)
    saida = grade.anotar(
        ip="203.0.113.8",
        conta=jwt,
        passo="auth.login",
        resultado="LIBERADO",
        motivo="ok",
        sid="99" * 8,
        app="n8n",
    )
    assert saida is None
    assert not (tmp_path / "trilha" / "trilha.jsonl").exists()
    assert not (tmp_path / "trilha" / "alertas.jsonl").exists()
    assert chamadas == []
    assert grade._resultados.get("203.0.113.8", []) == []
    recarregado = Trilha(tmp_path / "trilha")
    assert recarregado._resultados.get("203.0.113.8", []) == []
    assert recarregado.legiveis() == []
    assert jwt[8:-8] not in "".join(chamadas)


def test_motivos_de_sessao_vao_para_auth_sem_webhook(tmp_path, monkeypatch):
    from control_plane.trilha_auth import legiveis_auth

    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "http://127.0.0.1:5678/webhook/alerta-trilha")
    chamadas = []
    pasta = tmp_path / ".n8groker" / "trilha"
    grade = Trilha(pasta, transport=chamadas.append)
    grade.gravar_auth_sessao(ip="203.0.113.10", conta="ana", motivo_caminho="origem nao confere")
    grade.gravar_auth_sessao(
        ip="203.0.113.10", conta="ana", motivo_caminho="sem sessao", sid="ab" * 8
    )
    grade.gravar_auth_sessao(
        ip="203.0.113.10",
        conta="ana",
        motivo_caminho="sessao da conta revogada",
    )
    grade.gravar_auth_sessao(
        ip="203.0.113.10", conta="ana", motivo_caminho="sessao revogada", sid="cd" * 8
    )
    grade.gravar_auth_sessao(ip="203.0.113.10", conta="ana", motivo_caminho="rota sem painel")
    texto = (pasta / "auth.jsonl").read_text(encoding="utf-8")
    assert texto.count('"passo": "auth.sessao"') == 4
    assert '"motivo": "origem"' in texto
    assert '"motivo": "sem_sessao"' in texto
    assert '"motivo": "sessao"' not in texto
    assert texto.count('"motivo": "revogado"') == 2
    assert "ab" * 8 in texto
    assert "rota sem painel" not in texto
    assert "LIBERADO" not in texto
    assert "BLOQUEIO" not in texto
    assert not (pasta / "trilha.jsonl").exists()
    assert not (pasta / "alertas.jsonl").exists()
    assert chamadas == []
    linhas = legiveis_auth(tmp_path)
    assert len(linhas) == 4
    assert all("auth.sessao" in linha for linha in linhas)
    assert any("sem_sessao" in linha and "ab" * 8 in linha for linha in linhas)
    assert grade._resultados.get("203.0.113.10", []) == []


def test_auth_e_alerta_guardam_no_maximo_4_mais_4_do_jwt(tmp_path, monkeypatch):
    from control_plane.trilha_auth import anotar_auth, ler_auth

    jwt_auth = _jwt_longo("A" * 24, "B" * 24)
    anotar_auth(
        tmp_path,
        ip="127.0.0.1",
        conta=jwt_auth,
        passo="auth.login",
        resultado="recusado",
        motivo="formato",
    )
    item = ler_auth(tmp_path)[-1]
    assert item["passo"] == "auth.login"
    assert item["resultado"] == "recusado"
    assert item["motivo"] == "formato"
    assert item["conta"] == jwt_auth[:4] + "\u2026" + jwt_auth[-4:]
    auth_txt = (tmp_path / ".n8groker" / "trilha" / "auth.jsonl").read_text(encoding="utf-8")
    assert jwt_auth[8:-8] not in auth_txt
    assert jwt_auth not in auth_txt

    jwt_alerta = _jwt_longo("C" * 24, "D" * 24)
    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "http://127.0.0.1:5678/webhook/alerta-trilha")
    chamadas = []
    grade = Trilha(tmp_path / "trilha", transport=chamadas.append)
    grade.ver_sessao(sid="11" * 8, conta="ana", ip="203.0.113.8", origem="orig-1", app="n8n")
    grade.avisar(
        ip="203.0.113.8",
        conta=jwt_alerta,
        regra="dois_ips",
        detalhe="A conta também está no outro IP.",
        sid="22" * 8,
    )
    assert len(grade.sessoes()) == 1
    alerta = grade.alertas()[-1]
    assert alerta["regra"] == "dois_ips"
    assert alerta["conta"] == jwt_alerta[:4] + "\u2026" + jwt_alerta[-4:]
    bruto = (tmp_path / "trilha" / "alertas.jsonl").read_text(encoding="utf-8")
    assert jwt_alerta[8:-8] not in bruto
    assert jwt_alerta not in bruto
    assert chamadas
    assert jwt_alerta[8:-8] not in chamadas[-1]
    assert "regra=dois_ips" in chamadas[-1]
    assert "auth.sessao" not in chamadas[-1]


def test_revogar_sid_grava_auth_revogar_e_a_sessao_sai(tmp_path):
    from control_plane.trilha_auth import ler_auth, revogar_sessao

    pasta = tmp_path / ".n8groker" / "trilha"
    grade = Trilha(pasta)
    sid = "ab" * 8
    grade.ver_sessao(sid=sid, conta="ana", ip="203.0.113.8", origem="orig-1", app="n8n")
    assert len(grade.sessoes()) == 1

    def transport(method, url, payload, timeout):
        assert method == "POST"
        assert url == "http://127.0.0.1:8765/sessoes/revogar"
        assert payload == {"sid": sid}
        Trilha(pasta).revogar(payload["sid"])
        return {"ok": True}

    resultado = revogar_sessao(tmp_path, sid, conta="ana", ip="203.0.113.8", transport=transport)
    assert resultado["ok"] is True
    assert Trilha(pasta).sessoes() == []
    assert Trilha(pasta).revogada(sid) is True
    item = ler_auth(tmp_path)[-1]
    assert item["passo"] == "auth.revogar"
    assert item["resultado"] == "ok"
    assert item["motivo"] == "ok"
    assert item["conta"] == "ana"
    assert item["sid"] == sid
    assert not (pasta / "alertas.jsonl").exists()
    assert not (pasta / "trilha.jsonl").exists()


def test_recusa_de_sessao_no_portal_grava_auth_e_libera_o_seguinte(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from scout.core.origem import cookies_de_prova, gerar_chave, impressao

    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "http://127.0.0.1:5678/webhook/alerta-trilha")
    chamadas = []
    garantir(tmp_path)
    iniciar_usuario(caminho_geracao(tmp_path), "ana")
    privada = caminho_chave(tmp_path).read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    ec_priv, spki = gerar_chave()
    sid_recusado = "88" * 8
    token_recusado = emitir(
        privada,
        {
            "u": "ana",
            "ip": "203.0.113.10",
            "g": 1,
            "app": "n8n",
            "abrir": ["n8n"],
            "origem": "orig-1",
            "dispositivo": impressao(spki),
            "nonce": "nonce-1",
            "sid": sid_recusado,
        },
    )
    prova = cookies_de_prova(ec_priv, spki, "orig-1", "nonce-1", "2026-10-01T12:00:00Z")

    class _Fila:
        def consultar(self, ip, origem, conta, app):
            return {
                "status": "aprovado",
                "ip": ip,
                "conta_vinculada": "ana",
                "origem": "orig-1",
                "vinculo": "ativo",
            }

    pasta = tmp_path / ".n8groker" / "trilha"
    grade = Trilha(pasta, transport=chamadas.append)
    fila = _Fila()
    antes = montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
        trilha=grade,
    )
    seco = antes(
        {"caminho": "/n8n", "headers": {"x-forwarded-for": "203.0.113.10"}, "query": ""},
        "127.0.0.1",
        {},
    )
    assert seco["liga"] is False
    assert seco["status"] == 403
    assert not (pasta / "auth.jsonl").exists()

    grade.revogar(sid_recusado)
    recusado = antes(
        {
            "caminho": "/",
            "headers": {
                "x-forwarded-for": "203.0.113.10",
                "cookie": f"n8groker_sessao={token_recusado}",
            },
            "query": "",
        },
        "127.0.0.1",
        {},
    )
    assert recusado["liga"] is False
    assert recusado["status"] == 403
    auth = (pasta / "auth.jsonl").read_text(encoding="utf-8")
    assert '"passo": "auth.sessao"' in auth
    assert '"resultado": "recusado"' in auth
    assert '"motivo": "revogado"' in auth
    assert sid_recusado in auth
    assert token_recusado not in auth
    assert token_recusado[20:80] not in auth
    frases = grade.legiveis(ip="203.0.113.10")
    assert any("BLOQUEIO (sessao revogada)" in frase for frase in frases)
    assert all("auth.sessao" not in frase for frase in frases)
    assert chamadas == []

    token_ok = emitir(
        privada,
        {
            "u": "ana",
            "ip": "203.0.113.10",
            "g": 1,
            "app": "n8n",
            "abrir": ["n8n"],
            "origem": "orig-1",
            "dispositivo": impressao(spki),
            "nonce": "nonce-1",
            "sid": "99" * 8,
        },
    )
    liberado = antes(
        {
            "caminho": "/",
            "headers": {
                "x-forwarded-for": "203.0.113.10",
                "cookie": f"n8groker_sessao={token_ok}; {prova}",
            },
            "query": "",
        },
        "127.0.0.1",
        {},
    )
    assert liberado["liga"] is True
    assert any(frase.endswith("LIBERADO") for frase in grade.legiveis(ip="203.0.113.10"))
    recarregado = Trilha(pasta)
    assert "recusado" not in recarregado._resultados.get("203.0.113.10", [])
    for url in chamadas:
        assert "auth.sessao" not in url
        assert "auth.login" not in url
        assert token_ok not in url
        assert token_recusado not in url
        assert token_ok[20:80] not in url
    assert all(item.get("regra") != "auth.sessao" for item in grade.alertas())
    auth_final = (pasta / "auth.jsonl").read_text(encoding="utf-8")
    assert token_ok not in auth_final
    assert token_recusado not in auth_final
    assert token_ok not in (pasta / "trilha.jsonl").read_text(encoding="utf-8")
    from control_plane.trilha_auth import legiveis_auth

    visivel = legiveis_auth(tmp_path)
    assert any("auth.sessao" in linha and "revogado" in linha and sid_recusado in linha for linha in visivel)
    assert all(token_recusado not in linha and token_ok not in linha for linha in visivel)


def test_aba_e_compose_ficam_no_admin_do_console():
    painel = (ROOT / "control_plane" / "app.py").read_text(encoding="utf-8")
    aba = (ROOT / "control_plane" / "trilha_painel.py").read_text(encoding="utf-8")
    compose = (SCOUT / "docker-compose.yml").read_text(encoding="utf-8")
    script = (ROOT / "iniciar_servicos.ps1").read_text(encoding="utf-8")
    api = (SCOUT / "scout" / "server" / "api.py").read_text(encoding="utf-8")
    assert 'admin=_admin_sessao() and _modo() != "edge"' in painel
    assert "Revogar sessão" in aba
    assert "Visto" in aba
    assert "trilha.jsonl" in aba
    assert "auth.jsonl" in aba
    assert "linhas_auth" in aba
    assert ".n8groker/trilha:/run/trilha" in compose
    assert "admin.key:" not in compose
    assert "Ensure-TrilhaPasta" in script
    assert "/sessoes/revogar" in api
    assert "/alertas/visto" in api


def _linha_auth(conta, passo="auth.login", motivo="ok", resultado="ok", sid=""):
    item = {
        "quando": 1_700_000_000,
        "hora": "2026-10-07T00:00:00Z",
        "ip": "127.0.0.1",
        "conta": conta,
        "passo": passo,
        "resultado": resultado,
        "motivo": motivo,
    }
    if sid:
        item["sid"] = sid
    return json.dumps(item, ensure_ascii=False).encode("utf-8")


def test_utf8_rasgado_nao_derruba_a_leitura(tmp_path):
    from control_plane.trilha_auth import ler_auth

    pasta = tmp_path / ".n8groker" / "trilha"
    pasta.mkdir(parents=True)
    (pasta / "auth.jsonl").write_bytes(
        _linha_auth("ana") + b"\n" + b"\xff\xfe" + b"\n" + _linha_auth("bia") + b"\n"
    )
    assert [item["conta"] for item in ler_auth(tmp_path)] == ["ana", "bia"]
    trilha = tmp_path / "trilha"
    trilha.mkdir()
    agora = int(time.time())
    (trilha / "trilha.jsonl").write_bytes(
        (
            '{"quando":%d,"hora":"2026-10-07T00:00:00Z","ip":"203.0.113.8",'
            '"sid":"","conta":"ana","origem":"","app":"","passo":"porta","resultado":"BLOQUEIO","motivo":"negado"}\n'
            % agora
        ).encode("utf-8")
        + b"\xff\xfe\n"
    )
    frases = Trilha(trilha).legiveis(ip="203.0.113.8")
    assert any("BLOQUEIO" in frase for frase in frases)


def test_auth_jsonl_gira_com_o_mesmo_teto_da_trilha(tmp_path, monkeypatch):
    from control_plane.trilha_auth import anotar_auth

    monkeypatch.setenv("N8GROKER_TRILHA_MAX_BYTES", "80")
    pasta = tmp_path / ".n8groker" / "trilha"
    Trilha(pasta).anotar(ip="203.0.113.8", passo="/painel", resultado="ok")
    anotar_auth(
        tmp_path,
        ip="127.0.0.1",
        conta="primeira",
        passo="auth.login",
        resultado="recusado",
        motivo="formato",
    )
    anotar_auth(
        tmp_path,
        ip="127.0.0.1",
        conta="segunda",
        passo="auth.login",
        resultado="recusado",
        motivo="formato",
    )
    assert (pasta / "auth.jsonl.1").is_file()
    assert "primeira" in (pasta / "auth.jsonl.1").read_text(encoding="utf-8")
    assert "segunda" in (pasta / "auth.jsonl").read_text(encoding="utf-8")
    assert "primeira" not in (pasta / "auth.jsonl").read_text(encoding="utf-8")
    assert not (pasta / "trilha.jsonl.1").exists()
    assert (pasta / "auth.jsonl.lock").is_file()


def test_ler_auth_le_so_a_cauda_do_arquivo(tmp_path, monkeypatch):
    from control_plane.trilha_auth import anotar_auth, ler_auth

    anotar_auth(
        tmp_path,
        ip="127.0.0.1",
        conta="inicio-unico",
        passo="auth.login",
        resultado="ok",
        motivo="ok",
    )
    for indice in range(30):
        anotar_auth(
            tmp_path,
            ip="127.0.0.1",
            conta=f"meio{indice:02d}",
            passo="auth.login",
            resultado="recusado",
            motivo="formato",
        )
    anotar_auth(
        tmp_path,
        ip="127.0.0.1",
        conta="ultima-conta",
        passo="auth.login",
        resultado="ok",
        motivo="ok",
    )
    monkeypatch.setenv("N8GROKER_AUTH_CAUDA_BYTES", "900")
    itens = ler_auth(tmp_path)
    assert itens
    assert all(item.get("conta") != "inicio-unico" for item in itens)
    assert itens[-1]["conta"] == "ultima-conta"


def test_linhas_auth_mostra_no_maximo_30(tmp_path):
    from control_plane.trilha_auth import anotar_auth, linhas_auth

    for indice in range(40):
        anotar_auth(
            tmp_path,
            ip="127.0.0.1",
            conta=f"c{indice:02d}",
            passo="auth.login",
            resultado="recusado",
            motivo="formato",
        )
    linhas = linhas_auth(tmp_path)
    assert len(linhas) == 30
    assert "c00" not in "\n".join(linhas)
    assert "c39" in linhas[-1]


def test_filtro_por_sid_mostra_auth_sessao_e_auth_revogar(tmp_path):
    from control_plane.trilha_auth import anotar_auth, linhas_auth

    pasta = tmp_path / ".n8groker" / "trilha"
    sid = "ab" * 8
    outro = "cd" * 8
    Trilha(pasta).gravar_auth_sessao(
        ip="203.0.113.10", conta="ana", motivo_caminho="sem sessao", sid=sid
    )
    for indice in range(35):
        anotar_auth(
            tmp_path,
            ip="198.51.100.9",
            conta=f"conta{indice:02d}",
            passo="auth.login",
            resultado="recusado",
            motivo="formato",
        )
    anotar_auth(
        tmp_path,
        ip="203.0.113.10",
        conta="ana",
        passo="auth.revogar",
        resultado="ok",
        motivo="ok",
        sid=sid,
    )
    anotar_auth(
        tmp_path,
        ip="203.0.113.10",
        conta="bia",
        passo="auth.revogar",
        resultado="ok",
        motivo="ok",
        sid=outro,
    )
    linhas = linhas_auth(tmp_path, sid=sid)
    assert len(linhas) == 2
    assert all(sid in linha for linha in linhas)
    assert any("auth.sessao" in linha and "sem_sessao" in linha for linha in linhas)
    assert any("auth.revogar" in linha for linha in linhas)
    assert all(outro not in linha for linha in linhas)


def test_rajada_e_origem_avisam_so_na_tela(tmp_path, monkeypatch):
    from control_plane.trilha_auth import avisos_de_tela, ler_auth

    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "http://127.0.0.1:5678/webhook/alerta-trilha")
    chamadas = []
    marca, agora = _relogio()
    pasta = tmp_path / ".n8groker" / "trilha"
    grade = Trilha(pasta, agora=agora, transport=chamadas.append)
    grade.ver_sessao(sid="aa" * 8, conta="ana", ip="203.0.113.10", origem="orig-1", app="n8n")
    for _ in range(4):
        grade.gravar_auth_sessao(
            ip="203.0.113.10", conta="ana", motivo_caminho="sem sessao", sid="aa" * 8
        )
    assert avisos_de_tela(ler_auth(tmp_path), agora=int(marca["t"])) == []
    grade.gravar_auth_sessao(
        ip="203.0.113.10", conta="ana", motivo_caminho="sem sessao", sid="aa" * 8
    )
    avisos = avisos_de_tela(ler_auth(tmp_path), agora=int(marca["t"]))
    assert any("juntou 5" in aviso and "203.0.113.10" in aviso for aviso in avisos)
    assert not any("motivo origem" in aviso for aviso in avisos)
    assert not (pasta / "alertas.jsonl").exists()
    assert chamadas == []
    assert len(grade.sessoes()) == 1
    marca["t"] += 120
    assert avisos_de_tela(ler_auth(tmp_path), agora=int(marca["t"])) == []
    grade.gravar_auth_sessao(
        ip="203.0.113.10", conta="ana", motivo_caminho="origem nao confere", sid="aa" * 8
    )
    avisos = avisos_de_tela(ler_auth(tmp_path), agora=int(marca["t"]))
    assert any("motivo origem" in aviso and "203.0.113.10" in aviso for aviso in avisos)
    assert not any("juntou" in aviso for aviso in avisos)
    assert chamadas == []
    assert not (pasta / "alertas.jsonl").exists()
    assert len(Trilha(pasta, agora=agora).sessoes()) == 1


def test_botao_revogar_chama_a_api_e_calha_sem_anunciar_sucesso(tmp_path):
    import ast

    from scout.gate import GateError

    from control_plane.trilha_auth import mensagens_da_revogacao, revogar_sessao

    sid = "ab" * 8
    chamadas = []

    def transport(method, url, payload, timeout):
        chamadas.append((method, url, payload))
        raise GateError("O Scout não respondeu em http://127.0.0.1:8765.")

    resultado = revogar_sessao(tmp_path, sid, conta="ana", ip="203.0.113.8", transport=transport)
    assert chamadas == [("POST", "http://127.0.0.1:8765/sessoes/revogar", {"sid": sid})]
    aviso, erro = mensagens_da_revogacao(resultado)
    assert aviso == ""
    assert erro
    assert "Sessão revogada" not in erro
    pasta = tmp_path / ".n8groker" / "trilha"
    assert not (pasta / "auth.jsonl").exists()
    assert not (pasta / "revogadas.json").exists()
    arvore = ast.parse((ROOT / "control_plane" / "trilha_painel.py").read_text(encoding="utf-8"))
    render = next(no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name == "render_trilha_admin")
    nomes = []
    for no in ast.walk(render):
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name):
            nomes.append(no.func.id)
        if isinstance(no, ast.Constant) and isinstance(no.value, str):
            assert "Sessão revogada" not in no.value
    assert "revogar_sessao" in nomes
    assert "mensagens_da_revogacao" in nomes


def test_api_ok_grava_auth_revogar_sem_o_host_escrever_revogadas(tmp_path):
    from control_plane.trilha_auth import ler_auth, revogar_sessao

    pasta = tmp_path / ".n8groker" / "trilha"
    sid = "ef" * 8
    Trilha(pasta).ver_sessao(sid=sid, conta="ana", ip="203.0.113.8", origem="orig-1", app="n8n")

    def transport(method, url, payload, timeout):
        assert payload == {"sid": sid}
        return {"ok": True}

    resultado = revogar_sessao(tmp_path, sid, conta="ana", ip="203.0.113.8", transport=transport)
    assert resultado["ok"] is True
    assert "Sessão revogada" in resultado["mensagem"]
    assert not (pasta / "revogadas.json").exists()
    assert Trilha(pasta).revogada(sid) is False
    assert len(Trilha(pasta).sessoes()) == 1
    item = ler_auth(tmp_path)[-1]
    assert item["passo"] == "auth.revogar"
    assert item["sid"] == sid


def test_segunda_sessao_da_mesma_conta_continua_viva(tmp_path):
    from control_plane.trilha_auth import revogar_sessao

    pasta = tmp_path / ".n8groker" / "trilha"
    sid_um = "aa" * 8
    sid_dois = "bb" * 8
    grade = Trilha(pasta)
    grade.ver_sessao(sid=sid_um, conta="ana", ip="203.0.113.8", origem="orig-1", app="n8n")
    grade.ver_sessao(sid=sid_dois, conta="ana", ip="203.0.113.9", origem="orig-2", app="n8n")

    def transport(method, url, payload, timeout):
        assert payload == {"sid": sid_um}
        Trilha(pasta).revogar(payload["sid"])
        return {"ok": True}

    assert revogar_sessao(tmp_path, sid_um, conta="ana", ip="203.0.113.8", transport=transport)["ok"] is True
    vivas = Trilha(pasta).sessoes()
    assert [item["sid"] for item in vivas] == [sid_dois]
    assert Trilha(pasta).revogada(sid_dois) is False


def test_admin_entra_pelo_colar_depois_da_revogacao(tmp_path):
    from control_plane.admin_token import aceitar, init_keys, issue
    from control_plane.trilha_auth import revogar_sessao

    init_keys(tmp_path)
    token = issue(tmp_path)
    pasta = tmp_path / ".n8groker" / "trilha"
    sid = "cd" * 8
    Trilha(pasta).ver_sessao(sid=sid, conta="ana", ip="203.0.113.8", origem="orig-1", app="n8n")

    def transport(method, url, payload, timeout):
        Trilha(pasta).revogar(payload["sid"])
        return {"ok": True}

    assert revogar_sessao(tmp_path, sid, conta="ana", ip="203.0.113.8", transport=transport)["ok"] is True
    claims = aceitar(tmp_path, token, modo="console", ip="127.0.0.1")
    assert claims["sub"] == "admin"


def test_auth_sessao_grava_o_sid_que_o_cookie_ja_tem(tmp_path):
    """sem_sessao no /painel/sessao sem ticket ainda grava o sid do cookie."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from control_plane.trilha_auth import linhas_auth
    from scout.core.origem import cookies_de_prova, gerar_chave, impressao

    ip = "203.0.113.10"
    sid = "ab" * 8
    garantir(tmp_path)
    iniciar_usuario(caminho_geracao(tmp_path), "ana")
    privada = caminho_chave(tmp_path).read_bytes()
    publica = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    ec_priv, spki = gerar_chave()
    prova = cookies_de_prova(ec_priv, spki, "orig-1", "nonce-1", "2026-10-01T12:00:00Z")
    token = emitir(
        privada,
        {
            "u": "ana",
            "ip": ip,
            "g": 1,
            "app": "n8n",
            "abrir": ["n8n"],
            "origem": "orig-1",
            "dispositivo": impressao(spki),
            "nonce": "nonce-1",
            "sid": sid,
        },
    )

    class _Fila:
        def consultar(self, endereco, origem, conta, app):
            return {
                "status": "aprovado",
                "ip": endereco,
                "conta_vinculada": "ana",
                "origem": "orig-1",
                "vinculo": "ativo",
                "origens": [{"origem": "orig-1", "status": "aprovado"}],
            }

    pasta = tmp_path / ".n8groker" / "trilha"
    grade = Trilha(pasta)
    fila = _Fila()
    antes = montar_politica(
        antes_do_porteiro(fila),
        publica,
        privada,
        caminho_geracao(tmp_path),
        fila=fila,
        trilha=grade,
    )
    liberado = antes(
        {
            "caminho": "/",
            "headers": {
                "x-forwarded-for": ip,
                "cookie": f"n8groker_sessao={token}; {prova}",
            },
            "query": "",
        },
        "127.0.0.1",
        {},
    )
    assert liberado["liga"] is True
    caminho = [
        json.loads(linha)
        for linha in (pasta / "trilha.jsonl").read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    assert any(item.get("sid") == sid for item in caminho)
    recusa = antes(
        {
            "caminho": "/painel/sessao",
            "headers": {
                "x-forwarded-for": ip,
                "cookie": f"n8groker_sessao={token}; {prova}",
            },
            "query": "",
        },
        "127.0.0.1",
        {},
    )
    assert recusa["status"] == 403
    auth = (pasta / "auth.jsonl").read_text(encoding="utf-8")
    assert token not in auth
    linha = json.loads(auth.strip().splitlines()[-1])
    assert linha["passo"] == "auth.sessao"
    assert linha["motivo"] == "sem_sessao"
    assert linha["sid"] == sid
    assert any("auth.sessao" in item and sid in item for item in linhas_auth(tmp_path, sid=sid))
    antes(
        {
            "caminho": "/painel/sessao",
            "headers": {"x-forwarded-for": ip},
            "query": "",
        },
        "127.0.0.1",
        {},
    )
    vazia = json.loads((pasta / "auth.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert vazia["motivo"] == "sem_sessao"
    assert "sid" not in vazia
