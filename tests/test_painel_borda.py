"""O painel de borda só aceita o HMAC que o Porteiro assina."""

import hashlib
import hmac
import shutil
import subprocess
from pathlib import Path

from control_plane.edge_auth import sign_client, verify_client

ROOT = Path(__file__).resolve().parents[1]


def test_python_e_node_concordam_no_hmac(tmp_path):
    node = shutil.which("node")
    assert node
    chave = b"chave-de-teste-32-bytes-exatos!!"
    ip = "203.0.113.40"
    header = sign_client(ip, chave)
    assert verify_client(header, chave) == ip
    assert verify_client(header[:-1] + ("0" if header[-1] != "0" else "1"), chave) == ""
    script = r"""
const { conferirCliente, assinarCliente } = require("./Porteiro/hmac_cliente");
const chave = Buffer.from(process.argv[1], "hex");
const header = process.argv[2];
if (conferirCliente(header, chave) !== "203.0.113.40") process.exit(2);
const outro = assinarCliente("198.51.100.8", chave);
process.stdout.write(outro);
"""
    proc = subprocess.run(
        [node, "-e", script, chave.hex(), header],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert verify_client(proc.stdout, chave) == "198.51.100.8"
    mac = hmac.new(chave, ip.encode(), hashlib.sha256).hexdigest()
    assert header == f"{ip}|{mac}"


def test_suite_node_do_caminho_painel():
    node = shutil.which("node")
    assert node
    proc = subprocess.run(
        [node, "--test", "Porteiro/painel.test.js", "Porteiro/admin_rede.test.js", "Porteiro/token_aprovacao.test.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_aprovacao_tem_porta_local_fora_do_tunel():
    texto = (ROOT / "Porteiro" / "porteiro.js").read_text(encoding="utf-8")
    assert "adminServer.listen(PORTA_ADMIN, '127.0.0.1'" in texto
    assert "server.listen(PORTA_DO_PORTEIRO, '127.0.0.1'" in texto
    assert "server.listen(PORTA_DO_PORTEIRO, '0.0.0.0'" not in texto
    assert "ipsDoN8n" in texto
    assert "n8n-container-ip" in texto
    assert "decidirAdmin" in texto
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "http://127.0.0.1:5676" in readme
    assert "http://host.docker.internal:5677" in readme
    assert "ngrok" in readme.lower()


def test_porteiro_encaminha_painel_e_continua_n8n_na_raiz():
    texto = (ROOT / "Porteiro" / "porteiro.js").read_text(encoding="utf-8")
    assert "fazerProxyPainel" in texto
    assert "server.on('upgrade'" in texto
    assert "/n8n/vincular" in texto
    assert "port: 5678" in texto or "N8N_LOCAL_PORT || 5678" in texto
    assert "x-n8groker-client" in texto
