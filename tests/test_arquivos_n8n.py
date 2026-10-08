"""Volume Arquivos-n8n, allowlist do n8n e reset que preserva a pasta."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _texto(relativo: str) -> str:
    return (ROOT / relativo).read_text(encoding="utf-8-sig")


def test_compose_monta_arquivos_n8n_e_restringe_o_disco():
    compose = _texto("n8n/docker-compose.yml")
    assert "../Arquivos-n8n:/home/node/Arquivos-n8n" in compose
    assert "N8N_RESTRICT_FILE_ACCESS_TO=/home/node/Arquivos-n8n" in compose
    assert "N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES=true" in compose
    assert "./n8n/data:/home/node/.n8n" in compose
    assert "./n8n/storage:/home/node/.n8n-files" not in compose
    assert "NODES_EXCLUDE=${NODES_EXCLUDE:-" in compose
    assert "n8n-nodes-base.readWriteFile" in compose
    assert (ROOT / "Arquivos-n8n" / ".gitkeep").is_file()
    assert (ROOT / "Arquivos-n8n" / "README.md").is_file()
    assert not (ROOT / "n8n" / "data" / ".gitkeep").exists()


def test_gitignore_ignora_o_conteudo_e_os_segredos_da_v0():
    texto = _texto(".gitignore")
    for entrada in (
        ".env.*",
        "!.env.example",
        "*.key",
        "*.pem",
        "id_rsa*",
        "*.db",
        "**/trilha/*.jsonl",
        "auth.jsonl",
        "audit.jsonl",
        "n8n/data/",
        "n8n/n8n/storage/",
        "Arquivos-n8n/*",
        "!Arquivos-n8n/.gitkeep",
        "!Arquivos-n8n/README.md",
    ):
        assert entrada in texto
    linhas = [linha.strip() for linha in texto.splitlines()]
    assert "n8n/data/*" not in linhas
    assert "!n8n/data/.gitkeep" not in linhas


def test_factory_reset_preserva_arquivos_n8n():
    texto = _texto("factory_reset.ps1")
    assert "Arquivos-n8n preservada" in texto
    assert "o reset nao apaga" in texto
    for linha in texto.splitlines():
        if "Arquivos-n8n" not in linha:
            continue
        assert "Remove-" not in linha
        assert "Remove-TreeContents" not in linha
    assert 'Join-Path $Root "n8n\\n8n\\data"' in texto or 'n8n\\n8n\\data' in texto
    assert "Ensure-GitKeep (Join-Path $Root \"Arquivos-n8n\")" not in texto


def test_setup_e_orquestrador_migram_sem_sobrescrever():
    for relativo in ("iniciar_servicos.ps1", "setup_projeto.ps1"):
        texto = _texto(relativo)
        assert "function Ensure-ArquivosN8n" in texto
        assert "n8n\\n8n\\storage" in texto
        assert ".migrado-para-Arquivos-n8n" in texto
        assert "Ja existe em Arquivos-n8n; nao movido" in texto
        corpo = texto.split("function Ensure-ArquivosN8n", 1)[1].split("function ", 1)[0]
        assert "Move-Item" in corpo
        assert "if ($pending.Count -eq 0)" in corpo
    orquestrador = _texto("iniciar_servicos.ps1")
    assert orquestrador.count("Ensure-ArquivosN8n") >= 4
    setup = _texto("setup_projeto.ps1")
    assert 'ArgumentList "`"$PathEnv`""' in setup


def _extrair_funcao(texto: str, nome: str) -> str:
    marca = f"function {nome}"
    inicio = texto.index(marca)
    abre = texto.index("{", inicio)
    profundidade = 0
    for indice in range(abre, len(texto)):
        if texto[indice] == "{":
            profundidade += 1
        elif texto[indice] == "}":
            profundidade -= 1
            if profundidade == 0:
                return texto[inicio : indice + 1]
    raise AssertionError(nome)


def test_gitkeep_versionado_tem_zero_bytes():
    atributos = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert ".gitkeep -text -eol" in atributos
    for relativo in (
        "n8n/n8n/data/.gitkeep",
        "n8n/storage/Porteiro/.gitkeep",
        "Arquivos-n8n/.gitkeep",
    ):
        assert (ROOT / relativo).read_bytes() == b""
    gravacao = "[System.IO.File]::WriteAllBytes($keep, [byte[]]@())"
    assert gravacao in _texto("factory_reset.ps1")
    assert gravacao in _texto("setup_projeto.ps1")


def test_gitkeep_recriado_fica_vazio_e_o_git_status_limpo(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    repo = tmp_path / "repo"
    (repo / "n8n" / "n8n" / "data").mkdir(parents=True)
    (repo / "n8n" / "storage" / "Porteiro").mkdir(parents=True)
    (repo / "Arquivos-n8n").mkdir()
    (repo / ".gitattributes").write_text(
        "* text=auto eol=lf\n*.ps1 text eol=crlf\n.gitkeep -text -eol\n",
        encoding="utf-8",
    )
    marcadores = [
        repo / "n8n" / "n8n" / "data" / ".gitkeep",
        repo / "n8n" / "storage" / "Porteiro" / ".gitkeep",
        repo / "Arquivos-n8n" / ".gitkeep",
    ]
    for marcador in marcadores:
        marcador.write_bytes(b"")

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=check,
        )

    git("init")
    git("config", "user.email", "teste@example.com")
    git("config", "user.name", "teste")
    git("add", "-A")
    git("commit", "-m", "base")
    for marcador in marcadores:
        marcador.write_bytes(b"\xef\xbb\xbf\r\n")
    sujo = git("status", "--porcelain")
    assert sujo.stdout.strip()
    funcao = _extrair_funcao(_texto("factory_reset.ps1"), "Ensure-GitKeep")
    script = tmp_path / "recriar.ps1"
    chamadas = "\n".join(
        "Ensure-GitKeep '" + str(marcador.parent).replace("'", "''") + "'" for marcador in marcadores
    )
    script.write_text(
        "function Write-Ok { param($Message) }\n" + funcao + "\n" + chamadas + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for marcador in marcadores:
        assert marcador.read_bytes() == b""
    limpo = git("status", "--porcelain")
    assert limpo.stdout.strip() == ""


def test_varredura_de_log_nao_apaga_arquivos_n8n(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    raiz = tmp_path / "proj"
    (raiz / "Arquivos-n8n" / "sub").mkdir(parents=True)
    (raiz / "Arquivos-n8n" / "nota.log").write_text("usuario", encoding="utf-8")
    (raiz / "Arquivos-n8n" / "sub" / "outro.log").write_text("usuario", encoding="utf-8")
    (raiz / "Arquivos-n8n-extra").mkdir()
    (raiz / "Arquivos-n8n-extra" / "vizinho.log").write_text("fora", encoding="utf-8")
    (raiz / "n8n" / "storage" / "Porteiro").mkdir(parents=True)
    (raiz / "n8n" / "storage" / "Porteiro" / "registro_portaria.log").write_text(
        "porteiro", encoding="utf-8"
    )
    funcao = _extrair_funcao(_texto("factory_reset.ps1"), "Remove-LogsVolateis")
    script = tmp_path / "logs.ps1"
    script.write_text(
        "$Root = '" + str(raiz).replace("'", "''") + "'\n" + funcao + "\nRemove-LogsVolateis\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (raiz / "Arquivos-n8n" / "nota.log").read_text(encoding="utf-8") == "usuario"
    assert (raiz / "Arquivos-n8n" / "sub" / "outro.log").read_text(encoding="utf-8") == "usuario"
    assert not (raiz / "Arquivos-n8n-extra" / "vizinho.log").exists()
    assert not (raiz / "n8n" / "storage" / "Porteiro" / "registro_portaria.log").exists()


def test_migracao_move_uma_vez_e_nao_sobrescreve(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh nao esta instalado neste ambiente")
    funcao = _extrair_funcao(_texto("iniciar_servicos.ps1"), "Ensure-ArquivosN8n")
    script = tmp_path / "migrar.ps1"
    script.write_text(funcao + "\nEnsure-ArquivosN8n\n", encoding="utf-8")
    legado = tmp_path / "n8n" / "n8n" / "storage"
    legado.mkdir(parents=True)
    (legado / "relatório final.txt").write_text("um", encoding="utf-8")
    (legado / "novo.csv").write_text("csv", encoding="utf-8")
    destino = tmp_path / "Arquivos-n8n"
    destino.mkdir()
    (destino / "relatório final.txt").write_text("fica", encoding="utf-8")

    primeiro = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert primeiro.returncode == 0, primeiro.stdout + primeiro.stderr
    assert (destino / "novo.csv").read_text(encoding="utf-8") == "csv"
    assert (destino / "relatório final.txt").read_text(encoding="utf-8") == "fica"
    assert (legado / "relatório final.txt").is_file()
    assert not (legado / ".migrado-para-Arquivos-n8n").exists()

    (legado / "relatório final.txt").unlink()
    segundo = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert segundo.returncode == 0, segundo.stdout + segundo.stderr
    marcador = legado / ".migrado-para-Arquivos-n8n"
    assert marcador.is_file()
    (legado / "depois.txt").write_text("nao", encoding="utf-8")
    terceiro = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert terceiro.returncode == 0, terceiro.stdout + terceiro.stderr
    assert (legado / "depois.txt").is_file()
    assert not (destino / "depois.txt").exists()
