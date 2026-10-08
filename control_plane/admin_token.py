"""JWT Ed25519 de admin da máquina. A chave privada não entra no painel nem no .env.

Emissão: `python -m control_plane.admin_token`
Primeira chave: `python -m control_plane.admin_token --init`
Troca: `python -m control_plane.admin_token --rotate` chama o Keeper.
A pública anterior fica no disco só para o motivo rotacionado. Não abre sessão.
O comando imprime o token uma vez e não grava arquivo de token.
"""

from __future__ import annotations

import base64
import json
import secrets
import sys
import time
from pathlib import Path

_SCOUT = Path(__file__).resolve().parents[1] / "Scout_OSINT_Docker"
if str(_SCOUT) not in sys.path:
    sys.path.insert(0, str(_SCOUT))

from scout.core.gravar_arquivo import gravar_bytes  # noqa: E402

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from control_plane.audit import auditar
from control_plane.auth import AuthError, normalizar_token
from control_plane.trilha_auth import anotar_auth, proximo_passo

ISS = "n8groker-host"
AUD = "n8groker-console"
SUB = "admin"
TTL = 300
MAX_LIFE = 600
LEEWAY = 30
MENSAGEM_ROTATE = (
    "A chave de admin girou. O token anterior não entra. "
    "A sessão admin já aberta cai na próxima atualização."
)
MENSAGEM_SEMENTE = (
    "admin.key não é uma semente Ed25519 de 32 bytes. "
    "Apague .n8groker/admin.key e .n8groker/admin.pub e rode "
    "python -m control_plane.admin_token --init"
)
MENSAGEM_REGISTRO_ADMIN = (
    "O arquivo admin-jti.json não pôde ser lido. A sessão já aberta continua. "
    "Para entrar de novo, no shell: python -m control_plane.admin_token --reparar-jti "
    "e depois python -m control_plane.admin_token para emitir outro token."
)


def raiz_padrao() -> Path:
    """A mesma pasta do painel. Não é o diretório em que o comando foi chamado."""
    return Path(__file__).resolve().parent.parent


def _pasta(root: Path | str, nome: str) -> Path:
    base_root = Path(root)
    path = (base_root / ".n8groker" / nome).resolve()
    base = (base_root.resolve() / ".n8groker").resolve()
    if path.parent != base:
        raise AuthError("Arquivo de chave fora da pasta local.")
    return path


def key_path(root: Path) -> Path:
    return _pasta(root, "admin.key")


def pub_path(root: Path) -> Path:
    return _pasta(root, "admin.pub")


def _prev_pub(root: Path) -> Path:
    return _pasta(root, "admin-prev.pub")


def _prev_meta(root: Path) -> Path:
    return _pasta(root, "admin-prev.json")


def _jti_path(root: Path) -> Path:
    return _pasta(root, "admin-jti.json")


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


def _gravar_raw(path: Path, data: bytes) -> None:
    gravar_bytes(path, data)


def _gerar_par() -> tuple[bytes, bytes]:
    chave = Ed25519PrivateKey.generate()
    privada = chave.private_bytes_raw()
    publica = chave.public_key().public_bytes_raw()
    return privada, publica


def _semente_ok(privada: bytes) -> bool:
    if not isinstance(privada, (bytes, bytearray)) or len(privada) != 32:
        return False
    try:
        Ed25519PrivateKey.from_private_bytes(bytes(privada))
    except Exception:
        return False
    return True


def init_keys(root: Path) -> None:
    path = key_path(root)
    if path.is_file():
        try:
            bruta = path.read_bytes()
        except OSError as exc:
            raise AuthError(MENSAGEM_SEMENTE) from exc
        if not _semente_ok(bruta):
            raise AuthError(MENSAGEM_SEMENTE)
        raise AuthError("A chave de admin já existe. Para trocar, use --rotate.")
    privada, publica = _gerar_par()
    _gravar_raw(key_path(root), privada)
    _gravar_raw(pub_path(root), publica)


