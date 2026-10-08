"""Trilha do caminho e alertas. Não bloqueia e não grava segredo.

O arquivo ativo é `trilha.jsonl` dentro da pasta da trilha. No host isso é
`.n8groker/trilha/trilha.jsonl`. A rotação cria `trilha.jsonl.1` e seguintes
na mesma pasta. Senha, token e cookie não entram na linha.

`auth.jsonl` mora na mesma pasta. A janela dos últimos 20 resultados e o
webhook não leem esse arquivo. Passo `auth.*` da lista fixa não entra em
`trilha.jsonl`. Recusa de sessão do túnel grava `auth.sessao` no outro
arquivo, com o mesmo teto de 1 MB. Token e JWT, se caírem num campo, ficam
em 4+4. O `sid` de 16 hex é identificador e entra na linha.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

from scout.core.gravar_arquivo import gravar_bytes, renomear


def pasta_de(root: Path) -> Path:
    return Path(root) / ".n8groker" / "trilha"


def _inteiro(nome: str, padrao: int) -> int:
    try:
        valor = int(os.environ.get(nome, str(padrao)))
    except (TypeError, ValueError):
        return padrao
    return valor if valor > 0 else padrao


def _travar(fd: int) -> None:
    """Trava o byte 0. flock no Linux do container, msvcrt no Windows do host."""
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_END)
        if os.lseek(fd, 0, os.SEEK_CUR) < 1:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)


def _destravar(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def girar_jsonl(arquivo: Path) -> None:
    """Gira `nome.jsonl` com o mesmo teto e o mesmo número de cópias da trilha."""
    if not arquivo.is_file():
        return
    teto = _inteiro("N8GROKER_TRILHA_MAX_BYTES", 1048576)
    if arquivo.stat().st_size < teto:
        return
    copias = _inteiro("N8GROKER_TRILHA_COPIAS", 3)
    nome = arquivo.name
    ultimo = arquivo.with_name(f"{nome}.{copias}")
    if ultimo.is_file():
        ultimo.unlink()
    for indice in range(copias - 1, 0, -1):
        origem = arquivo.with_name(f"{nome}.{indice}")
        if origem.is_file():
            renomear(origem, arquivo.with_name(f"{nome}.{indice + 1}"))
    renomear(arquivo, arquivo.with_name(f"{nome}.1"))


def gravar_jsonl(caminho: Path, linha: dict) -> None:
    """Uma linha, um append. A trava segura o giro e a escrita juntos."""
    dados = (json.dumps(linha, ensure_ascii=False) + "\n").encode("utf-8")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    trava = caminho.with_name(caminho.name + ".lock")
    fd_trava = os.open(str(trava), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _travar(fd_trava)
        try:
            girar_jsonl(caminho)
            fd = os.open(str(caminho), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                offset = 0
                while offset < len(dados):
                    escrito = os.write(fd, dados[offset:])
                    if escrito <= 0:
                        raise OSError("append incompleto")
                    offset += escrito
            finally:
                os.close(fd)
        finally:
            _destravar(fd_trava)
    finally:
        os.close(fd_trava)


def ler_cauda_texto(caminho: Path, teto: int | None = None) -> str:
    """Lê só o fim do arquivo. Byte rasgado vira U+FFFD e a linha JSON cai fora."""
    if not caminho.is_file():
        return ""
    limite = teto if teto is not None else _inteiro("N8GROKER_AUTH_CAUDA_BYTES", 65536)
    try:
        tamanho = caminho.stat().st_size
        with caminho.open("rb") as handle:
            if tamanho > limite:
                handle.seek(max(0, tamanho - limite))
                bruto = handle.read(limite)
                quebra = bruto.find(b"\n")
                bruto = bruto[quebra + 1 :] if quebra >= 0 else b""
            else:
                bruto = handle.read()
    except OSError:
        return ""
    return bruto.decode("utf-8", errors="replace")


_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_RECORTE = re.compile(r"[A-Za-z0-9_-]{4}\u2026[A-Za-z0-9_-]{4}")
_AUTH_SESSAO = {
    "sessao revogada": "revogado",
    "sem sessao": "sem_sessao",
    "origem nao confere": "origem",
    "sessao da conta revogada": "revogado",
}
_PASSOS_AUTH = frozenset(
    {"auth.login", "auth.emitir", "auth.revogar", "auth.rotacionar", "auth.sessao"}
)


def _quatro_mais_quatro(texto: str) -> str:
    if len(texto) <= 8:
        return ""
    return texto[:4] + "\u2026" + texto[-4:]


def recortar_segredo(valor, limite: int = 80) -> str:
    """Tira token e JWT inteiros. O que resta do segredo tem no máximo 4+4."""
    texto = str(valor or "")
    if "\n" in texto or "\r" in texto or not texto:
        return ""
    if _JWT.fullmatch(texto):
        return _quatro_mais_quatro(texto)[:limite]
    if _JWT.search(texto):
        texto = _JWT.sub(lambda achado: _quatro_mais_quatro(achado.group(0)), texto)
    baixo = texto.lower()
    # Nome de conta pode ser «cookie». Só some quando o campo carrega segredo.
    if "n8groker_sessao" in baixo or (
        any(termo in baixo for termo in ("senha", "password", "cookie", "authorization", "bearer", "token"))
        and ("=" in texto or " " in texto)
    ):
        return ""
    if "eyj" in _RECORTE.sub("", texto).lower():
        return ""
    return texto[:limite]


def _limpo(valor, limite: int = 80) -> str:
    texto = str(valor or "")
    if "\n" in texto or "\r" in texto:
        return ""
    baixo = texto.lower()
    if "eyj" in baixo or "n8groker_sessao" in baixo:
        return ""
    for termo in ("senha", "password", "token", "cookie", "authorization", "bearer"):
        if termo in baixo:
            return ""
    return texto[:limite]


def sid_de(claims: dict | None) -> str:
    if not isinstance(claims, dict):
        return ""
    pronto = _limpo(claims.get("sid") or "", 32)
    if len(pronto) == 16 and all(c in "0123456789abcdef" for c in pronto):
        return pronto
    return ""


def texto_duracao(inicio: int, agora: int) -> str:
    delta = int(agora) - int(inicio)
    if delta < 0:
        delta = 0
    minutos, segundos = divmod(delta, 60)
    horas, minutos = divmod(minutos, 60)
    if horas:
        return f"{horas} h {minutos} min"
    if minutos:
        return f"{minutos} min"
    return f"{segundos} s"


def _hosts_alerta() -> set[str]:
    hosts = {"127.0.0.1", "localhost", "host.docker.internal", "n8n_app"}
    extra = os.environ.get("N8N_UPSTREAM_HOST", "").strip()
    if extra:
        hosts.add(extra)
    return hosts


def _reapontar_loopback(url: str) -> str:
    """Dentro do container, 127.0.0.1:5678 é o Scout, não o n8n."""
    if os.environ.get("SCOUT_BACKEND", "").strip() != "1":
        return url
    try:
        partes = urlparse(url)
    except ValueError:
        return url
    if partes.scheme != "http" or partes.hostname not in {"127.0.0.1", "localhost"}:
        return url
    host = os.environ.get("N8N_UPSTREAM_HOST", "").strip() or "n8n_app"
    porta = partes.port
    if not porta:
        try:
            porta = int(os.environ.get("N8N_UPSTREAM_PORT", "5678"))
        except (TypeError, ValueError):
            porta = 5678
    caminho = partes.path or "/webhook/alerta-trilha"
    consulta = ("?" + partes.query) if partes.query else ""
    return f"http://{host}:{porta}{caminho}{consulta}"


def url_do_alerta() -> str:
    url = os.environ.get("N8GROKER_ALERTA_WEBHOOK", "").strip()
    if not url and os.environ.get("SCOUT_BACKEND", "").strip() == "1":
        host = os.environ.get("N8N_UPSTREAM_HOST", "").strip() or "n8n_app"
        porta = os.environ.get("N8N_UPSTREAM_PORT", "").strip() or "5678"
        url = f"http://{host}:{porta}/webhook/alerta-trilha"
    if url:
        url = _reapontar_loopback(url)
    return url


def _webhook_local(url: str) -> bool:
    try:
        partes = urlparse(url)
    except ValueError:
        return False
    if partes.scheme != "http":
        return False
    if partes.hostname not in _hosts_alerta():
        return False
    return (partes.path or "").startswith("/webhook/")


class Trilha:
    def __init__(self, pasta, agora=None, transport=None):
        self.pasta = Path(pasta)
        self.arquivo = self.pasta / "trilha.jsonl"
        self._agora = agora or time.time
        self.transport = transport
        self._eventos: list[dict] = []
        self._sessoes: dict[str, dict] = {}
        self._apps: dict[str, list] = {}
        self._resultados: dict[str, list] = {}
        self._alertou: dict[tuple, float] = {}
        self._carregar_memoria()

    def _hora(self) -> str:
        return datetime.fromtimestamp(self._agora(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _carregar_memoria(self) -> None:
        for item in self._ler_arquivo(self.arquivo):
            self._eventos.append(item)
            self._lembrar(item)
        self._sessoes = self._ler_sessoes()

    def _lembrar(self, item: dict) -> None:
        ip = item.get("ip") or ""
        if item.get("resultado"):
            seq = self._resultados.setdefault(ip, [])
            seq.append(item["resultado"])
            del seq[:-20]
        app = item.get("app") or ""
        sid = item.get("sid") or ""
        if sid and app and item.get("resultado") == "LIBERADO":
            self._apps.setdefault(sid, []).append((int(item.get("quando") or 0), app))

    def anotar(self, *, ip, sid="", conta="", origem="", app="", passo="", resultado="", motivo="") -> dict | None:
        # Esses passos moram em auth.jsonl. Aqui entrariam na janela de 20 e no webhook.
        if str(passo or "").strip() in _PASSOS_AUTH:
            return None
        linha = {
            "quando": int(self._agora()),
            "hora": self._hora(),
            "ip": _limpo(ip, 64),
            "sid": _limpo(sid, 32),
            "conta": _limpo(conta, 64),
            "origem": _limpo(origem, 80),
            "app": _limpo(app, 32),
            "passo": _limpo(passo, 80),
            "resultado": _limpo(resultado, 32),
            "motivo": _limpo(motivo, 80),
        }
        if not linha["ip"] or not linha["resultado"]:
            return None
        if linha["resultado"] == "ok":
            for anterior in reversed(self._eventos):
                if anterior.get("ip") == linha["ip"] and anterior.get("sid") == linha["sid"]:
                    if anterior.get("passo") == linha["passo"] and anterior.get("resultado") == "ok":
                        return anterior
                    break
        self._podar()
        self._girar()
        self.pasta.mkdir(parents=True, exist_ok=True)
        with self.arquivo.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(linha, ensure_ascii=False) + "\n")
        self._eventos.append(linha)
        self._lembrar(linha)
        self._avaliar(linha)
        return linha

    def ver_sessao(self, *, sid, conta, ip, origem, app) -> None:
        identificador = _limpo(sid, 32)
        if not identificador or self.revogada(identificador):
            if identificador:
                self._sessoes.pop(identificador, None)
                self._gravar_sessoes()
            return
        agora = int(self._agora())
        atual = self._sessoes.get(identificador)
        if atual is None:
            atual = {
                "sid": identificador,
                "conta": _limpo(conta, 64),
                "ip": _limpo(ip, 64),
                "origem": _limpo(origem, 80),
                "app": _limpo(app, 32),
                "inicio": agora,
                "inicio_hora": self._hora(),
                "visto_em": agora,
            }
        else:
            atual["visto_em"] = agora
            atual["ip"] = _limpo(ip, 64) or atual["ip"]
            atual["app"] = _limpo(app, 32) or atual["app"]
            atual["origem"] = _limpo(origem, 80) or atual["origem"]
            atual["conta"] = _limpo(conta, 64) or atual["conta"]
        self._sessoes[identificador] = atual
        self._gravar_sessoes()
        self._dois_ips(atual)

    def sessoes(self) -> list[dict]:
        self._sessoes = self._ler_sessoes()
        agora = int(self._agora())
        ttl = _inteiro("N8GROKER_SESSAO_SEG", 900)
        vivas = []
        for item in self._sessoes.values():
            if self.revogada(item.get("sid") or ""):
                continue
            if agora - int(item.get("visto_em") or 0) > ttl:
                continue
            vivas.append(dict(item))
        vivas.sort(key=lambda item: int(item.get("inicio") or 0))
        return vivas

    def revogar(self, sid: str) -> None:
        identificador = _limpo(sid, 32)
        if not identificador:
            return
        dados = self._ler_json(self.pasta / "revogadas.json")
        lista = dados.get("sids") if isinstance(dados.get("sids"), list) else []
        if identificador not in lista:
            lista.append(identificador)
        self._gravar_json(self.pasta / "revogadas.json", {"sids": lista})
        self._sessoes.pop(identificador, None)
        self._gravar_sessoes()

    def gravar_auth_sessao(
        self, *, ip: str, conta: str = "", motivo_caminho: str = "", sid: str = ""
    ) -> None:
        """Recusa de sessão no túnel. Não entra na janela de 20 e não chama o webhook."""
        codigo = _AUTH_SESSAO.get(str(motivo_caminho or ""))
        if not codigo:
            return
        try:
            agora = self._agora()
            linha = {
                "quando": int(agora),
                "hora": datetime.fromtimestamp(int(agora), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ip": recortar_segredo(ip, 64) or "127.0.0.1",
                "conta": recortar_segredo(conta, 64),
                "passo": "auth.sessao",
                "resultado": "recusado",
                "motivo": codigo,
            }
            identificador = sid_de({"sid": sid})
            if identificador:
                linha["sid"] = identificador
            gravar_jsonl(self.pasta / "auth.jsonl", linha)
        except Exception:
            return

    def avisar(self, *, ip, conta, regra, detalhe, sid="", origem="", app="") -> None:
        """Alerta que não muda o veredito. O webhook continua o GET sem token."""
        self._alertar(
            {
                "ip": ip,
                "sid": sid,
                "conta": conta,
                "origem": origem,
                "app": app,
            },
            regra,
            detalhe,
        )

    def revogada(self, sid: str) -> bool:
        identificador = _limpo(sid, 32)
        if not identificador:
            return False
        dados = self._ler_json(self.pasta / "revogadas.json")
        lista = dados.get("sids") if isinstance(dados.get("sids"), list) else []
        return identificador in lista

    def legiveis(self, ip: str = "", sid: str = "") -> list[str]:
        eventos = [
            item
            for item in self._ler_eventos()
            if (not ip or item.get("ip") == ip) and (not sid or item.get("sid") == sid)
        ]
        grupos: dict[str, list] = {}
        for item in eventos:
            grupos.setdefault(item.get("ip") or "", []).append(item)
        linhas = []
        for endereco in sorted(grupos):
            atual: list[dict] = []
            for item in grupos[endereco]:
                atual.append(item)
                if item.get("resultado") in {"LIBERADO", "BLOQUEIO"}:
                    linhas.append(self._frase(endereco, atual))
                    atual = []
            if atual:
                linhas.append(self._frase(endereco, atual))
        return linhas

    def alertas(self) -> list[dict]:
        vistos = set(self._ler_json(self.pasta / "vistos.json").get("ids") or [])
        saida = []
        for item in self._ler_arquivo(self.pasta / "alertas.jsonl"):
            copia = dict(item)
            copia["visto"] = copia.get("id") in vistos
            saida.append(copia)
        return saida

    def marcar_visto(self, alerta_id: str) -> None:
        identificador = _limpo(alerta_id, 32)
        if not identificador:
            return
        dados = self._ler_json(self.pasta / "vistos.json")
        lista = dados.get("ids") if isinstance(dados.get("ids"), list) else []
        if identificador not in lista:
            lista.append(identificador)
        self._gravar_json(self.pasta / "vistos.json", {"ids": lista})

    def _frase(self, ip: str, eventos: list[dict]) -> str:
        partes = [ip]
        for item in eventos:
            if item.get("passo"):
                partes.append(item["passo"])
        ultimo = eventos[-1]
        if ultimo.get("resultado") == "BLOQUEIO":
            motivo = ultimo.get("motivo") or "negado"
            partes.append(f"BLOQUEIO ({motivo})")
        elif ultimo.get("resultado") == "LIBERADO":
            partes.append("LIBERADO")
        elif ultimo.get("resultado") == "RECUSADO" and ultimo.get("motivo"):
            partes.append(f"RECUSADO ({ultimo['motivo']})")
        elif ultimo.get("resultado"):
            partes.append(ultimo["resultado"])
        return " > ".join(partes)

    def _avaliar(self, linha: dict) -> None:
        self._trocas(linha)
        self._horario(linha)
        self._bloqueios(linha)
        self._pulou(linha)

    def _pulou(self, linha: dict) -> None:
        if linha.get("resultado") != "LIBERADO":
            return
        sid = linha.get("sid") or ""
        passos = [item.get("passo") for item in self._eventos if item.get("sid") == sid]
        if "/painel" in passos and "Porteiro ok" in passos:
            return
        self._alertar(linha, "pulou_etapas", "O acesso liberado não passou por /painel e pelo Porteiro.")

    def _trocas(self, linha: dict) -> None:
        sid = linha.get("sid") or ""
        app = linha.get("app") or ""
        if not sid or not app or linha.get("resultado") != "LIBERADO":
            return
        janela = _inteiro("N8GROKER_ALERTA_TROCAS_SEG", 60)
        limite = _inteiro("N8GROKER_ALERTA_TROCAS", 4)
        agora = int(linha.get("quando") or 0)
        hist = [(quando, nome) for quando, nome in self._apps.get(sid, []) if quando >= agora - janela]
        mudancas = 0
        anterior = ""
        for _quando, nome in hist:
            if nome != anterior:
                if anterior:
                    mudancas += 1
                anterior = nome
        if mudancas >= limite:
            self._alertar(linha, "trocas_app", f"{mudancas} trocas de app na janela.")

    def _horario(self, linha: dict) -> None:
        if linha.get("resultado") != "LIBERADO" and linha.get("passo") != "login ok":
            return
        conta = linha.get("conta") or ""
        faixa = self._faixa(conta)
        if faixa is None:
            return
        hora = datetime.fromtimestamp(int(linha.get("quando") or 0), timezone.utc).hour
        if _dentro(hora, faixa[0], faixa[1]):
            return
        self._alertar(linha, "horario", f"Hora {hora} UTC fora de {faixa[0]}-{faixa[1]}.")

    def _bloqueios(self, linha: dict) -> None:
        if linha.get("resultado") != "LIBERADO":
            return
        limite = _inteiro("N8GROKER_ALERTA_BLOQUEIOS", 3)
        seq = self._resultados.get(linha.get("ip") or "", [])
        conta = 0
        for item in reversed(seq[:-1]):
            if item == "LIBERADO":
                break
            if item != "BLOQUEIO":
                continue
            conta += 1
        if conta >= limite:
            self._alertar(linha, "bloqueios_antes", f"{conta} bloqueios seguidos de um acesso liberado.")

    def _dois_ips(self, atual: dict) -> None:
        conta = atual.get("conta") or ""
        if not conta:
            return
        agora = int(self._agora())
        ttl = _inteiro("N8GROKER_SESSAO_SEG", 900)
        for outra in self._sessoes.values():
            if outra.get("sid") == atual.get("sid"):
                continue
            if outra.get("conta") != conta or outra.get("ip") == atual.get("ip"):
                continue
            if agora - int(outra.get("visto_em") or 0) > ttl:
                continue
            self._alertar(
                {
                    "ip": atual.get("ip") or "",
                    "sid": atual.get("sid") or "",
                    "conta": conta,
                    "origem": atual.get("origem") or "",
                    "app": atual.get("app") or "",
                },
                "dois_ips",
                f"A conta também está em {outra.get('ip')}.",
            )
            return

    def _alertar(self, linha: dict, regra: str, detalhe: str) -> None:
        regra_txt = str(regra or "")
        if "\n" in regra_txt or "\r" in regra_txt or _JWT.search(regra_txt):
            regra_txt = "alerta"
        else:
            regra_txt = regra_txt[:32]
        ip = recortar_segredo(linha.get("ip") or "", 64)
        conta = recortar_segredo(linha.get("conta") or "", 64)
        sid = recortar_segredo(linha.get("sid") or "", 32)
        origem = recortar_segredo(linha.get("origem") or "", 80)
        app = recortar_segredo(linha.get("app") or "", 32)
        chave = (regra_txt, conta, ip, sid)
        agora = int(self._agora())
        janela = _inteiro("N8GROKER_ALERTA_JANELA_SEG", 300)
        if agora - self._alertou.get(chave, 0) < janela:
            return
        self._alertou[chave] = agora
        alerta = {
            "id": f"{regra_txt[:12]}{agora}",
            "hora": self._hora(),
            "regra": regra_txt,
            "ip": ip,
            "conta": conta,
            "sid": sid,
            "origem": origem,
            "app": app,
            "detalhe": _limpo(detalhe, 120),
        }
        if not alerta["detalhe"]:
            alerta["detalhe"] = regra_txt or "alerta"
        self.pasta.mkdir(parents=True, exist_ok=True)
        with (self.pasta / "alertas.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(alerta, ensure_ascii=False) + "\n")
        self._auditar(alerta)
        self._webhook(alerta)

    def _faixa(self, conta: str):
        dados = self._ler_json(self.pasta / "horarios.json")
        item = dados.get(conta) if isinstance(dados.get(conta), dict) else None
        if not item:
            return None
        try:
            inicio = int(item.get("inicio"))
            fim = int(item.get("fim"))
        except (TypeError, ValueError):
            return None
        if not (0 <= inicio <= 23 and 0 <= fim <= 24):
            return None
        return inicio, fim

    def _auditar(self, alerta: dict) -> None:
        caminho = os.environ.get("N8GROKER_AUDIT_ARQUIVO", "").strip()
        if not caminho or "\n" in caminho or "\r" in caminho:
            return
        registro = {
            "hora": alerta["hora"],
            "usuario": alerta["conta"] or "scout",
            "ip": alerta["ip"],
            "acao": "alerta_trilha",
            "alvo": alerta["regra"],
            "resultado": "alerta",
        }
        try:
            with open(caminho, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(registro, ensure_ascii=False) + "\n")
        except OSError:
            return

    def _webhook(self, alerta: dict) -> None:
        url = url_do_alerta()
        if not url or not _webhook_local(url):
            return
        consulta = urlencode(
            {
                "regra": alerta["regra"],
                "ip": alerta["ip"],
                "conta": alerta["conta"],
                "sid": alerta["sid"],
                "origem": alerta["origem"],
                "app": alerta["app"],
                "detalhe": alerta["detalhe"],
                "hora": alerta["hora"],
            }
        )
        destino = url + ("&" if "?" in url else "?") + consulta
        if self.transport is not None:
            self.transport(destino)
            return
        try:
            urllib.request.urlopen(destino, timeout=2)
        except (OSError, ValueError):
            return

    def _podar(self) -> None:
        if not self.arquivo.is_file():
            return
        dias = _inteiro("N8GROKER_TRILHA_DIAS", 7)
        limite = int(self._agora()) - dias * 86400
        linhas = []
        mudou = False
        for item, bruto in self._linhas(self.arquivo):
            if int(item.get("quando") or 0) >= limite:
                linhas.append(bruto)
            else:
                mudou = True
        if mudou:
            self.arquivo.write_text("".join(linha + "\n" for linha in linhas), encoding="utf-8")

    def _girar(self) -> None:
        girar_jsonl(self.arquivo)

    def _ler_eventos(self) -> list[dict]:
        dias = _inteiro("N8GROKER_TRILHA_DIAS", 7)
        limite = int(self._agora()) - dias * 86400
        copias = _inteiro("N8GROKER_TRILHA_COPIAS", 3)
        # Só trilha.jsonl e as cópias numeradas. auth.jsonl não entra nesta lista.
        nomes = [self.arquivo.with_name(f"trilha.jsonl.{indice}") for indice in range(copias, 0, -1)]
        nomes.append(self.arquivo)
        eventos = []
        for nome in nomes:
            for item in self._ler_arquivo(nome):
                if int(item.get("quando") or 0) >= limite:
                    eventos.append(item)
        eventos.sort(key=lambda item: int(item.get("quando") or 0))
        return eventos

    def _ler_arquivo(self, caminho: Path) -> list[dict]:
        saida = []
        for item, _bruto in self._linhas(caminho):
            saida.append(item)
        return saida

    def _linhas(self, caminho: Path):
        if not caminho.is_file():
            return
        try:
            texto = caminho.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return
        for bruto in texto.splitlines():
            if not bruto.strip():
                continue
            try:
                item = json.loads(bruto)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                yield item, bruto

    def _ler_sessoes(self) -> dict:
        dados = self._ler_json(self.pasta / "sessoes.json")
        itens = dados.get("sessoes") if isinstance(dados.get("sessoes"), list) else []
        return {item["sid"]: item for item in itens if isinstance(item, dict) and item.get("sid")}

    def _gravar_sessoes(self) -> None:
        self._gravar_json(self.pasta / "sessoes.json", {"sessoes": list(self._sessoes.values())})

    def _ler_json(self, caminho: Path) -> dict:
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return dados if isinstance(dados, dict) else {}

    def _gravar_json(self, caminho: Path, dados: dict) -> None:
        gravar_bytes(caminho, (json.dumps(dados, ensure_ascii=False) + "\n").encode("utf-8"))


def _dentro(hora: int, inicio: int, fim: int) -> bool:
    if inicio == fim:
        return True
    if inicio < fim:
        return inicio <= hora < fim
    return hora >= inicio or hora < fim
