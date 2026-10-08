"""IP lido na conexão, com as mesmas regras de Porteiro/identidade.js."""

from __future__ import annotations

import re


def normalizar_ip(ip: str) -> str:
    if not ip:
        return ""
    texto = str(ip).strip()
    if texto.startswith("::ffff:"):
        return texto[7:]
    return texto


def eh_localhost(ip: str) -> bool:
    return normalizar_ip(ip) in {"127.0.0.1", "::1"}


def eh_rede_docker(ip: str) -> bool:
    n = normalizar_ip(ip)
    match = re.match(r"^172\.(\d+)\.", n)
    if match and 16 <= int(match.group(1)) <= 31:
        return True
    return re.match(r"^192\.168\.65\.\d{1,3}$", n) is not None


def eh_formato_ip(ip: str) -> bool:
    if not ip or len(ip) > 45:
        return False
    return re.match(r"^[0-9a-fA-F:.]+$", ip) is not None


def _primeiro(xff: str) -> str:
    if not xff or not isinstance(xff, str):
        return ""
    primeiro = xff.split(",")[0].strip()
    if not eh_formato_ip(primeiro):
        return ""
    return normalizar_ip(primeiro)


def _ultimo(xff: str) -> str:
    """O ngrok acrescenta o IP do cliente no fim. O primeiro valor o visitante pode forjar."""
    if not xff or not isinstance(xff, str):
        return ""
    for parte in reversed(xff.split(",")):
        texto = parte.strip()
        if eh_formato_ip(texto):
            return normalizar_ip(texto)
    return ""


def ips_do_nome(nome: str) -> set[str]:
    """Resolve na hora. Não guarda o IP: o container pode mudar de endereço."""
    texto = str(nome or "").strip()
    if not texto:
        return set()
    import socket

    try:
        infos = socket.getaddrinfo(texto, None)
    except OSError:
        return set()
    saida = set()
    for info in infos:
        ip = normalizar_ip(info[4][0])
        if ip and not eh_localhost(ip):
            saida.add(ip)
    return saida


def _proxies(lista) -> set[str]:
    if isinstance(lista, (list, tuple, set)):
        itens = lista
    else:
        itens = str(lista or "").split(",")
    return {normalizar_ip(str(item).strip()) for item in itens if str(item).strip()}


def decidir_ip(socket_ip: str, xff: str, proxies=None, *, hop: str = "primeiro") -> dict:
    socket = normalizar_ip(socket_ip)
    conhecidos = _proxies(proxies)
    escolher = _ultimo if hop == "ultimo" else _primeiro
    if eh_localhost(socket):
        hop_ip = _primeiro(xff)
        if not hop_ip or eh_localhost(hop_ip):
            return {"acao": "local", "ip": socket, "via": "loopback", "socket": socket}
        if eh_rede_docker(hop_ip):
            return {
                "acao": "negar",
                "ip": "",
                "via": "rede-docker",
                "socket": socket,
                "motivo": "A rede do Docker não conta como localhost.",
            }
        return {"acao": "visitante", "ip": hop_ip, "via": "x-forwarded-for", "socket": socket}
    if socket in conhecidos:
        hop_ip = escolher(xff)
        if not hop_ip or eh_localhost(hop_ip) or eh_rede_docker(hop_ip):
            return {
                "acao": "negar",
                "ip": "",
                "via": "proxy-sem-cliente",
                "socket": socket,
                "motivo": "Proxy confiável sem IP de cliente. Acesso negado.",
            }
        return {"acao": "visitante", "ip": hop_ip, "via": "x-forwarded-for", "socket": socket}
    if eh_rede_docker(socket):
        return {
            "acao": "negar",
            "ip": "",
            "via": "rede-docker",
            "socket": socket,
            "motivo": "A rede do Docker não conta como localhost.",
        }
    if not eh_formato_ip(socket):
        return {
            "acao": "negar",
            "ip": "",
            "via": "socket-invalido",
            "socket": socket,
            "motivo": "IP de origem inválido.",
        }
    return {"acao": "visitante", "ip": socket, "via": "socket", "socket": socket}


def decidir_visita(socket_ip: str, xff: str, nome_proxy: str = "", proxies=None) -> dict:
    """Confia no X-Forwarded-For só se o peer for o IP atual do proxy nomeado.

    O gateway da rede Docker não entra nessa lista. Outro container, mesmo
    mandando o header, continua negado como rede-docker.
    """
    vivos = ips_do_nome(nome_proxy)
    socket = normalizar_ip(socket_ip)
    if socket and socket in vivos:
        return decidir_ip(socket_ip, xff, vivos, hop="ultimo")
    return decidir_ip(socket_ip, xff, proxies, hop="primeiro")
