"""JWT Ed25519 de usuário. A chave não é a do admin e não entra no git.

Emissão: a aba Admin do console, com a sessão do JWT de admin já aberta.
O token volta uma vez para copiar. No disco ficam o jti e a validade, nunca o JWT.

Boot: `garantir` cria `.n8groker/usuario.key` se o arquivo não existir.
Rotação troca a chave na hora: o token antigo fica com motivo rotacionado
e não abre sessão. A geração da sessão sobe, então o cookie cai no pedido
seguinte. A pública anterior só serve para classificar esse motivo.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

_SCOUT = Path(__file__).resolve().parents[1] / "Scout_OSINT_Docker"
if str(_SCOUT) not in sys.path:
    sys.path.insert(0, str(_SCOUT))

from scout.core.gravar_arquivo import substituir_bytes  # noqa: E402

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from control_plane.audit import auditar
from control_plane.auth import AuthError, normalizar_token
from control_plane.trilha_auth import anotar_auth, proximo_passo

ISS = "n8groker-host"
AUD = "n8groker-user"
LEEWAY = 30
HORAS_PADRAO = 8
HORAS_MAX = 168
HORAS_MIN = 0.25
_MSG_REGISTRO = (
    "O arquivo usuario-jti.json não pôde ser lido. "
    "O acesso fica recusado até esse arquivo voltar a ser legível."
)


def _pasta(root: Path | str, nome: str) -> Path:
    # O boot do Windows chama garantir(sys.argv[1]): root chega como str.
    base_root = Path(root)
    path = (base_root / ".n8groker" / nome).resolve()
    base = (base_root.resolve() / ".n8groker").resolve()
    if path.parent != base:
        raise AuthError("Arquivo de chave fora da pasta local.")
    return path


def key_path(root: Path) -> Path:
    return _pasta(root, "usuario.key")


def pub_path(root: Path) -> Path:
    return _pasta(root, "usuario.pub")


def jti_path(root: Path) -> Path:
    return _pasta(root, "usuario-jti.json")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    if not isinstance(text, str) or not text:
        raise AuthError("Token recusado.")
    resto = (-len(text)) % 4
    try:
        return base64.urlsafe_b64decode(text + ("=" * resto))
    except Exception as exc:
        raise AuthError("Token recusado.") from exc


def ttl_segundos() -> int:
    bruto = os.environ.get("N8GROKER_TOKEN_USUARIO_HORAS", str(HORAS_PADRAO))
    try:
        horas = float(str(bruto).strip())
    except (TypeError, ValueError):
        horas = float(HORAS_PADRAO)
    if horas <= 0:
        horas = float(HORAS_PADRAO)
    if horas < HORAS_MIN:
        horas = HORAS_MIN
    if horas > HORAS_MAX:
        horas = float(HORAS_MAX)
    return int(horas * 3600)


def _kid(publica: bytes) -> str:
    return hashlib.sha256(publica).hexdigest()[:16]


def _gerar_par() -> tuple[bytes, bytes]:
    chave = Ed25519PrivateKey.generate()
    return chave.private_bytes_raw(), chave.public_key().public_bytes_raw()


def _pub_de(chave: Path) -> bytes | None:
    try:
        bruta = chave.read_bytes()
    except OSError:
        return None
    if len(bruta) != 32:
        return None
    try:
        return Ed25519PrivateKey.from_private_bytes(bruta).public_key().public_bytes_raw()
    except Exception:
        return None


def _concluir_par(chave: Path, publica: Path) -> None:
    """Termina uma rotação que ficou no temporário.

    Os dois arquivos `.novo` já estão completos, ou só um sobrou depois do
    replace. O par que está valendo não é truncado.
    """
    novo_k = chave.with_name(chave.name + ".novo")
    novo_p = publica.with_name(publica.name + ".novo")
    tem_k = novo_k.is_file()
    tem_p = novo_p.is_file()
    if not tem_k and not tem_p:
        return
    if tem_k and tem_p:
        derivada = _pub_de(novo_k)
        try:
            pub_nova = novo_p.read_bytes()
        except OSError:
            return
        if derivada is None or derivada != pub_nova:
            novo_k.unlink(missing_ok=True)
            novo_p.unlink(missing_ok=True)
            return
        try:
            antiga = publica.read_bytes()
        except OSError:
            antiga = b""
        if len(antiga) == 32 and antiga != pub_nova:
            # Prova da troca interrompida: a pública que saiu fica para o motivo.
            substituir_bytes(publica.with_name("usuario-prev.pub"), antiga)
        os.replace(novo_k, chave)
        os.replace(novo_p, publica)
        return
    if tem_p and not tem_k:
        try:
            pub_nova = novo_p.read_bytes()
        except OSError:
            return
        if _pub_de(chave) == pub_nova:
            os.replace(novo_p, publica)
            return
        derivada = _pub_de(chave)
        if derivada is not None and publica.is_file():
            try:
                if publica.read_bytes() == derivada:
                    novo_p.unlink(missing_ok=True)
            except OSError:
                return
        return
    derivada = _pub_de(novo_k)
    if derivada is None:
        novo_k.unlink(missing_ok=True)
        return
    try:
        atual = publica.read_bytes() if publica.is_file() else b""
    except OSError:
        return
    if atual == derivada:
        os.replace(novo_k, chave)
        return
    viva = _pub_de(chave)
    if viva is not None and atual == viva:
        novo_k.unlink(missing_ok=True)


def _recusar_pasta(path: Path, rotulo: str) -> None:
    if path.is_dir():
        raise AuthError(
            f"{rotulo} é uma pasta, não um arquivo. "
            "Esvazie e remova a pasta antes de subir o núcleo. Nada foi apagado."
        )


def garantir(root: Path | str) -> None:
    """Cria o par se faltar. Arquivo com conteúdo não é reescrito.

    Aceita Path ou str. Os 32 bytes de usuario.key são a semente Ed25519;
    se o .pub faltar, ele é derivado dessa semente e a .key não é reescrita.
    """
    chave = key_path(root)
    publica = pub_path(root)
    _recusar_pasta(chave, "usuario.key")
    _recusar_pasta(publica, "usuario.pub")
    _concluir_par(chave, publica)
    if chave.is_file() and chave.stat().st_size == 32:
        bruta = chave.read_bytes()
        derivada = Ed25519PrivateKey.from_private_bytes(bruta).public_key().public_bytes_raw()
        if publica.is_file() and publica.stat().st_size == 32:
            if publica.read_bytes() != derivada:
                raise AuthError("usuario.pub não confere com usuario.key. Não reescrevi nenhuma das duas.")
            return
        substituir_bytes(publica, derivada)
        return
    if chave.is_file() and chave.stat().st_size not in (0, 32):
        raise AuthError("usuario.key existe com tamanho inesperado. Não reescrevi.")
    privada, pub = _gerar_par()
    substituir_bytes(chave, privada)
    substituir_bytes(publica, pub)


def _registro_ilegivel() -> AuthError:
    return AuthError(_MSG_REGISTRO, motivo="registro")


def _ler_jti(root: Path) -> list[dict]:
    """Arquivo ausente é lista vazia. Arquivo presente e ilegível recusa.

    Lista vazia num arquivo corrompido apagaria a revogação: o token revogado
    voltaria a entrar. A sessão de admin não lê este arquivo.
    """
    path = jti_path(root)
    try:
        existe = path.exists()
    except OSError as exc:
        raise _registro_ilegivel() from exc
    if not existe:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise _registro_ilegivel() from exc
    itens = data.get("items") if isinstance(data, dict) else None
    if not isinstance(itens, list):
        raise _registro_ilegivel()
    saida = []
    for item in itens:
        if not isinstance(item, dict) or not isinstance(item.get("jti"), str):
            raise _registro_ilegivel()
        saida.append(item)
    return saida


def _gravar_jti(root: Path, itens: list[dict]) -> None:
    agora = time.time()
    vivos = []
    for item in itens:
        try:
            exp = float(item.get("exp"))
        except (TypeError, ValueError):
            continue
        if exp + LEEWAY >= agora:
            vivos.append(item)
    substituir_bytes(jti_path(root), json.dumps({"items": vivos}, ensure_ascii=False).encode("utf-8"))


def recusar(
    root: Path,
    *,
    ip: str,
    conta: str,
    motivo: str,
    passo: str = "auth.login",
    arquivo: str = "",
) -> None:
    """Login recusado. Vai para auth.jsonl. Não alerta e não entra na janela de 20."""
    try:
        auditar(root, conta or "-", ip or "127.0.0.1", "login", motivo, "recusado")
    except ValueError:
        pass
    anotar_auth(
        root,
        ip=ip or "127.0.0.1",
        conta=conta,
        passo=passo,
        resultado="recusado",
        motivo=motivo,
        arquivo=arquivo,
    )


def _assinar(privada: bytes, claims: dict) -> str:
    cabecalho = _b64(json.dumps({"alg": "EdDSA", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    corpo = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    assinatura = Ed25519PrivateKey.from_private_bytes(privada).sign(f"{cabecalho}.{corpo}".encode("ascii"))
    return f"{cabecalho}.{corpo}.{_b64(assinatura)}"


def emitir(
    root: Path,
    username: str,
    *,
    ver: list,
    operar: list,
    abrir: list,
    por: str,
    ip: str,
) -> str:
    from control_plane.auth import _achar, _check_username, _listas, load_users, service_ids

    username = _check_username(username)
    usuario = _achar(load_users(root), username)
    if usuario is None or usuario.get("status") != "ativo":
        raise AuthError("Conta não encontrada.")
    conhecidos = service_ids()
    ver = _listas(ver, conhecidos)
    operar = _listas(operar, conhecidos)
    abrir = _listas(abrir, conhecidos)
    garantir(root)
    try:
        itens_previos = _ler_jti(root)
    except AuthError as exc:
        anotar_auth(
            root,
            ip=ip,
            conta=username,
            passo="auth.emitir",
            resultado="recusado",
            motivo=exc.motivo or "registro",
        )
        raise
    agora = int(time.time())
    exp = agora + ttl_segundos()
    publica = pub_path(root).read_bytes()
    jti = secrets.token_hex(16)
    claims = {
        "iss": ISS,
        "aud": AUD,
        "sub": username,
        "ver": ver,
        "operar": operar,
        "abrir": abrir,
        "iat": agora,
        "nbf": agora,
        "exp": exp,
        "jti": jti,
        "kid": _kid(publica),
    }
    token = _assinar(key_path(root).read_bytes(), claims)
    itens = itens_previos
    itens.append(
        {
            "jti": jti,
            "exp": exp,
            "conta": username,
            "por": por,
            "emitido": agora,
            "revogado": False,
        }
    )
    _gravar_jti(root, itens)
    from control_plane.sessao_host import ao_criar

    ao_criar(root, username)
    try:
        auditar(root, por or "admin", ip or "127.0.0.1", "emitir", f"{username} {jti}", "ok")
    except ValueError:
        pass
    anotar_auth(
        root,
        ip=ip,
        conta=username,
        passo="auth.emitir",
        resultado="ok",
        motivo="ok",
    )
    return token


def listar(root: Path, username: str = "") -> list[dict]:
    agora = time.time()
    saida = []
    for item in _ler_jti(root):
        if username and item.get("conta") != username:
            continue
        try:
            exp = float(item.get("exp"))
        except (TypeError, ValueError):
            continue
        if exp + LEEWAY < agora:
            continue
        saida.append(
            {
                "jti": item.get("jti"),
                "exp": int(exp),
                "conta": item.get("conta") or "",
                "por": item.get("por") or "",
                "emitido": int(item.get("emitido") or 0),
                "revogado": bool(item.get("revogado")),
            }
        )
    return saida


def _marcar_revogado(root: Path, jti: str) -> dict | None:
    itens = _ler_jti(root)
    achado = None
    for item in itens:
        if item.get("jti") == jti:
            item["revogado"] = True
            achado = item
    if achado is None:
        return None
    _gravar_jti(root, itens)
    return achado


def _derrubar_conta(root: Path, username: str) -> None:
    from control_plane.sessao_host import ao_mudar, preparar
    from scout.core.sessao_cookie import caminho_geracao, ler_geracao

    preparar(root)
    item = ler_geracao(caminho_geracao(root), username)
    ativo = True if item is None else bool(item["ativo"])
    ao_mudar(root, username, ativo=ativo)


def revogar_jti(root: Path, jti: str, *, por: str, ip: str) -> None:
    if not isinstance(jti, str) or len(jti) != 32:
        raise AuthError("Identificador recusado.")
    achado = _marcar_revogado(root, jti)
    if achado is None:
        raise AuthError("Identificador não encontrado.")
    conta = str(achado.get("conta") or "")
    if conta:
        _derrubar_conta(root, conta)
    try:
        auditar(root, por or "admin", ip or "127.0.0.1", "revogar", f"{conta} {jti}", "ok")
    except ValueError:
        pass
    anotar_auth(
        root,
        ip=ip,
        conta=conta,
        passo="auth.revogar",
        resultado="ok",
        motivo="ok",
    )


def revogar_conta(root: Path, username: str, *, por: str, ip: str) -> None:
    ilegivel: AuthError | None = None
    try:
        itens = _ler_jti(root)
    except AuthError as exc:
        ilegivel = exc
        itens = None
    if itens is not None:
        mudou = False
        for item in itens:
            if item.get("conta") == username and not item.get("revogado"):
                item["revogado"] = True
                mudou = True
        if mudou:
            _gravar_jti(root, itens)
    from control_plane.sessao_host import ao_mudar

    ao_mudar(root, username, ativo=False)
    try:
        auditar(root, por or "admin", ip or "127.0.0.1", "revogar", username, "ok")
    except ValueError:
        pass
    anotar_auth(
        root,
        ip=ip,
        conta=username,
        passo="auth.revogar",
        resultado="ok",
        motivo="ok",
    )
    if ilegivel is not None:
        raise ilegivel


def _derrubar_todas(root: Path) -> None:
    from control_plane.sessao_host import preparar
    from scout.core.sessao_cookie import caminho_geracao, subir_geracao

    preparar(root)
    path = caminho_geracao(root)
    try:
        dados = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        dados = {}
    if not isinstance(dados, dict):
        return
    for nome, item in list(dados.items()):
        if not isinstance(nome, str) or nome.startswith("_") or not isinstance(item, dict):
            continue
        subir_geracao(path, nome, ativo=bool(item.get("ativo", True)))


def rotacionar(root: Path, *, por: str, ip: str) -> None:
    garantir(root)
    chave = key_path(root)
    publica_path = pub_path(root)
    try:
        atual_pub = publica_path.read_bytes()
    except OSError:
        atual_pub = b""
    if len(atual_pub) == 32:
        # Fica no disco só para o motivo rotacionado. Não abre sessão.
        substituir_bytes(_pasta(root, "usuario-prev.pub"), atual_pub)
    privada, publica = _gerar_par()
    novo_k = chave.with_name(chave.name + ".novo")
    novo_p = publica_path.with_name(publica_path.name + ".novo")
    substituir_bytes(novo_k, privada)
    try:
        substituir_bytes(novo_p, publica)
    except OSError:
        novo_k.unlink(missing_ok=True)
        raise AuthError("Não consegui gravar o par novo. O par anterior permanece.") from None
    _concluir_par(chave, publica_path)
    if _pub_de(chave) != publica:
        raise AuthError(
            "A troca da chave não terminou. Na próxima leitura o par é concluído. Nada foi truncado."
        )
    _derrubar_todas(root)
    try:
        auditar(root, por or "admin", ip or "127.0.0.1", "rotacionar", "usuario.key", "ok")
    except ValueError:
        pass
    anotar_auth(
        root,
        ip=ip,
        conta=por or "admin",
        passo="auth.rotacionar",
        resultado="ok",
        motivo="ok",
    )


def _falha(root: Path, *, ip: str, conta: str, motivo: str, passo: str = "auth.login") -> None:
    arquivo = "usuario-jti.json" if motivo == "registro" else ""
    recusar(root, ip=ip, conta=conta, motivo=motivo, passo=passo, arquivo=arquivo)
    raise AuthError(proximo_passo(motivo), motivo=motivo)


def _prev_pub(root: Path) -> bytes:
    try:
        bruto = _pasta(root, "usuario-prev.pub").read_bytes()
    except OSError:
        return b""
    return bruto if len(bruto) == 32 else b""


def _motivo_assinatura(root: Path, publica: bytes, mensagem: bytes, assinatura: bytes, claims: dict) -> str | None:
    """None quando a pública atual assina. Senão rotacionado ou assinatura."""
    try:
        Ed25519PublicKey.from_public_bytes(publica).verify(assinatura, mensagem)
    except (InvalidSignature, ValueError):
        anterior = _prev_pub(root)
        if anterior and anterior != publica:
            try:
                Ed25519PublicKey.from_public_bytes(anterior).verify(assinatura, mensagem)
                return "rotacionado"
            except (InvalidSignature, ValueError):
                pass
        return "assinatura"
    return None


def conferir(root: Path, token: str, *, agora: float | None = None, ip: str = "", passo: str = "auth.login") -> dict:
    _concluir_par(key_path(root), pub_path(root))
    texto = normalizar_token(token)
    if not texto:
        _falha(root, ip=ip, conta="", motivo="vazio", passo=passo)
    if texto.count(".") != 2:
        _falha(root, ip=ip, conta="", motivo="formato", passo=passo)
    cabecalho_b64, corpo_b64, assinatura_b64 = texto.split(".")
    try:
        cabecalho = json.loads(_unb64(cabecalho_b64))
        claims = json.loads(_unb64(corpo_b64))
        assinatura = _unb64(assinatura_b64)
    except (AuthError, json.JSONDecodeError, UnicodeDecodeError):
        _falha(root, ip=ip, conta="", motivo="formato", passo=passo)
    if not isinstance(cabecalho, dict) or cabecalho.get("alg") != "EdDSA" or not isinstance(claims, dict):
        _falha(root, ip=ip, conta="", motivo="formato", passo=passo)
    publica_path = pub_path(root)
    try:
        publica = publica_path.read_bytes()
    except OSError:
        publica = b""
    if len(publica) != 32:
        _falha(root, ip=ip, conta="", motivo="chave", passo=passo)
    mensagem = f"{cabecalho_b64}.{corpo_b64}".encode("ascii")
    motivo_assinatura = _motivo_assinatura(root, publica, mensagem, assinatura, claims)
    if motivo_assinatura:
        _falha(root, ip=ip, conta="", motivo=motivo_assinatura, passo=passo)
    relogio = time.time() if agora is None else float(agora)
    try:
        iat = int(claims["iat"])
        nbf = int(claims["nbf"])
        exp = int(claims["exp"])
    except (KeyError, TypeError, ValueError):
        _falha(root, ip=ip, conta="", motivo="formato", passo=passo)
    conta = claims.get("sub") if isinstance(claims.get("sub"), str) else ""
    if exp - iat > int(HORAS_MAX * 3600) + 120 or relogio > exp + LEEWAY:
        _falha(root, ip=ip, conta=conta, motivo="prazo", passo=passo)
    if relogio + LEEWAY < nbf or relogio + LEEWAY < iat:
        _falha(root, ip=ip, conta=conta, motivo="relogio", passo=passo)
    if claims.get("iss") != ISS or claims.get("aud") != AUD:
        _falha(root, ip=ip, conta=conta, motivo="assinatura", passo=passo)
    if claims.get("kid") != _kid(publica):
        _falha(root, ip=ip, conta=conta, motivo="assinatura", passo=passo)
    jti = claims.get("jti")
    if not isinstance(jti, str) or len(jti) != 32:
        _falha(root, ip=ip, conta=conta, motivo="formato", passo=passo)
    for nome in ("ver", "operar", "abrir"):
        lista = claims.get(nome)
        if not isinstance(lista, list) or any(not isinstance(item, str) for item in lista):
            _falha(root, ip=ip, conta=conta, motivo="formato", passo=passo)
    caminho = jti_path(root)
    try:
        legivel = caminho.is_file()
    except OSError:
        legivel = False
    if not legivel:
        _falha(root, ip=ip, conta=conta, motivo="registro", passo=passo)
    try:
        registros = _ler_jti(root)
    except AuthError:
        _falha(root, ip=ip, conta=conta, motivo="registro", passo=passo)
    achado = None
    for item in registros:
        if item.get("jti") == jti:
            achado = item
            break
    # Arquivo legível sem esta linha não prova emissão. Isso é recusa, não registro:
    # registro fica para o arquivo ausente ou ilegível, que o dono precisa repor.
    if achado is None or achado.get("revogado"):
        _falha(root, ip=ip, conta=conta, motivo="revogado", passo=passo)
    return claims
