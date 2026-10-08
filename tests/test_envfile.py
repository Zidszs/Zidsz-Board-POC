import json

import pytest

from control_plane.envfile import (
    ensure_env,
    is_marker,
    llm_configured,
    n8n_overwrite,
    ngrok_tunnel_host,
    ngrok_tunnel_target,
    parse_env,
    read_env_file,
    read_text,
)


EXAMPLE = """
# comentario
NGROK_AUTHTOKEN=seu_token_do_ngrok_aqui
N8N_ENCRYPTION_KEY=__GENERATE_HEX_32__
USE_SCOUT=1
SCOUT_PUBLIC_PORT=4050
ENCRYPTION_KEY=__GENERATE_HEX_32__
LANGFUSE_DB_PASSWORD=__GENERATE_ALNUM_32__
LANGFUSE_PUBLIC_KEY=__GENERATE_PK_LF__
LANGFUSE_SECRET_KEY=__GENERATE_SK_LF__
LITELLM_MASTER_KEY=__GENERATE_LITELLM_MASTER__
N8N_CREDENTIALS_OVERWRITE_DATA=__GENERATE_N8N_OVERWRITE__
"""


def test_ensure_env_creates_secrets_and_keeps_ngrok_placeholder(tmp_path):
    example = tmp_path / ".env.example"
    dest = tmp_path / ".env"
    example.write_text(EXAMPLE, encoding="utf-8")
    created = ensure_env(example, dest)
    assert created.action == "created"
    values = parse_env(dest.read_text(encoding="utf-8"))
    assert values["NGROK_AUTHTOKEN"] == "seu_token_do_ngrok_aqui"
    assert not is_marker(values["N8N_ENCRYPTION_KEY"])
    assert len(values["N8N_ENCRYPTION_KEY"]) == 64
    assert values["LANGFUSE_PUBLIC_KEY"].startswith("pk-lf-")
    assert values["LANGFUSE_SECRET_KEY"].startswith("sk-lf-")
    assert values["LITELLM_MASTER_KEY"].startswith("sk-")
    payload = json.loads(values["N8N_CREDENTIALS_OVERWRITE_DATA"])
    assert payload["openAiApi"]["url"] == "http://litellm:4000/v1"
    assert payload["openAiApi"]["apiKey"] == values["LITELLM_MASTER_KEY"]
    assert "'" not in values["N8N_CREDENTIALS_OVERWRITE_DATA"]
    assert "ollama" not in dest.read_text(encoding="utf-8").lower()
    again = ensure_env(example, dest)
    assert again.action == "unchanged"
    assert parse_env(dest.read_text(encoding="utf-8"))["ENCRYPTION_KEY"] == values["ENCRYPTION_KEY"]


def test_ensure_env_does_not_rotate_existing_secrets(tmp_path):
    example = tmp_path / ".env.example"
    dest = tmp_path / ".env"
    example.write_text(EXAMPLE, encoding="utf-8")
    dest.write_text(
        "N8N_ENCRYPTION_KEY=ja-existe\nLITELLM_MASTER_KEY=sk-manualkey1\nNGROK_AUTHTOKEN=token-real\n",
        encoding="utf-8",
    )
    result = ensure_env(example, dest)
    values = parse_env(dest.read_text(encoding="utf-8"))
    assert values["N8N_ENCRYPTION_KEY"] == "ja-existe"
    assert values["LITELLM_MASTER_KEY"] == "sk-manualkey1"
    assert values["NGROK_AUTHTOKEN"] == "token-real"
    assert json.loads(values["N8N_CREDENTIALS_OVERWRITE_DATA"])["openAiApi"]["apiKey"] == "sk-manualkey1"
    assert "LANGFUSE_PUBLIC_KEY" in result.filled_keys
    assert "N8N_ENCRYPTION_KEY" not in result.filled_keys


def test_ensure_env_replaces_only_markers(tmp_path):
    example = tmp_path / ".env.example"
    dest = tmp_path / ".env"
    example.write_text(EXAMPLE, encoding="utf-8")
    dest.write_text("N8N_ENCRYPTION_KEY=__GENERATE_HEX_32__\nPORTEIRO_TOKEN=fixo\n", encoding="utf-8")
    ensure_env(example, dest)
    values = parse_env(dest.read_text(encoding="utf-8"))
    assert values["PORTEIRO_TOKEN"] == "fixo"
    assert not is_marker(values["N8N_ENCRYPTION_KEY"])


