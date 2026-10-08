import os
import stat
from pathlib import Path

from control_plane.porteiro_token import (
    ARQUIVO_N8N,
    ARQUIVO_N8N_ENV,
    ARQUIVO_PAINEL,
    garantir,
    token_n8n,
    token_painel,
)


def test_tokens_nascem_separados_e_nao_rotacionam(tmp_path):
    primeiro = garantir(tmp_path)
    assert primeiro[ARQUIVO_PAINEL] != primeiro[ARQUIVO_N8N]
    assert len(primeiro[ARQUIVO_PAINEL]) >= 16
    segundo = garantir(tmp_path)
    assert segundo == primeiro
    assert token_painel(tmp_path) == primeiro[ARQUIVO_PAINEL]
    assert token_n8n(tmp_path) == primeiro[ARQUIVO_N8N]
    env = (tmp_path / ".n8groker" / ARQUIVO_N8N_ENV).read_text(encoding="utf-8")
    assert env == "PORTEIRO_N8N_TOKEN=" + primeiro[ARQUIVO_N8N] + "\n"
    assert primeiro[ARQUIVO_PAINEL] not in env
    for nome in (ARQUIVO_PAINEL, ARQUIVO_N8N, ARQUIVO_N8N_ENV):
        modo = (tmp_path / ".n8groker" / nome).stat().st_mode
        assert stat.S_IMODE(modo) & 0o077 == 0
        assert os.stat(tmp_path / ".n8groker" / nome).st_mode & 0o077 == 0


def test_n8n_recebe_so_o_token_do_workflow():
    raiz = Path(__file__).resolve().parents[1]
    compose = (raiz / "n8n" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "env_file:" in compose
    assert "../.n8groker/porteiro-n8n.env" in compose
    fluxo = (raiz / "Workflows_para_Autenticação" / "Aprovacao de Acesso (Novo).json").read_text(encoding="utf-8")
    assert "={{ $env.PORTEIRO_N8N_TOKEN }}" in fluxo
    assert "PORTEIRO_TOKEN" not in (raiz / ".env.example").read_text(encoding="utf-8")
