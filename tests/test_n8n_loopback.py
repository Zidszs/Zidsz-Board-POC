"""O n8n local continua em 127.0.0.1:5678, sem o Porteiro na frente."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_publica_so_no_loopback_e_o_proxy_aponta_para_la():
    compose = (ROOT / "n8n" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:5678:5678" in compose
    assert '"5678:5678"' not in compose
    porteiro = (ROOT / "Porteiro" / "porteiro.js").read_text(encoding="utf-8")
    assert "hostname: '127.0.0.1'" in porteiro
    assert "port: 5678" in porteiro
    assert "listen(5678" not in porteiro
    assert "PORTA_DO_PORTEIRO" in porteiro
    assert porteiro.count("5678") >= 1