def _geracao_path(root: Path) -> Path:
    return _pasta(root, "admin-geracao")


_TETO_GERACAO = 1_000_000_000


def _teto_path(root: Path) -> Path:
    """Maior geração já entregue a uma sessão. Não é o arquivo que o HUD lê."""
    return _pasta(root, "admin-geracao-max")


def _ler_numero(path: Path, *, ausente: int | None) -> int | None:
    if not path.exists():
        return ausente
    try:
        texto = path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    if not texto:
        return ausente
    if not texto.isdigit():
        return None
    numero = int(texto)
    if numero > _TETO_GERACAO:
        return None
    return numero


def ler_geracao(root: Path) -> int | None:
    """Número em admin-geracao. Ausente é 0. Ilegível é None e não fecha a sessão."""
    return _ler_numero(_geracao_path(root), ausente=0)


def _ler_teto(root: Path) -> int | None:
    return _ler_numero(_teto_path(root), ausente=0)


def _lembrar_teto(root: Path, numero: int) -> None:
    """Guarda o maior número já entregue. Não rebaixa um teto ilegível."""
    if numero < 0 or numero > _TETO_GERACAO:
        return
    path = _teto_path(root)
    atual = _ler_numero(path, ausente=None)
    if isinstance(atual, int) and atual >= numero:
        return
    if atual is None and path.exists():
        return
    _gravar_raw(path, f"{numero}\n".encode("ascii"))


def subir_geracao(root: Path) -> int:
    """Próximo número legível, acima de qualquer geração já entregue.

    Arquivo ausente ou ilegível não volta para 1: usa admin-geracao-max.
    Acima do teto a função recusa, sem gravar um número que a leitura trataria
    como ilegível.
    """
    atual = ler_geracao(root)
    teto = _ler_teto(root)
    base = atual if isinstance(atual, int) else 0
    if isinstance(teto, int):
        base = max(base, teto)
    elif teto is None and atual is None:
        base = _TETO_GERACAO - 1
    novo = base + 1
    if novo > _TETO_GERACAO:
        raise AuthError(
            "O contador admin-geracao chegou ao teto. A chave não foi trocada.",
            motivo="chave",
        )
    payload = f"{novo}\n".encode("ascii")
    _gravar_raw(_geracao_path(root), payload)
    try:
        _gravar_raw(_teto_path(root), payload)
    except OSError:
        pass
    return novo


def numero_para_sessao(root: Path) -> int:
    """Na entrada, arquivo ilegível não tranca: a sessão guarda 0."""
    valor = ler_geracao(root)
    if valor is None:
        return 0
    try:
        _lembrar_teto(root, valor)
    except OSError:
        pass
    return valor


def geracao_confere(estado: dict, root: Path) -> bool:
    """False só quando o número da sessão é menor que o do disco."""
    disco = ler_geracao(root)
    if disco is None:
        return True
    guardado = estado.get("cp_admin_geracao")
    if guardado is None:
        if disco == 0:
            estado["cp_admin_geracao"] = 0
            return True
        return False
    try:
        numero = int(guardado)
    except (TypeError, ValueError):
        return disco == 0
    return numero >= disco


def _ler_bruto(path: Path) -> bytes | None:
    try:
        if path.is_file():
            return path.read_bytes()
    except OSError:
        return None
    return None


def _restaurar_bruto(path: Path, bruto: bytes | None) -> None:
    if bruto is None:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
        return
    _gravar_raw(path, bruto)


def _registrar_giro(root: Path, ip: str) -> None:
    """A trilha não desfaz um giro que já gravou a chave."""
    try:
        auditar(root, "admin", ip or "127.0.0.1", "rotacionar", "admin.key", "ok")
    except Exception:
        pass
    try:
        anotar_auth(
            root,
            ip=ip or "127.0.0.1",
            conta="admin",
            passo="auth.rotacionar",
            resultado="ok",
            motivo="ok",
        )
    except Exception:
        pass


