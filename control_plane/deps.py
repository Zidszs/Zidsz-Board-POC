"""Dependências que o painel precisa de verdade, além do Streamlit."""

from __future__ import annotations


def modulos_ausentes() -> list[str]:
    faltando: list[str] = []
    for nome in ("cryptography",):
        try:
            __import__(nome)
        except ImportError:
            faltando.append(nome)
    return faltando


def mensagem_dependencia(faltando: list[str]) -> str:
    nomes = ", ".join(faltando) if faltando else "cryptography"
    return (
        "Falta dependência do painel ("
        + nomes
        + "). Um venv antigo pode ter só o Streamlit. "
        "Com o Python desse venv, na raiz do projeto: "
        "python -m pip install -r control_plane/requirements.txt. "
        "O iniciar_servicos.ps1 reinstala sozinho quando o requirements.txt muda "
        "ou quando esse import falha."
    )
