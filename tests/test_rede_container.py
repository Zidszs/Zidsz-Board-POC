"""O túnel no Docker Desktop: peer do ngrok, upstream do container e trilha do bloqueio."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "Scout_OSINT_Docker"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

from scout.core.ip_vivo import decidir_visita  # noqa: E402
from scout.core.politica_portal import montar_politica  # noqa: E402
from scout.core.porta_apps import antes_do_porteiro, destino_de  # noqa: E402
from scout.core.trilha import Trilha, url_do_alerta  # noqa: E402

NGROK = "172.18.0.7"
GATEWAY = "172.18.0.1"
OUTRO = "172.18.0.9"
CLIENTE = "203.0.113.47"


def _dns(monkeypatch):
    def ips(nome):
        if nome == "ngrok_service":
            return {NGROK}
        return set()

    monkeypatch.setattr("scout.core.ip_vivo.ips_do_nome", ips)
    monkeypatch.setenv("N8GROKER_PROXY_NOME", "ngrok_service")
    monkeypatch.setenv("PORTEIRO_TRUSTED_PROXIES", "")


def test_gateway_com_proxies_vazios_nega_e_grava_a_trilha(tmp_path, monkeypatch):
    _dns(monkeypatch)
    visto = decidir_visita(GATEWAY, CLIENTE, "ngrok_service", "")
    assert visto["acao"] == "negar"
    assert visto["via"] == "rede-docker"
    assert visto["ip"] == ""

    grade = Trilha(tmp_path / "trilha")

    class Fila:
        def consultar(self, ip, origem, conta, app):
            raise AssertionError("o gateway não chega na fila")

    antes = montar_politica(antes_do_porteiro(Fila()), trilha=grade)
    acao = antes(
        {"caminho": "/painel", "headers": {"x-forwarded-for": CLIENTE}, "query": ""},
        GATEWAY,
        {},
    )
    assert acao["status"] == 403
    assert b"Acesso em Analise" not in acao["corpo"]
    linhas = grade.legiveis(ip=GATEWAY)
    assert linhas
    assert "rede-docker" in linhas[0]
    assert "BLOQUEIO" in linhas[0]
    assert grade.legiveis(ip=CLIENTE) == []


def test_ngrok_resolvido_na_hora_usa_o_ultimo_xff(monkeypatch):
    _dns(monkeypatch)
    visto = decidir_visita(NGROK, f"198.51.100.9, {CLIENTE}", "ngrok_service", "")
    assert visto["acao"] == "visitante"
    assert visto["ip"] == CLIENTE
    falso = decidir_visita(OUTRO, CLIENTE, "ngrok_service", "")
    assert falso["acao"] == "negar"
    assert falso["via"] == "rede-docker"
    loop = decidir_visita(NGROK, f"{CLIENTE}, 127.0.0.1", "ngrok_service", "")
    assert loop["acao"] == "negar"
    assert loop["via"] == "proxy-sem-cliente"
    docker = decidir_visita(NGROK, f"{CLIENTE}, 172.18.0.4", "ngrok_service", "")
    assert docker["acao"] == "negar"


def test_painel_pelo_ngrok_fica_em_analise_e_nao_esvazia_a_trilha(tmp_path, monkeypatch):
    _dns(monkeypatch)
    grade = Trilha(tmp_path / "trilha")

    class Fila:
        def consultar(self, ip, origem, conta, app):
            assert ip == CLIENTE
            return None

    antes = montar_politica(antes_do_porteiro(Fila()), trilha=grade)
    acao = antes(
        {"caminho": "/painel", "headers": {"x-forwarded-for": f"198.51.100.9, {CLIENTE}"}, "query": ""},
        NGROK,
        {},
    )
    assert acao["status"] == 202
    assert b"Acesso em Analise" in acao["corpo"]
    assert grade.legiveis(ip=CLIENTE)


def test_painel_aprovado_aponta_para_o_host(tmp_path, monkeypatch):
    _dns(monkeypatch)
    monkeypatch.delenv("PAINEL_BORDA_HOST", raising=False)
    chave = tmp_path / "porteiro-hmac.key"
    chave.write_bytes(b"k" * 32)
    monkeypatch.setenv("PORTEIRO_HMAC_KEY_ARQUIVO", str(chave))

    class Fila:
        def consultar(self, ip, origem, conta, app):
            return {
                "status": "aprovado",
                "ip": ip,
                "origens": [{"origem": "orig-1", "status": "aprovado"}],
            }

    fila = Fila()
    antes = montar_politica(antes_do_porteiro(fila), fila=fila)
    acao = antes(
        {
            "caminho": "/painel",
            "headers": {"x-forwarded-for": CLIENTE, "cookie": "n8groker_origem=orig-1"},
            "query": "",
        },
        NGROK,
        {},
    )
    assert acao["liga"] is True
    assert acao["host"] == "host.docker.internal"
    assert acao["port"] == 8502
    assert str(acao.get("injetar_pedido") or "").startswith("x-n8groker-client: ")


def test_upstream_dos_apps_e_o_nome_do_container(monkeypatch):
    monkeypatch.delenv("N8N_UPSTREAM_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_UPSTREAM_HOST", raising=False)
    monkeypatch.delenv("LITELLM_UPSTREAM_HOST", raising=False)
    assert destino_de("n8n") == ("n8n_app", 5678)
    assert destino_de("langfuse") == ("langfuse-web", 3000)
    assert destino_de("litellm") == ("litellm", 4000)
    monkeypatch.setenv("N8N_UPSTREAM_HOST", "n8n_app")
    monkeypatch.setenv("N8N_UPSTREAM_PORT", "5678")
    assert destino_de("n8n")[0] == "n8n_app"
    assert "127.0.0.1" not in destino_de("n8n")[0]


def test_webhook_de_alerta_alcanca_o_n8n_do_container(tmp_path, monkeypatch):
    chamadas = []
    monkeypatch.setenv("SCOUT_BACKEND", "1")
    monkeypatch.delenv("N8GROKER_ALERTA_WEBHOOK", raising=False)
    monkeypatch.delenv("N8N_UPSTREAM_HOST", raising=False)
    assert url_do_alerta() == "http://n8n_app:5678/webhook/alerta-trilha"
    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "http://127.0.0.1:5678/webhook/alerta-trilha")
    assert url_do_alerta().startswith("http://n8n_app:5678/webhook/alerta-trilha")
    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "https://exemplo.invalid/webhook/alerta-trilha")
    grade = Trilha(tmp_path / "trilha", transport=chamadas.append)
    grade.anotar(ip=CLIENTE, sid="aa" * 8, conta="ana", app="n8n", passo="n8n", resultado="LIBERADO")
    assert chamadas == []
    monkeypatch.setenv("N8GROKER_ALERTA_WEBHOOK", "http://203.0.113.9/webhook/alerta-trilha")
    grade2 = Trilha(tmp_path / "trilha2", transport=chamadas.append, agora=lambda: 1_700_000_500)
    grade2.anotar(ip="203.0.113.48", sid="bb" * 8, conta="ana", app="n8n", passo="n8n", resultado="LIBERADO")
    assert chamadas == []


def test_compose_entrega_proxy_upstream_e_webhook():
    scout = (SCOUT / "docker-compose.yml").read_text(encoding="utf-8")
    ngrok = (ROOT / "ngrok" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "N8GROKER_PROXY_NOME=ngrok_service" in scout
    assert "PAINEL_BORDA_HOST=${PAINEL_BORDA_HOST:-host.docker.internal}" in scout
    assert "N8N_UPSTREAM_HOST=${N8N_UPSTREAM_HOST:-n8n_app}" in scout
    assert "LANGFUSE_UPSTREAM_HOST=${LANGFUSE_UPSTREAM_HOST:-langfuse-web}" in scout
    assert "LITELLM_UPSTREAM_HOST=${LITELLM_UPSTREAM_HOST:-litellm}" in scout
    assert "N8GROKER_ALERTA_WEBHOOK=${N8GROKER_ALERTA_WEBHOOK:-http://n8n_app:5678/webhook/alerta-trilha}" in scout
    assert "admin.key:" not in scout
    assert "${NGROK_TUNNEL_HOST:-host.docker.internal}:${NGROK_TUNNEL_TARGET:-5677}" in ngrok
    script = (ROOT / "iniciar_servicos.ps1").read_text(encoding="utf-8")
    boot = script.split("\nInvoke-AutoSetup\n", 1)[1].split("LOOP DO PAINEL", 1)[0]
    assert boot.index("Start-Scout") < boot.index("COMPOSE_NGROK")
    assert "$env:NGROK_TUNNEL_HOST = $planoNgrok.Host" in boot
    plano = (ROOT / "scripts" / "boot_scout.ps1").read_text(encoding="utf-8")
    assert 'Host = "scout-backend"' in plano