def rotate_keys(root: Path, *, ip: str = "127.0.0.1") -> None:
    if not key_path(root).is_file() or not pub_path(root).is_file():
        raise AuthError("Não há chave para trocar. Rode --init primeiro.", motivo="chave")
    try:
        atual = pub_path(root).read_bytes()
        privada_antes = key_path(root).read_bytes()
    except OSError as exc:
        raise AuthError("Não há chave para trocar. Rode --init primeiro.", motivo="chave") from exc
    geracao_antes = _ler_bruto(_geracao_path(root))
    teto_antes = _ler_bruto(_teto_path(root))
    try:
        subir_geracao(root)
    except AuthError:
        raise
    except OSError as exc:
        raise AuthError(
            "O contador admin-geracao não pôde ser gravado. A chave não foi trocada.",
            motivo="chave",
        ) from exc
    try:
        _gravar_raw(_prev_pub(root), atual)
        meta = _prev_meta(root)
        try:
            if meta.is_file():
                meta.unlink()
        except OSError:
            pass
        privada, publica = _gerar_par()
        _gravar_raw(key_path(root), privada)
        _gravar_raw(pub_path(root), publica)
    except OSError as exc:
        try:
            publica_agora = pub_path(root).read_bytes()
        except OSError:
            publica_agora = b""
        if publica_agora == atual:
            try:
                if key_path(root).read_bytes() != privada_antes:
                    _gravar_raw(key_path(root), privada_antes)
            except OSError:
                pass
            try:
                _restaurar_bruto(_geracao_path(root), geracao_antes)
                _restaurar_bruto(_teto_path(root), teto_antes)
            except OSError:
                pass
            raise AuthError(
                "O contador admin-geracao não pôde ser gravado. A chave não foi trocada.",
                motivo="chave",
            ) from exc
        _registrar_giro(root, ip)
        return
    _registrar_giro(root, ip)


