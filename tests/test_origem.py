"""Origem presa à chave. Duas origens no mesmo IP não derrubam a primeira."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.origem import anotar_origem, conferir, cookies_de_prova, gerar_chave, impressao  # noqa: E402
from scout.core.porta_apps import exige_porteiro  # noqa: E402


def test_assinatura_e_impressao_sem_mac():
    privada, spki = gerar_chave()
    prova = cookies_de_prova(privada, spki, "orig-1", "nonce-1", "2026-10-01T12:00:00Z")
    assert "n8groker_assinatura=" in prova
    texto = (SCOUT / "scout" / "core" / "origem.py").read_text(encoding="utf-8").lower()
    js = (SCOUT / "scout" / "static" / "origem.js").read_text(encoding="utf-8")
    assert "mac_address" not in texto
    assert '"mac"' not in texto
    assert "extractable" not in js.lower() or "false" in js
    assert "P-256" in js
    assert "getUserMedia" not in js
    assert "mac_address" not in js.lower()
    assert "/painel/registrar-origem" in js
    assert "origem-aviso" in js
    assert 'modo === "agora" && entrou' in js
    antes_do_ok = js.split("resposta.ok", 1)[0]
    assert 'cookie("n8groker_origem"' not in antes_do_ok
    depois_do_ok = js.split("resposta.ok", 1)[1]
    assert depois_do_ok.index('cookie("n8groker_origem"') < depois_do_ok.index("n8groker_horario")
    agora = js.split('modo === "agora"', 1)[1].split("400", 1)[0]
    assert "n8groker_origem=" in agora
    assert "Este navegador recusou o cookie. Ative cookies para este site" in js
    assert "Tentar de novo" in js
    assert "SameSite=Lax" in js
    assert "; Secure;" in js
    assert 'digest("SHA-256"' in js
    assert "isSecureContext" in js
    assert ".catch(function () {})" not in js
    dispositivo = impressao(spki)
    from scout.core.origem import spki_do_texto

    spki2 = spki_do_texto(prova.split("n8groker_spki=")[1].split(";")[0])
    assinatura = prova.split("n8groker_assinatura=")[1].split(";")[0]
    assert conferir(spki2, "nonce-1", "orig-1", "2026-10-01T12:00:00Z", assinatura)
    assert impressao(spki2) == dispositivo
    assert conferir(spki2, "outro", "orig-1", "2026-10-01T12:00:00Z", assinatura) is False


def test_segunda_origem_nao_derruba_a_aprovada():
    registro = {"ip": "203.0.113.10", "status": "aprovado", "origens": []}
    primeira = anotar_origem(registro, "orig-1", "aaa")
    primeira["item"]["status"] = "aprovado"
    segunda = anotar_origem(registro, "orig-2", "bbb")
    assert segunda["nova"] is True
    assert segunda["origem_aprovada"] == "orig-1"
    assert segunda["item"]["status"] == "pendente"
    assert registro["origens"][0]["status"] == "aprovado"

    def consultar(ip, origem, conta, app):
        return {
            "status": "aprovado",
            "conta_vinculada": "ana",
            "vinculo": "ativo",
            "origens": registro["origens"],
            "ip": ip,
        }

    segue = exige_porteiro("n8n", "203.0.113.10", "ana", "orig-1", consultar)
    trava = exige_porteiro("n8n", "203.0.113.10", "ana", "orig-2", consultar)
    assert segue["liga"] is True
    assert trava["liga"] is False
    assert trava["status"] == 202
