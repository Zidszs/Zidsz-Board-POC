"""Parada limpa com stdin fechado não pode morrer no Read-Host."""

from pathlib import Path


def test_confirm_interactive_salta_sem_console():
    texto = Path("iniciar_servicos.ps1").read_text(encoding="utf-8-sig")
    inicio = texto.index("function Test-ConsoleInterativo")
    meio = texto.index("function Confirm-Interactive")
    sonda = texto[inicio:meio]
    assert "UserInteractive" in sonda
    assert "IsInputRedirected" in sonda
    bloco = texto[meio:].split("\nfunction ", 1)[0]
    assert bloco.index("Test-ConsoleInterativo") < bloco.index("Read-Host")