def test_ngrok_target_follows_scout_flag():
    assert ngrok_tunnel_target({"USE_SCOUT": "1", "SCOUT_PUBLIC_PORT": "4050"}) == "4050"
    assert ngrok_tunnel_host({"USE_SCOUT": "1"}) == "scout-backend"
    assert ngrok_tunnel_target({"USE_SCOUT": "0"}) == "5677"
    assert ngrok_tunnel_target({"USE_SCOUT": "1", "SCOUT_PUBLIC_PORT": "8501"}) == "4050"
    assert ngrok_tunnel_host({"USE_SCOUT": "0"}) == "host.docker.internal"
    with pytest.raises(ValueError):
        ngrok_tunnel_target({"USE_SCOUT": "1", "SCOUT_PUBLIC_PORT": "abc"})
    assert llm_configured(parse_env(EXAMPLE)) is False


def test_leitores_aceitam_bom_e_a_primeira_chave(tmp_path):
    dest = tmp_path / ".env"
    dest.write_bytes("\ufeffNGROK_AUTHTOKEN=abc\nLITELLM_MASTER_KEY=sk-abc$xyz\n".encode("utf-8"))
    assert read_text(dest).startswith("NGROK_AUTHTOKEN=")
    values = read_env_file(dest)
    assert list(values)[0] == "NGROK_AUTHTOKEN"
    assert values["NGROK_AUTHTOKEN"] == "abc"
    assert values["LITELLM_MASTER_KEY"] == "sk-abc$xyz"
    assert "\ufeff" not in "".join(values)

    example = tmp_path / ".env.example"
    example.write_text(
        "NGROK_AUTHTOKEN=seu_token_do_ngrok_aqui\nN8N_ENCRYPTION_KEY=__GENERATE_HEX_32__\n",
        encoding="utf-8",
    )
    marcado = tmp_path / "com-bom.env"
    marcado.write_bytes(
        b"\xef\xbb\xbfNGROK_AUTHTOKEN=token-real\nN8N_ENCRYPTION_KEY=__GENERATE_HEX_32__\n"
    )
    ensure_env(example, marcado)
    cru = marcado.read_bytes()
    assert not cru.startswith(b"\xef\xbb\xbf")
    gravado = parse_env(cru.decode("utf-8"))
    assert gravado["NGROK_AUTHTOKEN"] == "token-real"
    assert not is_marker(gravado["N8N_ENCRYPTION_KEY"])


def test_n8n_overwrite_rejects_odd_keys():
    with pytest.raises(ValueError):
        n8n_overwrite("sk-has'quote")
    raw = n8n_overwrite("sk-abc123")
    assert "http://litellm:4000/v1" in raw
    assert "ollama" not in raw


def test_stacks_boot_nao_vira_segredo(tmp_path):
    example = tmp_path / ".env.example"
    dest = tmp_path / ".env"
    example.write_text(
        "STACKS_BOOT=\nN8N_ENCRYPTION_KEY=__GENERATE_HEX_32__\nNGROK_AUTHTOKEN=seu_token_do_ngrok_aqui\n",
        encoding="utf-8",
    )
    created = ensure_env(example, dest)
    values = parse_env(dest.read_text(encoding="utf-8"))
    assert values["STACKS_BOOT"] == ""
    assert "STACKS_BOOT" not in created.filled_keys
    assert not is_marker(values["N8N_ENCRYPTION_KEY"])
    assert "STACKS_BOOT" not in envfile_generators()

    marcado = tmp_path / "marcado.env"
    marcado.write_text(
        "STACKS_BOOT=__GENERATE_HEX_32__\nN8N_ENCRYPTION_KEY=ja-existe\n",
        encoding="utf-8",
    )
    antes = marcado.read_text(encoding="utf-8")
    de_novo = ensure_env(example, marcado)
    assert de_novo.action == "unchanged"
    assert "STACKS_BOOT" not in de_novo.filled_keys
    assert marcado.read_text(encoding="utf-8") == antes

    marcado.write_text(
        "STACKS_BOOT=n8n,llm\nN8N_ENCRYPTION_KEY=ja-existe\n",
        encoding="utf-8",
    )
    outra = ensure_env(example, marcado)
    assert outra.action == "unchanged"
    assert parse_env(marcado.read_text(encoding="utf-8"))["STACKS_BOOT"] == "n8n,llm"
    assert parse_env(marcado.read_text(encoding="utf-8"))["N8N_ENCRYPTION_KEY"] == "ja-existe"


def envfile_generators():
    from control_plane.envfile import _generators

    return _generators()
