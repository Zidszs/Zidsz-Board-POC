"""Contas em `.n8groker/users.json`. O admin da máquina não é uma dessas contas.

Não há auto-cadastro e não há senha. Quem cria a conta é a sessão admin.
O login é um JWT de usuário emitido na aba Admin. Hash antigo neste arquivo,
ou em `controle_acesso.json`, é ignorado: entra só com token novo.

`auth.json` no formato antigo é renomeado para `auth.json.legado` e deixa
de autenticar. `python -m control_plane.auth --reset` não apaga usuários
nem a auditoria e não cria senha de admin.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import sys
from pathlib import Path

from control_plane.audit import auditar

_USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
_CAMPOS_SENHA = ("salt", "hash", "n", "r", "p", "must_change_password", "password", "senha")
_CAMPOS_SENHA_PORTEIRO = ("senha", "password", "hash", "salt", "must_change_password")


class AuthError(Exception):
    def __init__(self, message: str, motivo: str = ""):
        super().__init__(message)
        self.message = message
        self.motivo = motivo


_ASPAS = {'"', "'", "\u201c", "\u201d", "\u2018", "\u2019", "«", "»"}
_INVISIVEIS = ("\ufeff", "\u200b", "\u200c", "\u200d", "\u2060", "\u00a0", "\u202f")
MENSAGEM_FORMATO = "Cole de novo numa linha só, sem aspas e sem quebra de linha."


def normalizar_token(token: str) -> str:
    """Uma cola só, antes de validar qualquer token.

    O console (8501), a borda (8502) e o /painel passam por aqui.
    Tira invisíveis (BOM, U+200B, NBSP), aspas em qualquer lado e espaço ou quebra.
    """
    if not isinstance(token, str):
        return ""
    texto = token
    for invisivel in _INVISIVEIS:
        texto = texto.replace(invisivel, "")
    for aspa in _ASPAS:
        texto = texto.replace(aspa, "")
    return "".join(texto.split())


def auth_path(root: Path) -> Path:
    return _na_pasta(root, "auth.json")


def users_path(root: Path) -> Path:
    return _na_pasta(root, "users.json")


def _na_pasta(root: Path, nome: str) -> Path:
    path = (root / ".n8groker" / nome).resolve()
    base = (root.resolve() / ".n8groker").resolve()
    if path.parent != base:
        raise AuthError("Arquivo de conta fora da pasta local.")
    return path


def account_exists(root: Path) -> bool:
    return bool(load_users(root))


def _check_username(username: str) -> str:
    if not isinstance(username, str):
        raise AuthError("Usuário recusado. Use de 3 a 64 letras, números, ponto, _ ou -.")
    cleaned = username.strip()
    if not _USERNAME.fullmatch(cleaned):
        raise AuthError("Usuário recusado. Use de 3 a 64 letras, números, ponto, _ ou -.")
    return cleaned


def _same_text(left: str, right: str) -> bool:
    a = left.encode("utf-8")
    b = right.encode("utf-8")
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def service_ids() -> tuple[str, ...]:
    from control_plane.config import default_services

    return tuple(spec.id for spec in default_services())


def _listas(valor, conhecidos: tuple[str, ...]) -> list[str]:
    if valor is None:
        return []
    if not isinstance(valor, list):
        raise AuthError("Lista de permissão recusada.")
    saida = []
    for item in valor:
        if not isinstance(item, str) or item not in conhecidos or item in saida:
            raise AuthError("Lista de permissão recusada.")
        saida.append(item)
    return saida


def migrar_legado(root: Path) -> bool:
    path = auth_path(root)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or "users" in data:
        return False
    if "username" not in data or "hash" not in data:
        return False
    destino = path.with_name("auth.json.legado")
    if destino.exists():
        return False
    path.replace(destino)
    return True


def _sem_senha(registro: dict) -> tuple[dict, bool]:
    if not any(campo in registro for campo in _CAMPOS_SENHA):
        return registro, False
    limpo = {chave: valor for chave, valor in registro.items() if chave not in _CAMPOS_SENHA}
    return limpo, True


def _tirar_senhas(valor):
    if isinstance(valor, dict):
        mudou = False
        saida = {}
        for chave, item in valor.items():
            if chave in _CAMPOS_SENHA_PORTEIRO:
                mudou = True
                continue
            novo, interno = _tirar_senhas(item)
            saida[chave] = novo
            mudou = mudou or interno
        return saida, mudou
    if isinstance(valor, list):
        mudou = False
        saida = []
        for item in valor:
            novo, interno = _tirar_senhas(item)
            saida.append(novo)
            mudou = mudou or interno
        return saida, mudou
    return valor, False


def ignorar_senhas_do_porteiro(root: Path) -> bool:
    """Tira hash de senha do controle_acesso.json. O resto da fila fica."""
    path = root / "n8n" / "storage" / "Porteiro" / "controle_acesso.json"
    if not path.is_file() or path.is_dir():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    limpo, mudou = _tirar_senhas(data)
    if not mudou:
        return False
    from scout.core.gravar_arquivo import gravar_bytes

    gravar_bytes(path, json.dumps(limpo, ensure_ascii=False, indent=2).encode("utf-8"))
    return True


def load_users(root: Path) -> list[dict]:
    migrar_legado(root)
    ignorar_senhas_do_porteiro(root)
    path = users_path(root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    usuarios = data.get("users") if isinstance(data, dict) else None
    if not isinstance(usuarios, list):
        return []
    saida = []
    mudou = False
    for item in usuarios:
        if not isinstance(item, dict):
            continue
        limpo, tirou = _sem_senha(item)
        saida.append(limpo)
        mudou = mudou or tirou
    if mudou:
        _gravar(root, saida)
    return saida


def _gravar(root: Path, usuarios: list[dict]) -> None:
    path = users_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"users": usuarios}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _achar(usuarios: list[dict], username: str) -> dict | None:
    for usuario in usuarios:
        stored = usuario.get("username")
        if isinstance(stored, str) and _same_text(username, stored):
            return usuario
    return None


def list_users(root: Path) -> list[dict]:
    saida = []
    for usuario in load_users(root):
        saida.append(
            {
                "username": usuario.get("username"),
                "ver": list(usuario.get("ver") or []),
                "operar": list(usuario.get("operar") or []),
                "abrir": list(usuario.get("abrir") or []),
                "status": usuario.get("status") or "ativo",
            }
        )
    return saida


def create_user(
    root: Path,
    username: str,
    *,
    ver: list | None = None,
    operar: list | None = None,
    abrir: list | None = None,
) -> None:
    username = _check_username(username)
    conhecidos = service_ids()
    usuarios = load_users(root)
    if _achar(usuarios, username) is not None:
        raise AuthError("Já existe uma conta com esse usuário.")
    usuarios.append(
        {
            "username": username,
            "ver": _listas(ver, conhecidos),
            "operar": _listas(operar, conhecidos),
            "abrir": _listas(abrir, conhecidos),
            "status": "ativo",
        }
    )
    _gravar(root, usuarios)
    from control_plane.sessao_host import ao_criar

    ao_criar(root, username)


def update_lists(root: Path, username: str, *, ver: list, operar: list, abrir: list) -> None:
    """Grava o modelo do próximo token. O token já emitido não muda."""
    username = _check_username(username)
    conhecidos = service_ids()
    usuarios = load_users(root)
    usuario = _achar(usuarios, username)
    if usuario is None:
        raise AuthError("Conta não encontrada.")
    usuario["ver"] = _listas(ver, conhecidos)
    usuario["operar"] = _listas(operar, conhecidos)
    usuario["abrir"] = _listas(abrir, conhecidos)
    _gravar(root, usuarios)


def desativar_conta(root: Path, username: str, *, ip: str = "") -> None:
    username = _check_username(username)
    usuarios = load_users(root)
    usuario = _achar(usuarios, username)
    if usuario is None:
        raise AuthError("Conta não encontrada.")
    usuario["status"] = "inativo"
    _gravar(root, usuarios)
    from control_plane.user_token import revogar_conta

    revogar_conta(root, username, por="admin", ip=ip)


def _listas_do_claims(claims: dict, nome: str, conhecidos: tuple[str, ...]) -> list[str]:
    lista = claims.get(nome)
    if not isinstance(lista, list):
        return []
    saida = []
    for item in lista:
        if isinstance(item, str) and item in conhecidos and item not in saida:
            saida.append(item)
    return saida


def _aud_claim(token: str) -> str:
    texto = normalizar_token(token)
    partes = texto.split(".")
    if len(partes) != 3 or not partes[1]:
        return ""
    resto = (-len(partes[1])) % 4
    try:
        import base64

        bruto = base64.urlsafe_b64decode(partes[1] + ("=" * resto))
        claims = json.loads(bruto.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return ""
    aud = claims.get("aud") if isinstance(claims, dict) else ""
    return aud if isinstance(aud, str) else ""


def sessao_do_token(root: Path, token: str, *, ip: str, modo: str, passo: str = "auth.login") -> dict:
    """Confere assinatura, prazo, jti e a chave atual. Não grava sucesso."""
    from control_plane.trilha_auth import proximo_passo
    from control_plane.user_token import conferir, recusar

    claims = conferir(root, token, ip=ip, passo=passo)
    username = str(claims.get("sub") or "")
    try:
        username = _check_username(username)
    except AuthError:
        recusar(root, ip=ip, conta="", motivo="conta", passo=passo)
        raise AuthError(proximo_passo("conta"), motivo="conta") from None
    usuario = _achar(load_users(root), username)
    if usuario is None or usuario.get("status") != "ativo":
        recusar(root, ip=ip, conta=username, motivo="conta", passo=passo)
        raise AuthError(proximo_passo("conta"), motivo="conta")
    aguardando = False
    if modo == "edge":
        from control_plane.binding import vinculo_ativo

        aguardando = not vinculo_ativo(root, ip, username)
    conhecidos = service_ids()
    from control_plane.sessao_host import ao_criar

    ao_criar(root, username)
    return {
        "username": username,
        "admin": False,
        "must_change": False,
        "aguardando": aguardando,
        "ver": [] if aguardando else _listas_do_claims(claims, "ver", conhecidos),
        "operar": [] if aguardando else _listas_do_claims(claims, "operar", conhecidos),
        "abrir": [] if aguardando else _listas_do_claims(claims, "abrir", conhecidos),
        "ip": ip,
        "jti": str(claims.get("jti") or ""),
        "exp": int(claims.get("exp") or 0),
    }


def entrar_com_token(root: Path, token: str, *, ip: str, modo: str) -> dict:
    if modo == "edge" and _aud_claim(token) == "n8groker-console":
        from control_plane.trilha_auth import proximo_passo
        from control_plane.user_token import recusar

        recusar(root, ip=ip, conta="admin", motivo="tela", passo="auth.login")
        raise AuthError(proximo_passo("tela"), motivo="tela")
    sessao = sessao_do_token(root, token, ip=ip, modo=modo, passo="auth.login")
    from control_plane.trilha_auth import anotar_auth

    anotar_auth(
        root,
        ip=ip,
        conta=sessao["username"],
        passo="auth.login",
        resultado="ok",
        motivo="ok",
    )
    auditar(root, sessao["username"], ip, "login", sessao["username"], "ok")
    if sessao["aguardando"]:
        auditar(root, sessao["username"], ip, "aguardando", sessao["username"], "ok")
    return sessao


def reset_account(root: Path) -> bool:
    """Não apaga users.json nem audit.jsonl e não cria senha de admin."""
    return migrar_legado(root)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--reset" not in args:
        print(
            "O admin da máquina é um JWT, não uma senha. "
            "Este comando não apaga usuários nem a auditoria. "
            "Para afastar um auth.json antigo: python -m control_plane.auth --reset",
            file=sys.stderr,
        )
        return 2
    root = Path.cwd()
    if "--root" in args:
        index = args.index("--root")
        try:
            root = Path(args[index + 1])
        except IndexError:
            print("Informe a pasta depois de --root.", file=sys.stderr)
            return 2
    moved = reset_account(root)
    if moved:
        print("auth.json antigo foi renomeado e não autentica mais. Usuários e auditoria permanecem.")
    else:
        print("Nada para afastar. Usuários e auditoria não foram apagados. Nenhuma senha de admin foi criada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
