from pathlib import Path

import pytest

from control_plane.backup import (
    BackupError,
    backups_dir,
    build_backup_plan,
    build_restore_plan,
)

ROOT = Path(__file__).resolve().parents[1]
STAMP = "20261001T150000Z"
SECRET = "senha-que-nao-pode-vazar"


def test_pg_dump_nao_coloca_senha_no_comando(tmp_path):
    env = {
        "LANGFUSE_DB_USER": "langfuse",
        "LANGFUSE_DB_NAME": "langfuse",
        "LANGFUSE_DB_PASSWORD": SECRET,
    }
    plan = build_backup_plan("langfuse-postgres", tmp_path, env, docker="docker", stamp=STAMP)
    assert len(plan) == 1
    cmd = plan[0]
    assert cmd.argv == (
        "docker",
        "exec",
        "-i",
        "langfuse-postgres",
        "pg_dump",
        "-U",
        "langfuse",
        "--dbname",
        "langfuse",
        "--no-owner",
        "--format=custom",
    )
    assert cmd.capture_path.endswith(f".n8groker/backups/langfuse-postgres-{STAMP}.dump")
    assert SECRET not in " ".join(cmd.argv)
    assert SECRET not in (cmd.capture_path or "")


def test_litellm_usa_usuario_padrao_e_restore_para_antes(tmp_path):
    plan = build_backup_plan("litellm-db", tmp_path, {}, docker="docker", stamp=STAMP)
    assert plan[0].argv[3] == "litellm-db"
    assert "litellm" in plan[0].argv
    dump = backups_dir(tmp_path) / f"litellm-db-{STAMP}.dump"
    dump.write_bytes(b"PGDMP")
    with pytest.raises(BackupError, match="confirme"):
        build_restore_plan("litellm-db", tmp_path, {}, source_name=dump.name, confirmed=False, docker="docker")
    restore = build_restore_plan(
        "litellm-db",
        tmp_path,
        {"LITELLM_DB_PASSWORD": SECRET},
        source_name=dump.name,
        confirmed=True,
        docker="docker",
    )
    assert [cmd.argv[1] for cmd in restore] == ["stop", "exec", "start"]
    assert restore[0].argv == ("docker", "stop", "litellm")
    assert "pg_restore" in restore[1].argv
    assert restore[1].stdin_path == str(dump.resolve())
    assert restore[0].argv.index("stop") < restore[1].argv.index("pg_restore")
    joined = " ".join(" ".join(cmd.argv) for cmd in restore)
    assert SECRET not in joined


def test_usuario_injetado_e_alvo_fora_da_lista(tmp_path):
    with pytest.raises(BackupError):
        build_backup_plan(
            "langfuse-postgres",
            tmp_path,
            {"LANGFUSE_DB_USER": "langfuse;rm"},
            docker="docker",
            stamp=STAMP,
        )
    with pytest.raises(BackupError):
        build_backup_plan("langfuse-web", tmp_path, {}, docker="docker", stamp=STAMP)


def test_n8n_copia_o_sqlite_conhecido(tmp_path):
    sqlite = tmp_path / "n8n" / "n8n" / "data" / "database.sqlite"
    sqlite.parent.mkdir(parents=True)
    sqlite.write_bytes(b"sqlite-marker")
    plan = build_backup_plan(
        "n8n",
        tmp_path,
        {"N8N_ENCRYPTION_KEY": SECRET},
        docker="docker",
        stamp=STAMP,
    )
    assert plan[0].kind == "copy"
    assert plan[0].src == str(sqlite.resolve())
    assert plan[0].dest.endswith(f"n8n-{STAMP}.sqlite")
    assert SECRET not in (plan[0].src or "")
    assert SECRET not in (plan[0].dest or "")
    source = Path(plan[0].dest)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"sqlite-marker")
    restore = build_restore_plan("n8n", tmp_path, {}, source_name=source.name, confirmed=True, docker="docker")
    assert restore[0].argv == ("docker", "stop", "n8n_app")
    assert restore[1].kind == "copy"
    assert restore[1].dest == str(sqlite.resolve())
    assert restore[2].argv == ("docker", "start", "n8n_app")


def test_clickhouse_e_minio_usam_volume_fixo_sem_senha(tmp_path):
    env = {"CLICKHOUSE_PASSWORD": SECRET, "MINIO_ROOT_PASSWORD": SECRET}
    click = build_backup_plan("clickhouse", tmp_path, env, docker="docker", stamp=STAMP)
    joined = " ".join(click[0].argv)
    assert "n8groker-llm_langfuse_clickhouse_data" in joined
    assert "docker.io/postgres:17.11" in click[0].argv
    assert "--entrypoint" in click[0].argv
    assert "tar" in click[0].argv
    assert SECRET not in joined
    mini = build_backup_plan("minio", tmp_path, env, docker="docker", stamp=STAMP)
    assert "n8groker-llm_langfuse_minio_data" in " ".join(mini[0].argv)
    name = f"clickhouse-{STAMP}.tar.gz"
    (backups_dir(tmp_path) / name).write_bytes(b"tar")
    restore = build_restore_plan("clickhouse", tmp_path, env, source_name=name, confirmed=True, docker="docker")
    assert restore[0].argv[1] == "stop"
    assert "langfuse-clickhouse" in restore[0].argv
    assert "langfuse-web" in restore[0].argv
    assert restore[1].argv[1] == "run"
    assert "-xzf" in restore[1].argv
    assert restore[2].argv[1] == "start"
    assert SECRET not in " ".join(" ".join(cmd.argv) for cmd in restore)


def test_modulo_nao_usa_shell():
    text = (ROOT / "control_plane" / "backup.py").read_text(encoding="utf-8")
    assert "shell=True" not in text
    assert "shell=False" in text