def _assinar(privada: bytes, claims: dict) -> str:
    cabecalho = _b64(json.dumps({"alg": "EdDSA", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    corpo = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    assinatura = Ed25519PrivateKey.from_private_bytes(privada).sign(f"{cabecalho}.{corpo}".encode("ascii"))
    return f"{cabecalho}.{corpo}.{_b64(assinatura)}"


def _alinhar_pub(root: Path, privada: bytes) -> str:
    """A pública que o painel lê tem de ser a desta privada. Devolve um aviso ou ''."""
    try:
        derivada = Ed25519PrivateKey.from_private_bytes(privada).public_key().public_bytes_raw()
    except Exception as exc:
        raise AuthError(MENSAGEM_SEMENTE) from exc
    path = pub_path(root)
    try:
        atual = path.read_bytes() if path.is_file() else b""
    except OSError:
        atual = b""
    if atual == derivada:
        return ""
    _gravar_raw(path, derivada)
    if not atual:
        return "admin.pub não existia e foi gravada a partir de admin.key."
    return "admin.pub não conferia com admin.key e foi regravada. A pública antiga não continua valendo."


def issue(root: Path) -> str:
    path = key_path(root)
    if not path.is_file():
        raise AuthError("Não há admin.key. Rode python -m control_plane.admin_token --init na raiz do projeto.")
    privada = path.read_bytes()
    aviso = _alinhar_pub(root, privada)
    if aviso:
        print(aviso, file=sys.stderr)
    agora = _agora_emissao(root)
    claims = {
        "iss": ISS,
        "aud": AUD,
        "sub": SUB,
        "iat": agora,
        "nbf": agora,
        "exp": agora + TTL,
        "jti": secrets.token_hex(16),
    }
    return _assinar(privada, claims)


def _normalizar(token: str) -> str:
    return normalizar_token(token)


def _recusa(root: Path, *, ip: str, motivo: str, passo: str = "auth.login", arquivo: str = "") -> None:
    """Recusa de admin em auth.jsonl. O webhook e a janela de 20 não leem esse arquivo."""
    endereco = ip or "127.0.0.1"
    try:
        auditar(root, "admin", endereco, "login", motivo, "recusado")
    except ValueError:
        pass
    anotar_auth(
        root,
        ip=endereco,
        conta="admin",
        passo=passo,
        resultado="recusado",
        motivo=motivo,
        arquivo=arquivo,
    )


def _falha(root: Path, *, ip: str, motivo: str, passo: str = "auth.login") -> None:
    _recusa(root, ip=ip, motivo=motivo, passo=passo)
    raise AuthError(proximo_passo(motivo), motivo=motivo)


def _assinatura_na_anterior(root: Path, mensagem: bytes, assinatura: bytes) -> bool:
    """A pública anterior só classifica rotacionado. Ela não abre sessão."""
    try:
        anterior = _prev_pub(root).read_bytes()
    except OSError:
        return False
    if len(anterior) != 32:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(anterior).verify(assinatura, mensagem)
    except (InvalidSignature, ValueError):
        return False
    return True


def _publicas(root: Path, agora: float) -> list[bytes]:
    """Só a pública atual abre sessão. A anterior não entra, nem dentro de 300 s."""
    _ = agora
    try:
        atual = pub_path(root).read_bytes()
    except OSError:
        return []
    if len(atual) == 32:
        return [atual]
    return []


def conferir(root: Path, token: str, *, agora: float | None = None, ip: str = "") -> dict:
    texto = _normalizar(token)
    if not texto:
        _falha(root, ip=ip, motivo="vazio")
    if texto.count(".") != 2:
        _falha(root, ip=ip, motivo="formato")
    cabecalho_b64, corpo_b64, assinatura_b64 = texto.split(".")
    try:
        cabecalho = json.loads(_unb64(cabecalho_b64))
        claims = json.loads(_unb64(corpo_b64))
        assinatura = _unb64(assinatura_b64)
    except (AuthError, json.JSONDecodeError, UnicodeDecodeError):
        _falha(root, ip=ip, motivo="formato")
    if not isinstance(cabecalho, dict) or cabecalho.get("alg") != "EdDSA" or not isinstance(claims, dict):
        _falha(root, ip=ip, motivo="formato")
    mensagem = f"{cabecalho_b64}.{corpo_b64}".encode("ascii")
    relogio = time.time() if agora is None else agora
    publicas = _publicas(root, relogio)
    if not publicas:
        _falha(root, ip=ip, motivo="chave")
    valido = False
    for publica in publicas:
        try:
            Ed25519PublicKey.from_public_bytes(publica).verify(assinatura, mensagem)
            valido = True
            break
        except (InvalidSignature, ValueError):
            continue
    if not valido:
        if _assinatura_na_anterior(root, mensagem, assinatura):
            _falha(root, ip=ip, motivo="rotacionado")
        _falha(root, ip=ip, motivo="assinatura")
    try:
        iat = int(claims["iat"])
        nbf = int(claims["nbf"])
        exp = int(claims["exp"])
    except (KeyError, TypeError, ValueError):
        _falha(root, ip=ip, motivo="formato")
    if exp - iat > MAX_LIFE or claims.get("iss") != ISS or claims.get("aud") != AUD or claims.get("sub") != SUB:
        _falha(root, ip=ip, motivo="assinatura")
    jti = claims.get("jti")
    if not isinstance(jti, str) or len(jti) != 32:
        _falha(root, ip=ip, motivo="formato")
    if relogio + LEEWAY < nbf or relogio + LEEWAY < iat:
        _falha(root, ip=ip, motivo="relogio")
    if relogio > exp + LEEWAY:
        _falha(root, ip=ip, motivo="prazo")
    return claims


def _numero(valor) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        try:
            return float(valor)
        except ValueError:
            return None
    return None


def _item_admin_valido(item) -> bool:
    if not isinstance(item, dict):
        return False
    jti = item.get("jti")
    if not isinstance(jti, str) or not jti:
        return False
    return _numero(item.get("exp")) is not None


def _lista_admin_valida(data) -> bool:
    """Lista de tokens já usados. Vazia é válida: nenhum jti foi consumido.

    Item que não é objeto, sem jti string ou sem exp numérico invalida o arquivo.
    """
    if not isinstance(data, dict):
        return False
    itens = data.get("items")
    if not isinstance(itens, list) or any(not _item_admin_valido(item) for item in itens):
        return False
    if "recusar_emitidos_antes" in data and _numero(data.get("recusar_emitidos_antes")) is None:
        return False
    return True


def _ler_registro(root: Path) -> tuple[list[dict], float | None]:
    """(itens, corte). Ausente devolve lista vazia. Estrutura inválida é registro."""
    path = _jti_path(root)
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        raise AuthError(MENSAGEM_REGISTRO_ADMIN, motivo="registro") from None
    if not _lista_admin_valida(data):
        raise AuthError(MENSAGEM_REGISTRO_ADMIN, motivo="registro")
    corte = None
    if "recusar_emitidos_antes" in data:
        corte = _numero(data.get("recusar_emitidos_antes"))
    return list(data["items"]), corte


def _agora_emissao(root: Path) -> int:
    """Depois do reparo, o token novo nasce depois do corte. O antigo fica revogado."""
    agora = int(time.time())
    try:
        _itens, corte = _ler_registro(root)
    except AuthError:
        return agora
    if corte is not None and agora <= int(corte):
        return int(corte) + 1
    return agora


def reparar_jti(root: Path) -> str:
    """Tira do caminho um admin-jti.json ilegível ou com item inválido.

    {"items":[]} é lista válida de usados e não é apagado. O conteúdo quebrado
    vai para admin-jti.json.quebrado. O arquivo novo traz recusar_emitidos_antes:
    JWT com iat até esse instante recusa como revogado. A sessão já aberta não
    lê o arquivo. Emita outro token depois. --init não faz este reparo.
    """
    path = _jti_path(root)
    if not path.exists():
        return "admin-jti.json não existe. O próximo token pode entrar."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        data = None
    if data is not None and _lista_admin_valida(data):
        return "admin-jti.json está legível. Nada foi apagado."
    destino = path.with_name("admin-jti.json.quebrado")
    if destino.exists():
        destino = path.with_name(f"admin-jti.json.quebrado.{int(time.time())}")
    path.replace(destino)
    corte = time.time()
    _gravar_raw(
        path,
        json.dumps({"items": [], "recusar_emitidos_antes": corte}).encode("utf-8"),
    )
    return (
        f"{path.name} quebrado foi para {destino.name}. "
        "Tokens de admin emitidos até este reparo não entram. "
        "A sessão já aberta continua. Emita outro token."
    )


def _jti_usados(root: Path, agora: float) -> tuple[list[dict], float | None]:
    itens, corte = _ler_registro(root)
    vivos = []
    for item in itens:
        exp = _numero(item.get("exp"))
        jti = item.get("jti")
        if exp is not None and exp >= agora and isinstance(jti, str):
            vivos.append({"jti": jti, "exp": exp})
    return vivos, corte


def _gravar_usados(root: Path, usados: list[dict], corte: float | None) -> None:
    corpo: dict = {"items": usados}
    if corte is not None:
        corpo["recusar_emitidos_antes"] = corte
    _gravar_raw(_jti_path(root), json.dumps(corpo).encode("utf-8"))


def _consumir(root: Path, jti: str, exp: float) -> None:
    agora = time.time()
    usados, corte = _jti_usados(root, agora)
    if any(item["jti"] == jti for item in usados):
        raise AuthError("Token já utilizado.", motivo="repetido")
    usados.append({"jti": jti, "exp": exp})
    _gravar_usados(root, usados, corte)


def _marca_chave(root: Path, token: str) -> str:
    texto = _normalizar(token)
    partes = texto.split(".")
    if len(partes) != 3:
        return "atual"
    try:
        assinatura = _unb64(partes[2])
    except AuthError:
        return "atual"
    mensagem = f"{partes[0]}.{partes[1]}".encode("ascii")
    try:
        atual = pub_path(root).read_bytes()
    except OSError:
        atual = b""
    if len(atual) == 32:
        try:
            Ed25519PublicKey.from_public_bytes(atual).verify(assinatura, mensagem)
            return "atual"
        except (InvalidSignature, ValueError):
            pass
    if _assinatura_na_anterior(root, mensagem, assinatura):
        return "anterior"
    return "atual"


def aceitar(root: Path, token: str, *, modo: str, ip: str = "") -> dict:
    if modo != "console":
        _recusa(root, ip=ip, motivo="tela")
        raise AuthError(proximo_passo("tela"), motivo="tela")
    claims = conferir(root, token, ip=ip)
    try:
        _itens, corte = _ler_registro(root)
    except AuthError as exc:
        if exc.motivo == "registro":
            _recusa(root, ip=ip, motivo="registro", arquivo="admin-jti.json")
            raise AuthError(exc.message, motivo="registro") from None
        raise
    if corte is not None and int(claims["iat"]) <= int(corte):
        _recusa(root, ip=ip, motivo="revogado")
        raise AuthError(proximo_passo("revogado"), motivo="revogado")
    try:
        _consumir(root, str(claims["jti"]), float(claims["exp"]))
    except AuthError as exc:
        if exc.motivo == "registro":
            _recusa(root, ip=ip, motivo="registro", arquivo="admin-jti.json")
            raise AuthError(exc.message, motivo="registro") from None
        _recusa(root, ip=ip, motivo="repetido")
        raise AuthError(proximo_passo("repetido"), motivo="repetido") from None
    anotar_auth(
        root,
        ip=ip or "127.0.0.1",
        conta="admin",
        passo="auth.login",
        resultado="ok",
        motivo="ok",
        chave=_marca_chave(root, token),
    )
    auditar(root, "admin", ip, "sessão admin aberta", "", "ok")
    return claims


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = raiz_padrao()
    if "--root" in args:
        index = args.index("--root")
        try:
            root = Path(args[index + 1])
        except IndexError:
            print("Informe a pasta depois de --root.", file=sys.stderr)
            return 2
    try:
        if "--init" in args:
            init_keys(root)
            token = issue(root)
            print(token)
            print(
                "Chave de admin criada em .n8groker/admin.key. Ela não vai para o .env.",
                file=sys.stderr,
            )
            print(
                "Vale 5 minutos. Cole só essa linha no campo Token de admin, com o console já aberto.",
                file=sys.stderr,
            )
            return 0
        if "--reparar-jti" in args:
            print(reparar_jti(root))
            return 0
        if "--rotate" in args:
            from control_plane.keeper_cliente import BANNER_KEEPER_FORA, chamar

            resposta = chamar(root, "rotacionar", {"alvo": "admin"})
            if resposta.get("fora"):
                print(BANNER_KEEPER_FORA)
                print(BANNER_KEEPER_FORA, file=sys.stderr)
                return 2
            if not resposta.get("ok"):
                texto = proximo_passo(str(resposta.get("motivo") or "chave"))
                print(texto)
                print(texto, file=sys.stderr)
                return 2
            print(MENSAGEM_ROTATE)
            return 0
        token = issue(root)
    except AuthError as exc:
        print(exc.message)
        print(exc.message, file=sys.stderr)
        return 2
    print(token)
    print(
        "Vale 5 minutos. Cole só essa linha no campo Token de admin, com o console já aberto.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
