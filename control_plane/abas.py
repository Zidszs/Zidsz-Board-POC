"""Qual seção o rádio mostra. O clique do usuário não volta para a URL antiga."""

from __future__ import annotations


def sincronizar_aba(widget: str | None, url_aba: str, aplicada: str | None, abas: tuple[str, ...]) -> str:
    """A URL manda na primeira abertura e quando o link muda.

    Se o rádio já mudou e a URL ainda está na seção anterior, fica o rádio.
    """
    if widget not in abas:
        return url_aba
    if aplicada != url_aba and widget == aplicada:
        return url_aba
    return widget
