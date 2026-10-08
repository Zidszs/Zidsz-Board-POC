"""Identidade do Porteiro: o mesmo módulo Node que o servidor carrega."""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_suite_node_da_identidade():
    node = shutil.which("node")
    assert node, "o teste da identidade precisa do node"
    proc = subprocess.run(
        [node, "--test", "Porteiro/identidade.test.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_porteiro_usa_o_modulo_e_nao_trata_docker_como_imune():
    texto = (ROOT / "Porteiro" / "porteiro.js").read_text(encoding="utf-8")
    assert "require('./identidade')" in texto
    assert "ehImune" not in texto
    assert "decidirIp" in texto or "classificar" in texto
    assert "127.0.0.1" in texto
    assert "5678" in texto
