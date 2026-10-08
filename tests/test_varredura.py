"""Nove prefixos negados bloqueiam. Assets do painel não. Desbloquear não apaga alias."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.varredura import Varredura  # noqa: E402


def _relogio(inicio=1_000.0):
    marca = {"t": inicio}
    return marca, lambda: marca["t"]


def test_nove_prefixos_bloqueiam_e_o_prazo_libera():
    marca, agora = _relogio()
    grade = Varredura(agora=agora)
    aliases = {"203.0.113.8": "casa"}
    ip = "203.0.113.8"
    for indice in range(9):
        grade.observar(ip, f"/rota{indice}", negado=True)
    assert grade.bloqueado(ip) is True
    assert grade.linhas[-1]["acao"] == "varredura"
    assert grade.linhas[-1]["resultado"] == "bloqueado"
    marca["t"] += 899
    assert grade.bloqueado(ip) is True
    marca["t"] += 1
    assert grade.bloqueado(ip) is False
    assert aliases == {"203.0.113.8": "casa"}


def test_assets_do_painel_nao_varrem():
    _marca, agora = _relogio()
    grade = Varredura(agora=agora)
    ip = "203.0.113.8"
    for indice in range(40):
        grade.observar(ip, f"/painel/static/app{indice}.js", negado=False)
    grade.observar(ip, "/painel/_stcore/stream", negado=False)
    assert grade.bloqueado(ip) is False
    assert grade.linhas == []


def test_desbloquear_so_aquele_ip():
    _marca, agora = _relogio()
    grade = Varredura(agora=agora)
    for indice in range(9):
        grade.observar("203.0.113.8", f"/a{indice}", negado=True)
        grade.observar("203.0.113.9", f"/b{indice}", negado=True)
    grade.desbloquear("203.0.113.8", "admin")
    assert grade.bloqueado("203.0.113.8") is False
    assert grade.bloqueado("203.0.113.9") is True
    assert grade.linhas[-1]["acao"] == "desbloquear_varredura"


def test_rota_manual_fora_da_lista_conta():
    _marca, agora = _relogio()
    grade = Varredura(agora=agora)
    for nome in ("litellm", "langfuse", "n8n", "postgres", "redis", "minio", "ngrok", "scout"):
        grade.observar("203.0.113.8", "/" + nome, negado=True)
    assert grade.bloqueado("203.0.113.8") is True


def test_linha_varredura_entra_no_audit(tmp_path, monkeypatch):
    arquivo = tmp_path / "audit.jsonl"
    monkeypatch.setenv("N8GROKER_AUDIT_ARQUIVO", str(arquivo))
    _marca, agora = _relogio()
    grade = Varredura(agora=agora)
    for indice in range(9):
        grade.observar("203.0.113.8", f"/rota{indice}", negado=True)
    linhas = [json.loads(item) for item in arquivo.read_text(encoding="utf-8").splitlines()]
    assert linhas[-1]["acao"] == "varredura"
    assert linhas[-1]["resultado"] == "bloqueado"
    assert linhas[-1]["ip"] == "203.0.113.8"
    assert "rota0" in linhas[-1]["alvo"]
    assert "eyJ" not in arquivo.read_text(encoding="utf-8")


def test_compose_monta_o_audit_sem_chave_admin():
    texto = (SCOUT / "docker-compose.yml").read_text(encoding="utf-8")
    script = (ROOT / "iniciar_servicos.ps1").read_text(encoding="utf-8")
    assert "audit.jsonl:/run/audit.jsonl" in texto
    assert "N8GROKER_AUDIT_ARQUIVO=/run/audit.jsonl" in texto
    assert "admin.key:" not in texto
    assert "Ensure-AuditArquivo" in script


def test_api_e_aba_expoem_o_desbloqueio():
    api = (SCOUT / "scout" / "server" / "api.py").read_text(encoding="utf-8")
    painel = (ROOT / "control_plane" / "app.py").read_text(encoding="utf-8")
    assert "/varredura/desbloquear" in api
    assert "desbloquear_varredura" in painel
    assert "127.0.0.1:8765/varredura" in painel
